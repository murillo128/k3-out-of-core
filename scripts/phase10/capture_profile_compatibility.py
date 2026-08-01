#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build_prefetch_profile import build
from prefetch_common import (PHASE2_ARCHIVE_SHA256, Phase10Error, build_fingerprint, canonical_bytes, load_json,
    sha256_bytes, validate_profile, write_json)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def retained_tunings(offline: dict[str, Any]) -> list[dict[str, Any]]:
    profile_policies = {"STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY",
        "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE", "BLOCKING_HOT"}
    rejected = {"rejected_below_break_even"}
    records = [record for record in offline["shortlist"] if record["policy"] in profile_policies and
        record["disposition"] not in rejected]
    keys = set()
    result = []
    for record in sorted(records, key=lambda item: (item["artifact"], item["fold"], item["policy"],
            item["transport"], item["readiness"], item["temporal_window_tokens"], item["candidates_per_target"])):
        key = (record["artifact"], record["fold"], record["policy"], record["transport"], record["readiness"])
        if key in keys:
            raise Phase10Error("offline shortlist contains duplicate frozen tuning")
        keys.add(key)
        result.append(record)
    if not result:
        raise Phase10Error("offline shortlist retained no profiles")
    return result


def frozen_tuning_digest(offline: dict[str, Any], tuning: dict[str, Any]) -> str:
    validation_record = {key: value for key, value in tuning.items() if key != "held_out_test"}
    return sha256_bytes(canonical_bytes({"matrix_sha256": offline["matrix_sha256"],
        "validation_record": validation_record}))


def build_profile(
        archive: Path,
        storage_map: Path,
        costs: Path,
        artifact: str,
        fold: int,
        tuning: dict[str, Any],
        tuning_digest: str) -> dict[str, Any]:
    arguments = SimpleNamespace(archive=str(archive), storage_map=str(storage_map), artifact=artifact, fold=fold,
        costs=str(costs), transport=tuning["transport"], readiness=tuning["readiness"], policy=tuning["policy"],
        candidates=tuning["candidates_per_target"], temporal_window=tuning["temporal_window_tokens"], seed_slots=14,
        tuning_digest=tuning_digest)
    first = build(arguments)
    second = build(arguments)
    if canonical_bytes(first) != canonical_bytes(second):
        raise Phase10Error("profile build was not byte-identical")
    validate_profile(first)
    return first


def run_validation(probe: Path, profile: Path, model: Path, identity: str) -> dict[str, Any]:
    command = [str(probe), "--profile", str(profile), "--model", str(model), "--identity", identity,
        "--validate-only"]
    completed = subprocess.run(command, check=False, capture_output=True)
    record = {"command": command, "exit_code": completed.returncode,
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest()}
    if completed.returncode == 0:
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise Phase10Error(f"validation probe emitted invalid JSON: {error}") from error
        project_head, separator, nested_head = identity.partition(":")
        if not separator or document != {"schema_version": "phase10-profile-validation-v1",
                "project_head": project_head, "nested_head": nested_head,
                "profile_sha256": sha256_file(profile), "profile_parse_ns": document.get("profile_parse_ns"),
                "model_profile_load_ns": document.get("model_profile_load_ns")}:
            raise Phase10Error("validation probe emitted inconsistent identity")
        if not isinstance(document["profile_parse_ns"], int) or document["profile_parse_ns"] < 0 or \
                not isinstance(document["model_profile_load_ns"], int) or document["model_profile_load_ns"] < 0:
            raise Phase10Error("validation probe emitted invalid timing")
        record.update({"profile_sha256": document["profile_sha256"],
            "profile_parse_ns": document["profile_parse_ns"],
            "model_profile_load_ns": document["model_profile_load_ns"]})
    return record


def run_native_profile_validation(native: Path, profile: Path, directory: Path) -> dict[str, Any]:
    profile_document = load_json(profile)
    minimum_capacity = max(item["physical_bytes"] for item in profile_document["target"]["expert_bytes"])
    request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile),
        "initial_resident": [],
        "policy": "OFF", "cache_mode": "COLD_CACHE", "miss_policy": "PROMOTE_AND_GPU",
        "transport": profile_document["selection"]["transport"],
        "readiness": profile_document["selection"]["readiness"], "temporal_window_tokens": 0,
        "candidates_per_target": 0, "request_ordinal": 1, "events": [], "completion_order": [],
        "ready_before_deadline": [],
        "limits": {"cold_capacity_bytes": minimum_capacity, "hot_capacity_slots": 1, "max_speculative_flights": 0,
            "max_speculative_storage_bytes_in_flight": 0, "max_speculative_h2d_bytes_in_flight": 0,
            "max_speculative_storage_bytes_per_token": 0, "max_speculative_h2d_bytes_per_token": 0,
            "max_speculative_cold_slots": 0, "max_speculative_hot_slots": 0},
        "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}
    request_path = directory / "profile-validation-request.json"
    write_json(request_path, request)
    completed = subprocess.run([str(native), str(request_path)], check=False, capture_output=True)
    record = {"exit_code": completed.returncode, "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest()}
    if completed.returncode == 0:
        document = json.loads(completed.stdout)
        if document.get("profile_sha256") != sha256_file(profile) or document.get("candidate_stream") != []:
            raise Phase10Error("native profile validation output is inconsistent")
        record["profile_sha256"] = document["profile_sha256"]
    return record


def capture(args: argparse.Namespace) -> dict[str, Any]:
    archive = Path(args.archive).resolve()
    offline = load_json(args.offline_replay)
    probe = Path(args.probe).resolve()
    native = Path(args.native_replay).resolve()
    identity = f"{args.project_head}:{args.nested_head}"
    output_dir = Path(args.profile_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "f16": {"storage": Path(args.f16_storage_map).resolve(), "costs": Path(args.f16_costs).resolve(),
            "model": Path(args.f16_model).resolve()},
        "mxfp4": {"storage": Path(args.mxfp4_storage_map).resolve(), "costs": Path(args.mxfp4_costs).resolve(),
            "model": Path(args.mxfp4_model).resolve()},
    }
    for name in ("project_head", "nested_head"):
        value = getattr(args, name)
        if len(value) != 40 or any(byte not in "0123456789abcdef" for byte in value):
            raise Phase10Error(f"{name} must be a lowercase commit SHA")
    if sha256_file(archive) != PHASE2_ARCHIVE_SHA256:
        raise Phase10Error("Phase 2 archive identity mismatch")
    if offline.get("matrix_sha256") != sha256_bytes(canonical_bytes(offline.get("matrix"))):
        raise Phase10Error("offline replay matrix identity mismatch")
    if offline.get("project_head") != args.project_head or offline.get("nested_head") != args.nested_head:
        raise Phase10Error("offline replay revision mismatch")
    for inputs in artifacts.values():
        cost_document = load_json(inputs["costs"])
        if cost_document.get("project_head") != args.project_head or \
                cost_document.get("nested_head") != args.nested_head:
            raise Phase10Error("cost evidence revision mismatch")
    profiles = []
    profile_paths = []
    runtime_candidates = {}
    representative_paths = {}
    expected_targets = {artifact: build_fingerprint(load_json(inputs["storage"]))
        for artifact, inputs in artifacts.items()}
    for tuning in retained_tunings(offline):
        artifact = tuning["artifact"]
        fold = tuning["fold"]
        inputs = artifacts[artifact]
        tuning_digest = frozen_tuning_digest(offline, tuning)
        profile = build_profile(archive, inputs["storage"], inputs["costs"], artifact, fold, tuning,
            tuning_digest)
        if profile["target"] != expected_targets[artifact]:
            raise Phase10Error("profile target differs from the exact storage-map fingerprint")
        path = output_dir / f"{profile['profile_id']}.json"
        write_json(path, profile)
        profile_paths.append(path)
        runtime_key = (artifact, tuning["policy"], tuning["readiness"])
        runtime_candidates.setdefault(runtime_key, path)
        representative_paths.setdefault(artifact, path)
        profiles.append({"artifact": artifact, "fold": fold, "path": evidence_path(path),
            "sha256": sha256_file(path), "bytes": path.stat().st_size,
            "target_package_sha256": profile["target"]["package_sha256"],
            "tensor_layout_sha256": profile["target"]["tensor_layout_sha256"],
            "selection": profile["selection"], "offline_disposition": tuning["disposition"],
            "build_repeatability": "byte_identical", "storage_map_match": True})
    exact_runtime = {}
    for (artifact, policy, readiness), path in sorted(runtime_candidates.items()):
        key = f"{artifact}:{policy}:{readiness}"
        exact_runtime[key] = run_validation(probe, path, artifacts[artifact]["model"], identity)
        if exact_runtime[key]["exit_code"] != 0:
            raise Phase10Error(f"exact {key} profile was rejected by the runtime")
    native_profile_validation = []
    with tempfile.TemporaryDirectory(prefix="phase10-profile-validation-") as temporary:
        validation_root = Path(temporary)
        for path in profile_paths:
            record = run_native_profile_validation(native, path, validation_root)
            if record["exit_code"] != 0:
                raise Phase10Error(f"generated profile was rejected by the native strict loader: {path}; "
                    f"stderr_sha256={record['stderr_sha256']}")
            native_profile_validation.append({"path": evidence_path(path), **record})
    wrong_runtime = {
        "mxfp4_profile_on_f16": run_validation(
            probe, representative_paths["mxfp4"], artifacts["f16"]["model"], identity),
        "f16_profile_on_mxfp4": run_validation(
            probe, representative_paths["f16"], artifacts["mxfp4"]["model"], identity),
    }
    if any(record["exit_code"] == 0 for record in wrong_runtime.values()):
        raise Phase10Error("wrong-package runtime profile was accepted")
    return {"schema_version": "phase10-profile-compatibility-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "phase2_archive_sha256": sha256_file(archive),
        "offline_replay_sha256": sha256_file(Path(args.offline_replay)),
        "profiles": profiles, "native_profile_validation": native_profile_validation,
        "exact_runtime": exact_runtime, "wrong_package_runtime": wrong_runtime,
        "strict_integer_only_profile": True, "runtime_raw_training_input": False,
        "all_exact_profiles_accepted": True, "all_wrong_packages_rejected": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--offline-replay", required=True)
    parser.add_argument("--f16-storage-map", required=True)
    parser.add_argument("--mxfp4-storage-map", required=True)
    parser.add_argument("--f16-costs", required=True)
    parser.add_argument("--mxfp4-costs", required=True)
    parser.add_argument("--f16-model", required=True)
    parser.add_argument("--mxfp4-model", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--native-replay", required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        write_json(args.output, capture(args))
        print(Path(args.output))
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

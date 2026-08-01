#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build_prefetch_profile import build
from prefetch_common import Phase10Error, build_fingerprint, canonical_bytes, load_json, validate_profile, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def selected_tuning(offline: dict[str, Any], artifact: str, fold: int) -> dict[str, Any]:
    candidates = [record for record in offline["shortlist"] if record["artifact"] == artifact and
        record["fold"] == fold and record["policy"] == "TEMPORAL_FREQUENCY" and
        record["transport"] == "BUFFERED" and record["readiness"] == "DEVICE_READY"]
    if candidates:
        return candidates[0]
    return {"policy": "STATIC_LAYER", "temporal_window_tokens": 0, "candidates_per_target": 2,
        "transport": "BUFFERED", "readiness": "DEVICE_READY", "disposition": "compatibility_only_no_decode_validation"}


def build_profile(
        archive: Path,
        storage_map: Path,
        costs: Path,
        artifact: str,
        fold: int,
        tuning: dict[str, Any]) -> dict[str, Any]:
    arguments = SimpleNamespace(archive=str(archive), storage_map=str(storage_map), artifact=artifact, fold=fold,
        costs=str(costs), transport=tuning["transport"], readiness=tuning["readiness"], policy=tuning["policy"],
        candidates=tuning["candidates_per_target"], temporal_window=tuning["temporal_window_tokens"], seed_slots=14)
    first = build(arguments)
    second = build(arguments)
    if canonical_bytes(first) != canonical_bytes(second):
        raise Phase10Error("profile build was not byte-identical")
    validate_profile(first)
    return first


def run_probe(probe: Path, profile: Path, model: Path, identity: str, output: Path | None) -> dict[str, Any]:
    command = [str(probe), "--profile", str(profile), "--model", str(model), "--identity", identity]
    completed = subprocess.run(command, check=False, capture_output=True)
    record = {"command": command, "exit_code": completed.returncode,
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest()}
    if completed.returncode == 0:
        try:
            document = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise Phase10Error(f"probe emitted invalid JSON: {error}") from error
        if output is None:
            raise Phase10Error("successful probe requires an output path")
        write_json(output, document)
        record.update({"measurement_path": str(output), "measurement_sha256": sha256_file(output),
            "profile_sha256": document["profile_sha256"]})
    return record


def capture(args: argparse.Namespace) -> dict[str, Any]:
    archive = Path(args.archive).resolve()
    offline = load_json(args.offline_replay)
    probe = Path(args.probe).resolve()
    identity = f"{args.project_head}:{args.nested_head}"
    output_dir = Path(args.profile_dir).resolve()
    measurement_dir = Path(args.measurement_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    measurement_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "f16": {"storage": Path(args.f16_storage_map).resolve(), "costs": Path(args.f16_costs).resolve(),
            "model": Path(args.f16_model).resolve()},
        "mxfp4": {"storage": Path(args.mxfp4_storage_map).resolve(), "costs": Path(args.mxfp4_costs).resolve(),
            "model": Path(args.mxfp4_model).resolve()},
    }
    profiles = []
    fold_zero_paths = {}
    for artifact, inputs in artifacts.items():
        expected_target = build_fingerprint(load_json(inputs["storage"]))
        for fold in range(6):
            tuning = selected_tuning(offline, artifact, fold)
            profile = build_profile(archive, inputs["storage"], inputs["costs"], artifact, fold, tuning)
            if profile["target"] != expected_target:
                raise Phase10Error("profile target differs from the exact storage-map fingerprint")
            path = output_dir / f"{profile['profile_id']}.json"
            write_json(path, profile)
            if fold == 0:
                fold_zero_paths[artifact] = path
            profiles.append({"artifact": artifact, "fold": fold, "path": str(path),
                "sha256": sha256_file(path), "bytes": path.stat().st_size,
                "target_package_sha256": profile["target"]["package_sha256"],
                "tensor_layout_sha256": profile["target"]["tensor_layout_sha256"],
                "selection": profile["selection"], "offline_disposition": tuning["disposition"],
                "build_repeatability": "byte_identical", "storage_map_match": True})
    exact_runtime = {}
    for artifact, inputs in artifacts.items():
        measurement = measurement_dir / f"{artifact}-transport-measurements.json"
        exact_runtime[artifact] = run_probe(probe, fold_zero_paths[artifact], inputs["model"], identity, measurement)
        if exact_runtime[artifact]["exit_code"] != 0:
            raise Phase10Error(f"exact {artifact} profile was rejected by the runtime")
    wrong_runtime = {
        "mxfp4_profile_on_f16": run_probe(probe, fold_zero_paths["mxfp4"], artifacts["f16"]["model"], identity, None),
        "f16_profile_on_mxfp4": run_probe(probe, fold_zero_paths["f16"], artifacts["mxfp4"]["model"], identity, None),
    }
    if any(record["exit_code"] == 0 for record in wrong_runtime.values()):
        raise Phase10Error("wrong-package runtime profile was accepted")
    return {"schema_version": "phase10-profile-compatibility-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "phase2_archive_sha256": sha256_file(archive),
        "profiles": profiles, "exact_runtime": exact_runtime, "wrong_package_runtime": wrong_runtime,
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
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--profile-dir", required=True)
    parser.add_argument("--measurement-dir", required=True)
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

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
from prefetch_common import (PHASE2_ARCHIVE_SHA256, Phase10Error, build_fingerprint, canonical_bytes, load_json,
    validate_profile, write_json)


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


def require_commit(value: str, name: str) -> None:
    if len(value) != 40 or any(byte not in "0123456789abcdef" for byte in value):
        raise Phase10Error(f"{name} must be a lowercase commit SHA")


def validate_measurement(
        document: dict[str, Any],
        project_head: str,
        nested_head: str,
        profile_sha256: str,
        storage_map_sha256: str) -> None:
    if document.get("schema_version") != "phase10-transport-measurements-v1":
        raise Phase10Error("probe emitted an unsupported measurement schema")
    if document.get("project_head") != project_head or document.get("nested_head") != nested_head:
        raise Phase10Error("probe emitted a different revision identity")
    if document.get("profile_sha256") != profile_sha256:
        raise Phase10Error("probe measured a different profile")
    provenance = document.get("path_provenance")
    if not isinstance(provenance, dict) or provenance.get("storage_map_sha256") != storage_map_sha256 or \
            provenance.get("exact_runtime_provider_path") is not True:
        raise Phase10Error("probe did not bind the exact runtime storage path")
    for envelope in document.get("envelopes", []):
        basis = envelope.get("measurement_basis")
        if not isinstance(basis, dict):
            raise Phase10Error("measurement envelope lacks path provenance")
        if envelope.get("supported") and (basis.get("storage_map_sha256") != storage_map_sha256 or
                basis.get("all_observed_spans_exact") is not True or
                envelope.get("scheduler_demand_delay_p95_ns", 0) <= 0 or
                envelope.get("displacement_refill_p95_ns", 0) <= 0 or
                basis.get("scheduler_samples", 0) <= 0 or
                (basis.get("storage_refill_samples", 0) <= 0 and basis.get("h2d_refill_samples", 0) <= 0)):
            raise Phase10Error("supported envelope lacks measured scheduler or displacement/refill samples")


def build_calibration_profile(archive: Path, storage_map: Path, costs: Path, artifact: str) -> dict[str, Any]:
    arguments = SimpleNamespace(archive=str(archive), storage_map=str(storage_map), artifact=artifact, fold=0,
        costs=str(costs), transport="BUFFERED", readiness="DEVICE_READY", policy="TEMPORAL_FREQUENCY",
        candidates=2, temporal_window=4, seed_slots=14)
    first = build(arguments)
    second = build(arguments)
    if canonical_bytes(first) != canonical_bytes(second):
        raise Phase10Error("calibration profile build was not byte-identical")
    if first["target"] != build_fingerprint(load_json(storage_map)):
        raise Phase10Error("calibration profile target differs from the storage map")
    validate_profile(first)
    return first


def capture(args: argparse.Namespace) -> dict[str, Any]:
    archive = Path(args.archive).resolve()
    probe = Path(args.probe).resolve()
    profile_dir = Path(args.profile_dir).resolve()
    measurement_dir = Path(args.measurement_dir).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    measurement_dir.mkdir(parents=True, exist_ok=True)
    require_commit(args.project_head, "project_head")
    require_commit(args.nested_head, "nested_head")
    if sha256_file(archive) != PHASE2_ARCHIVE_SHA256:
        raise Phase10Error("Phase 2 archive identity mismatch")
    if not probe.is_file():
        raise Phase10Error("transport probe executable is unavailable")
    artifacts = {
        "f16": {"storage": Path(args.f16_storage_map).resolve(),
            "bootstrap_costs": Path(args.f16_bootstrap_costs).resolve(), "model": Path(args.f16_model).resolve()},
        "mxfp4": {"storage": Path(args.mxfp4_storage_map).resolve(),
            "bootstrap_costs": Path(args.mxfp4_bootstrap_costs).resolve(), "model": Path(args.mxfp4_model).resolve()},
    }
    records = {}
    identity = f"{args.project_head}:{args.nested_head}"
    for artifact, inputs in artifacts.items():
        bootstrap = load_json(inputs["bootstrap_costs"])
        if bootstrap.get("schema_version") != "phase10-transport-break-even-v1" or bootstrap.get("status") != "pass":
            raise Phase10Error("bootstrap cost input is not eligible")
        profile = build_calibration_profile(archive, inputs["storage"], inputs["bootstrap_costs"], artifact)
        profile_path = profile_dir / f"{artifact}-calibration-profile.json"
        write_json(profile_path, profile)
        profile_sha256 = sha256_file(profile_path)
        command = [str(probe), "--profile", str(profile_path), "--model", str(inputs["model"]),
            "--identity", identity, "--storage-map", str(inputs["storage"])]
        completed = subprocess.run(command, check=False, capture_output=True)
        if completed.returncode != 0:
            raise Phase10Error(f"{artifact} transport probe failed; stderr sha256 "
                f"{hashlib.sha256(completed.stderr).hexdigest()}")
        try:
            measurement = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise Phase10Error(f"{artifact} transport probe emitted invalid JSON: {error}") from error
        storage_map_sha256 = sha256_file(inputs["storage"])
        validate_measurement(measurement, args.project_head, args.nested_head, profile_sha256,
            storage_map_sha256)
        measurement_path = measurement_dir / f"{artifact}-transport-measurements.json"
        write_json(measurement_path, measurement)
        records[artifact] = {"profile_path": evidence_path(profile_path), "profile_sha256": profile_sha256,
            "profile_bytes": profile_path.stat().st_size, "measurement_path": evidence_path(measurement_path),
            "measurement_sha256": sha256_file(measurement_path), "command": command,
            "bootstrap_cost_path": evidence_path(inputs["bootstrap_costs"]),
            "bootstrap_cost_sha256": sha256_file(inputs["bootstrap_costs"]),
            "storage_map_path": evidence_path(inputs["storage"]),
            "storage_map_sha256": storage_map_sha256,
            "bootstrap_cost_revision_binding": "calibration_only_not_transferred",
            "build_repeatability": "byte_identical"}
    return {"schema_version": "phase10-transport-calibration-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "phase2_archive_sha256": sha256_file(archive),
        "bootstrap_role": "profile_shape_only; measured costs are derived at the requested exact revisions",
        "artifacts": records}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--f16-storage-map", required=True)
    parser.add_argument("--mxfp4-storage-map", required=True)
    parser.add_argument("--f16-bootstrap-costs", required=True)
    parser.add_argument("--mxfp4-bootstrap-costs", required=True)
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

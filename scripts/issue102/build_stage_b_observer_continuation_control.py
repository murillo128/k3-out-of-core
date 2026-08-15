#!/usr/bin/env python3
"""Freeze the post-retry observer continuation controller and hygiene reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


EXPECTED_RESUME_SHA256 = "ffde39561a0574ccf4b7313d3fbc2ef20a7dcaf321ac8d79987581a533070f36"
EXPECTED_RETRY_CHECKPOINT_SHA256 = "ee898981e49b40a0190b467c6f83c6216fe3b3b5a89ce29ae4ada6b219091d04"
EXPECTED_V2_SHA256 = "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
EXPECTED_INITIAL_HYGIENE_SHA256 = "0ff25fceb0df8bffa58130dfdd0ade244912e9502ae3007277cc1737f53b3ba8"
EXPECTED_HOST_NORMALIZATION_SHA256 = "c93049ba3ac7929be7aed885690f8c17e2a14ad4589e6c43bb87cb2ef5849b27"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--retry-checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--v2-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--initial-hygiene", type=pathlib.Path, required=True)
    parser.add_argument("--host-normalization", type=pathlib.Path, required=True)
    parser.add_argument("--controller", type=pathlib.Path, required=True)
    parser.add_argument("--capture-validator", type=pathlib.Path, required=True)
    parser.add_argument("--route-validator", type=pathlib.Path, required=True)
    parser.add_argument("--allowlist-builder", type=pathlib.Path, required=True)
    parser.add_argument("--hygiene-tool", type=pathlib.Path, required=True)
    parser.add_argument("--output-reference", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    value: dict[str, Any] = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if resolved.suffix == ".json":
        document = json.loads(resolved.read_text())
        if "schema_version" in document:
            value["schema_version"] = document["schema_version"]
    return value


def require_identity(path: pathlib.Path, expected: str, label: str) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    if sha256(resolved) != expected:
        raise ValueError(f"{label} identity changed")
    return resolved


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    args = arguments()
    resume_path = require_identity(args.resume_preregistration, EXPECTED_RESUME_SHA256, "resume")
    checkpoint_path = require_identity(
        args.retry_checkpoint, EXPECTED_RETRY_CHECKPOINT_SHA256, "retry checkpoint",
    )
    v2_path = require_identity(args.v2_preregistration, EXPECTED_V2_SHA256, "V2 preregistration")
    initial_path = require_identity(
        args.initial_hygiene, EXPECTED_INITIAL_HYGIENE_SHA256, "initial hygiene",
    )
    host_path = require_identity(
        args.host_normalization, EXPECTED_HOST_NORMALIZATION_SHA256, "host normalization",
    )
    resume = json.loads(resume_path.read_text())
    checkpoint = json.loads(checkpoint_path.read_text())
    v2 = json.loads(v2_path.read_text())
    initial = json.loads(initial_path.read_text())
    host = json.loads(host_path.read_text())
    model_identity = json.loads(pathlib.Path(v2["inputs"]["model_identity"]["path"]).read_text())
    first_shard = model_identity["artifact"]["files"][0]
    if (
        pathlib.Path(host["identities"]["model_first_shard_path"]).name != first_shard["name"]
        or host["identities"]["model_first_shard_bytes"] != first_shard["size"]
        or host["identities"]["model_identity_manifest_sha256"]
        != v2["inputs"]["model_identity"]["sha256"]
    ):
        raise ValueError("normalized model entry shard does not match the frozen identity manifest")
    if checkpoint["disposition"] != "CAPTURE_004_RETRY_ACCEPTED_READY_FOR_ORDINAL_005":
        raise ValueError("retry checkpoint is not ready for ordinal 5")
    remaining = resume["resume_plan"][1:]
    if len(remaining) != 40 or [row["ordinal"] for row in remaining] != list(range(5, 45)):
        raise ValueError("continuation is not the exact ordinal 5..44 suffix")

    projection = initial["host"]["guard_projection_after"]
    reference = {
        "schema_version": "phase13-6pg-observer-hygiene-reference-v1",
        "status": "frozen",
        "provenance": "DERIVED_NON_SCIENTIFIC_HYGIENE_REFERENCE",
        "source": identity(initial_path),
        "generator": identity(pathlib.Path(__file__)),
        "preflight": {
            "system_memory": {
                "model_file_cache_resident_bytes": projection["reference_model_file_cache_resident_bytes"],
                "model_file_virtual_bytes": projection["reference_model_file_virtual_bytes"],
                "system_reserve_bytes": projection["system_reserve_bytes"],
                "runtime_reserve_bytes": projection["runtime_reserve_bytes"],
                "hysteresis_bytes": projection["hysteresis_bytes"],
            },
        },
        "scientific_or_performance_evidence": False,
        "disposition": "REFERENCE_ONLY_FOR_POST_ATTEMPT_HYGIENE_PROJECTION",
    }
    reference_path = args.output_reference.resolve()
    if reference_path.exists():
        frozen_reference = json.loads(reference_path.read_text())
        if (
            frozen_reference.get("schema_version") != reference["schema_version"]
            or frozen_reference.get("status") != "frozen"
            or frozen_reference.get("source", {}).get("sha256")
            != reference["source"]["sha256"]
            or frozen_reference.get("preflight") != reference["preflight"]
            or frozen_reference.get("disposition") != reference["disposition"]
        ):
            raise ValueError("existing frozen hygiene reference differs from the derived values")
    else:
        write_json(reference_path, reference)

    tools = {
        "controller": identity(args.controller),
        "capture_validator": identity(args.capture_validator),
        "route_validator": identity(args.route_validator),
        "allowlist_builder": identity(args.allowlist_builder),
        "hygiene_tool": identity(args.hygiene_tool),
    }
    runtime_files = {
        "helper_source": identity(pathlib.Path(v2["runtime"]["helper_source"]["path"])),
        "helper_binary": identity(pathlib.Path(v2["runtime"]["helper_binary"]["path"])),
        "runner": identity(pathlib.Path(v2["runtime"]["runner"]["path"])),
    }
    for name, expected in v2["runtime"].items():
        if name in runtime_files and runtime_files[name]["sha256"] != expected["sha256"]:
            raise ValueError(f"runtime {name} identity changed")

    output = {
        "schema_version": "phase13-6pg-stage-b-observer-continuation-control-v4",
        "status": "frozen",
        "provenance": "PREREGISTERED_MEASURED_OBSERVER_CONTINUATION",
        "inputs": {
            "resume_preregistration": identity(resume_path),
            "retry_checkpoint": identity(checkpoint_path),
            "v2_preregistration": identity(v2_path),
            "initial_hygiene": identity(initial_path),
            "host_normalization": identity(host_path),
            "hygiene_reference": identity(reference_path),
            "generator": identity(pathlib.Path(__file__)),
            "tools": tools,
            "runtime_files": runtime_files,
        },
        "runtime": resume["runtime"],
        "model_entry_shard": {
            "path": host["identities"]["model_first_shard_path"],
            "bytes": host["identities"]["model_first_shard_bytes"],
            "identity_manifest_sha256": host["identities"]["model_identity_manifest_sha256"],
            "content_hash_must_not_be_recomputed": True,
        },
        "configuration": resume["configuration"],
        "accepted_prefix": {
            "capture_count": 4,
            "captures_001_003": resume["preserved"]["accepted_captures"],
            "capture_004_retry": checkpoint["accepted_retry"],
            "original_capture_004_failure": checkpoint["preserved_pre_context_failure"],
        },
        "remaining_plan": remaining,
        "remaining_capture_count": len(remaining),
        "attempt_contract": {
            "fresh_process_per_capture": True,
            "serial_execution": True,
            "authorized_process_attempts": 40,
            "retry_allowed": False,
            "preserve_every_failed_or_missing_capture": True,
            "stop_after_first_failure": True,
        },
        "hygiene_contract": {
            **resume["hygiene_contract"],
            "isolated_exhaustive_validation_before_release": True,
            "validation_process_exits_before_next_k3": True,
            "small_frozen_reference_avoids_reopening_released_result_payload": True,
            "allowlist_and_hygiene_records_live_outside_observer_output_root": True,
        },
        "validation": resume["validation"],
        "outcome_inspection": "NO_CAPTURE_005_OR_LATER_K3_OUTCOME_INSPECTED",
        "performance_interpretation": "FORBIDDEN",
        "disposition": "READY_FOR_SERIAL_CAPTURE_005_THROUGH_044",
    }
    output_path = args.output.resolve()
    write_json(output_path, output)
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "hygiene_reference_sha256": sha256(reference_path),
        "remaining_capture_count": len(remaining),
        "status": "frozen",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

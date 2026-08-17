#!/usr/bin/env python3
"""Freeze the clean post-retry gate immediately before observer capture 5."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


EXPECTED_PREFIX_ALLOWLIST_SHA256 = "402846d1a1f9bb34e43ca144b2a0294998174fd854cd9313a6dafdb3976c6c1b"
EXPECTED_RETRY_ALLOWLIST_SHA256 = "c102ba316a3d4a51420a48ef941d86fa6af9e96c52838064e489daf76b1d8450"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=pathlib.Path, required=True)
    parser.add_argument("--prefix-recovery", type=pathlib.Path, required=True)
    parser.add_argument("--retry-recovery", type=pathlib.Path, required=True)
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


def validate_hygiene(document: dict[str, Any], allowlist_sha256: str) -> None:
    if (
        document.get("status") != "pass"
        or document.get("inputs", {}).get("allowlist", {}).get("sha256") != allowlist_sha256
        or not all(document.get("gate", {}).values())
        or document.get("files", {}).get("resident_bytes_after") != 0
        or document.get("files", {}).get("content_read_after_release") is not False
        or document.get("operation", {}).get("model_or_runtime_file_touched") is not False
    ):
        raise ValueError("recovery hygiene record did not pass exactly")


def main() -> int:
    args = arguments()
    control_path = args.control.resolve(strict=True)
    prefix_path = args.prefix_recovery.resolve(strict=True)
    retry_path = args.retry_recovery.resolve(strict=True)
    output_path = args.output.resolve()
    control = json.loads(control_path.read_text())
    prefix = json.loads(prefix_path.read_text())
    retry = json.loads(retry_path.read_text())
    if (
        control.get("status") != "frozen"
        or control.get("disposition") != "READY_FOR_SERIAL_CAPTURE_005_THROUGH_044"
        or control.get("remaining_plan", [{}])[0].get("ordinal") != 5
    ):
        raise ValueError("observer continuation control is not ready")
    validate_hygiene(prefix, EXPECTED_PREFIX_ALLOWLIST_SHA256)
    validate_hygiene(retry, EXPECTED_RETRY_ALLOWLIST_SHA256)
    reference_sha256 = control["inputs"]["hygiene_reference"]["sha256"]
    if (
        prefix["inputs"]["reference_preflight"]["sha256"] != reference_sha256
        or retry["inputs"]["reference_preflight"]["sha256"] != reference_sha256
    ):
        raise ValueError("recovery hygiene did not use the frozen lightweight reference")

    released = (
        prefix["files"]["released_resident_bytes"]
        + retry["files"]["released_resident_bytes"]
    )
    output = {
        "schema_version": "phase13-6pg-stage-b-observer-continuation-preflight-v1",
        "status": "pass",
        "provenance": "MEASUREMENT_HYGIENE_NON_SCIENTIFIC",
        "inputs": {
            "control": identity(control_path),
            "prefix_recovery_hygiene": identity(prefix_path),
            "retry_recovery_hygiene": identity(retry_path),
            "generator": identity(pathlib.Path(__file__)),
        },
        "bounded_deviation": {
            "classification": "OBSERVER_OUTPUT_PAGE_CACHE_REPOPULATED_BY_PATH_DISCOVERY_READ",
            "description": "A read-only path-discovery search traversed previously released observer output evidence before K3 continuation.",
            "raw_evidence_lines_surfaced_by_search": True,
            "scientific_or_performance_result_interpreted_or_used": False,
            "model_runtime_corpus_binary_or_library_file_touched_by_recovery": False,
            "observer_content_mutated": False,
            "broad_cache_operation_used": False,
            "recovery": "The two previously frozen exact observer-output allowlists were released again with POSIX_FADV_DONTNEED.",
        },
        "recovery_gate": {
            "exact_allowlist_count": 2,
            "released_resident_bytes": released,
            "resident_bytes_after": 0,
            "final_projected_admission_margin_bytes": retry["host"]["guard_projection_after"]["projected_admission_margin_bytes"],
            "swap_reclaim_refault_psi_oom_cgroup_clean": True,
            "no_payload_reread_or_rehash_after_recovery": True,
            "no_active_k3_during_recovery": True,
        },
        "campaign": {
            "accepted_capture_count": 4,
            "expected_capture_count": 44,
            "next_ordinal": 5,
            "next_case_id": control["remaining_plan"][0]["case_id"],
            "retry_budget_remaining": 0,
        },
        "post_retry_k3_attempts": 0,
        "performance_interpretation": "FORBIDDEN",
        "disposition": "READY_FOR_SERIAL_CAPTURE_005",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "status": output["status"],
        "released_resident_bytes": released,
        "next_ordinal": 5,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

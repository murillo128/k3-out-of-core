#!/usr/bin/env python3
"""Freeze the bounded issue-102 observer campaign resume contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


EXPECTED_V2_SHA256 = "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
EXPECTED_RETURN_SHA256 = "c1493c3732e349179ce71dc8a5933da82736967083fee87d07ceef57d3f3d264"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v2-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--technical-return", type=pathlib.Path, required=True)
    parser.add_argument("--initial-allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--hygiene-tool", type=pathlib.Path, required=True)
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


def main() -> int:
    args = arguments()
    v2_path = args.v2_preregistration.resolve(strict=True)
    return_path = args.technical_return.resolve(strict=True)
    allowlist_path = args.initial_allowlist.resolve(strict=True)
    hygiene_path = args.hygiene_tool.resolve(strict=True)
    output_path = args.output.resolve()
    if sha256(v2_path) != EXPECTED_V2_SHA256:
        raise ValueError("observer V2 preregistration identity changed")
    if sha256(return_path) != EXPECTED_RETURN_SHA256:
        raise ValueError("observer technical-return identity changed")
    v2 = json.loads(v2_path.read_text())
    technical_return = json.loads(return_path.read_text())
    allowlist = json.loads(allowlist_path.read_text())
    if allowlist["disposition"] != "READY_FOR_TARGETED_HYGIENE_GATE":
        raise ValueError("initial observer evidence allowlist is not frozen")
    if technical_return["campaign"]["accepted_capture_count"] != 3:
        raise ValueError("technical return does not preserve exactly three accepted captures")
    failure = technical_return["campaign"]["failed"]
    if failure["ordinal"] != 4 or failure["scientific_result_available"]:
        raise ValueError("technical return does not own the pre-context ordinal-4 failure")

    remaining_plan = []
    for source in v2["capture_plan"][3:]:
        row = dict(source)
        if row["ordinal"] == 4:
            row["output_directory"] += "-retry-01"
            row["same_case_retry"] = 1
            row["supersedes_pre_context_attempt"] = failure["output_directory"]
        else:
            row["same_case_retry"] = 0
        remaining_plan.append(row)
    if len(remaining_plan) != 41 or remaining_plan[0]["ordinal"] != 4:
        raise ValueError("resume plan is not the exact retry plus ordinals 5..44")

    output = {
        "schema_version": "phase13-6pg-stage-b-observer-resume-preregistration-v3",
        "status": "frozen",
        "provenance": "PREREGISTERED_MEASURED_OBSERVER_RESUME",
        "inputs": {
            "v2_preregistration": identity(v2_path),
            "technical_return": identity(return_path),
            "initial_evidence_cache_allowlist": identity(allowlist_path),
            "hygiene_tool": identity(hygiene_path),
            "generator": identity(pathlib.Path(__file__)),
        },
        "runtime": v2["runtime"],
        "configuration": v2["configuration"],
        "preserved": {
            "accepted_captures": technical_return["campaign"]["accepted"],
            "pre_context_capture_004_failure": failure,
            "accepted_captures_may_be_rerun": False,
            "failed_capture_may_be_deleted_or_hidden": False,
        },
        "hygiene_contract": {
            "initial_gate_required_before_retry": True,
            "target": "OBSERVER_CAMPAIGN_REGULAR_EVIDENCE_FILES_ONLY",
            "durability": "syncfs before exact-file advice",
            "advice": "POSIX_FADV_DONTNEED",
            "canonical_path_device_inode_size_sha256_required": True,
            "resident_bytes_before_after_required": True,
            "full_payload_read_or_hash_after_release_allowed": False,
            "model_runtime_corpus_binary_or_library_file_allowed": False,
            "required_after_every_observer_attempt": True,
            "required_before_later_physical_k3_work": True,
        },
        "resume_plan": remaining_plan,
        "remaining_capture_count": len(remaining_plan),
        "attempt_budget": {
            "prior_process_attempts": 5,
            "authorized_new_process_attempts": 41,
            "maximum_total_process_attempts": 46,
            "capture_004_retry_limit": 1,
            "retry_until_pass": False,
        },
        "validation": v2["validation"],
        "outcome_inspection": "NO_POST_RETURN_K3_OR_HYGIENE_OUTCOME_INSPECTED",
        "disposition": "READY_FOR_INITIAL_EVIDENCE_CACHE_HYGIENE_GATE",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "remaining_capture_count": len(remaining_plan),
        "status": output["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

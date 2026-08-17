#!/usr/bin/env python3
"""Freeze the accepted ordinal-4 retry without rereading released evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


EXPECTED_RESUME_SHA256 = "ffde39561a0574ccf4b7313d3fbc2ef20a7dcaf321ac8d79987581a533070f36"
EXPECTED_INITIAL_HYGIENE_SHA256 = "0ff25fceb0df8bffa58130dfdd0ade244912e9502ae3007277cc1737f53b3ba8"
EXPECTED_RETRY_ALLOWLIST_SHA256 = "c102ba316a3d4a51420a48ef941d86fa6af9e96c52838064e489daf76b1d8450"
EXPECTED_RETRY_HYGIENE_SHA256 = "04543c4b0f87cf6772a0f29f8b192bdebf64a764ca220a5a83a22c13ec2da6d4"
EXPECTED_RETRY_PROJECT_SHA = "734d33a39ec17606571289558b95297c1a5ceb51"
EXPECTED_RESULT_SHA256 = "f0a795961cea21bb1a575c664ec21cbf1607f98f3e8f8bcc689da9440d59ae81"
EXPECTED_ENVELOPE_SHA256 = "8f24d3f7a7c414e721f7147ad1f72635c8ad6cb02c2bd6dfc39b4d192078c3eb"
EXPECTED_STDOUT_SHA256 = "23a7156d711f5d05278267201369ac507bb47b852feb9ac492834517ce26d22e"
EXPECTED_STDERR_SHA256 = "08d85235c87ca327f337bf2a23a5250e06e2ae94ced24b687fbb05344f317a7f"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--initial-hygiene", type=pathlib.Path, required=True)
    parser.add_argument("--retry-allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--retry-hygiene", type=pathlib.Path, required=True)
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


def main() -> int:
    args = arguments()
    resume_path = require_identity(
        args.resume_preregistration, EXPECTED_RESUME_SHA256, "resume preregistration",
    )
    initial_path = require_identity(
        args.initial_hygiene, EXPECTED_INITIAL_HYGIENE_SHA256, "initial hygiene gate",
    )
    allowlist_path = require_identity(
        args.retry_allowlist, EXPECTED_RETRY_ALLOWLIST_SHA256, "retry allowlist",
    )
    hygiene_path = require_identity(
        args.retry_hygiene, EXPECTED_RETRY_HYGIENE_SHA256, "retry hygiene gate",
    )
    output_path = args.output.resolve()

    resume = json.loads(resume_path.read_text())
    initial = json.loads(initial_path.read_text())
    allowlist = json.loads(allowlist_path.read_text())
    hygiene = json.loads(hygiene_path.read_text())
    plan = resume["resume_plan"][0]
    if (
        plan["ordinal"] != 4
        or plan["case_id"] != "04-factual-b2"
        or plan["same_case_retry"] != 1
        or not plan["output_directory"].endswith("run-004-04-factual-b2-retry-01")
    ):
        raise ValueError("resume preregistration does not own the exact ordinal-4 retry")
    if initial["status"] != "pass" or not all(initial["gate"].values()):
        raise ValueError("initial hygiene gate did not pass")
    if (
        allowlist["capture"] != {
            "ordinal": 4,
            "case_id": "04-factual-b2",
            "status": "pass",
            "root": plan["output_directory"],
        }
        or allowlist["file_count"] != 4
        or allowlist["total_bytes"] != 131604594
    ):
        raise ValueError("retry allowlist does not own the accepted attempt")
    expected_hashes = {
        "result.json": EXPECTED_RESULT_SHA256,
        "envelope.json": EXPECTED_ENVELOPE_SHA256,
        "stdout.log": EXPECTED_STDOUT_SHA256,
        "stderr.log": EXPECTED_STDERR_SHA256,
    }
    files = {pathlib.Path(row["canonical_path"]).name: row for row in allowlist["files"]}
    if set(files) != set(expected_hashes):
        raise ValueError("retry allowlist file set changed")
    if any(files[name]["sha256"] != value for name, value in expected_hashes.items()):
        raise ValueError("retry evidence identity changed")
    if (
        hygiene["status"] != "pass"
        or not all(hygiene["gate"].values())
        or hygiene["inputs"]["allowlist"]["sha256"] != EXPECTED_RETRY_ALLOWLIST_SHA256
        or hygiene["files"]["resident_bytes_before"] != 131604594
        or hygiene["files"]["resident_bytes_after"] != 0
        or hygiene["files"]["content_read_after_release"]
        or hygiene["operation"]["model_or_runtime_file_touched"]
    ):
        raise ValueError("post-retry hygiene gate did not pass exactly")

    validation = {
        "performed_before_targeted_page_cache_release": True,
        "result_schema_status_exit_case": "pass",
        "project_sha": EXPECTED_RETRY_PROJECT_SHA,
        "nested_sha": resume["runtime"]["nested_llama_cpp"],
        "point": "EXACT",
        "protocol": "full-prompt",
        "prompt_tokens": 199,
        "generated_tokens": 64,
        "observer_records": 24196,
        "selected_occurrences": 387136,
        "candidate_occurrences": 774272,
        "exhaustive_record_order_shape_prefix_and_finite_checks": "pass",
        "host_swap_reclaim_pressure_oom_cgroup_and_unused_nvme_checks": "pass",
        "post_release_payload_read_or_hash": False,
    }
    output = {
        "schema_version": "phase13-6pg-stage-b-observer-retry-checkpoint-v1",
        "status": "pass",
        "provenance": "MEASURED_OBSERVER_NON_PERFORMANCE",
        "inputs": {
            "resume_preregistration": identity(resume_path),
            "initial_hygiene": identity(initial_path),
            "retry_allowlist": identity(allowlist_path),
            "retry_hygiene": identity(hygiene_path),
            "generator": identity(pathlib.Path(__file__)),
        },
        "preserved_pre_context_failure": resume["preserved"]["pre_context_capture_004_failure"],
        "accepted_retry": {
            "ordinal": 4,
            "case_id": "04-factual-b2",
            "selection_role": plan["selection_role"],
            "output_directory": plan["output_directory"],
            "execution_project_sha": EXPECTED_RETRY_PROJECT_SHA,
            "evidence": {
                name: {
                    "path": files[name]["canonical_path"],
                    "bytes": files[name]["bytes"],
                    "sha256": files[name]["sha256"],
                }
                for name in ("result.json", "envelope.json", "stdout.log", "stderr.log")
            },
            "validation": validation,
            "hygiene": {
                "released_resident_bytes": hygiene["files"]["released_resident_bytes"],
                "resident_bytes_after": hygiene["files"]["resident_bytes_after"],
                "projected_margin_before": hygiene["host"]["guard_projection_before"]["projected_admission_margin_bytes"],
                "projected_margin_after": hygiene["host"]["guard_projection_after"]["projected_admission_margin_bytes"],
                "status": hygiene["status"],
            },
        },
        "campaign": {
            "accepted_capture_count": 4,
            "expected_capture_count": 44,
            "retry_budget_consumed": 1,
            "retry_budget_remaining": 0,
            "next_capture": resume["resume_plan"][1],
        },
        "performance_interpretation": "FORBIDDEN",
        "disposition": "CAPTURE_004_RETRY_ACCEPTED_READY_FOR_ORDINAL_005",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "status": output["status"],
        "accepted_capture_count": 4,
        "next_ordinal": 5,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

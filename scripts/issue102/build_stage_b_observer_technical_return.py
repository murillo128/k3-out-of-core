#!/usr/bin/env python3
"""Freeze the issue-102 Stage-B/B2 observer technical-return evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


EXPECTED_PREREGISTRATION_SHA256 = (
    "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
)
EXPECTED_RUNTIME_SOURCE_TARGET = "7d7307452a97eec9b30d5028bd9e831a96c73990"
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
EXPECTED_HELPER_SHA256 = "a8cd60963c7da3ece8937ba83834435217ac2ec7922de15c50cd5a59743fb392"
EXPECTED_RUNNER_SHA256 = "0e09960035666f15bfc82cef2a8dd81358f744a848f3f1f633d27d420afeca92"
EXPECTED_CACHE_BYTES = 137728475136
EXPECTED_CACHE_SLOTS = 7849
EXPECTED_ROUTED_LAYERS = 92
EXPECTED_DECODE_FORWARDS = 64


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--campaign-progress", type=pathlib.Path, required=True)
    parser.add_argument("--failed-attempt-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def all_zero(values: dict[str, int]) -> bool:
    return all(value == 0 for value in values.values())


def host_safety(envelope: dict[str, Any]) -> dict[str, bool]:
    vmstat = envelope["delta"]["vmstat"]
    return {
        "zero_swap": envelope["samples"]["peak_process_swap_kib"] == 0
        and vmstat["pswpin"] == 0
        and vmstat["pswpout"] == 0,
        "zero_reclaim_refault": all(
            value == 0
            for key, value in vmstat.items()
            if key.startswith(("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_"))
        ),
        "zero_oom_cgroup": vmstat["oom_kill"] == 0
        and all_zero(envelope["delta"]["cgroup_memory_events"]),
        "zero_memory_pressure": all_zero(envelope["memory_pressure_total_delta_usec"]),
        "zero_unused_nvme_reads": envelope["delta"]["nvme"]["nvme2n1"]["read_bytes"] == 0
        and envelope["delta"]["nvme"]["nvme2n1"]["read_operations"] == 0,
    }


def accepted_capture(plan: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    root = pathlib.Path(plan["output_directory"]).resolve()
    result_path = root / "result.json"
    envelope_path = root / "envelope.json"
    stdout_path = root / "stdout.log"
    stderr_path = root / "stderr.log"
    result = json.loads(result_path.read_text())
    envelope = json.loads(envelope_path.read_text())
    prompt_tokens = plan["actual_templated_prompt_tokens"]
    expected_records = (prompt_tokens + EXPECTED_DECODE_FORWARDS) * EXPECTED_ROUTED_LAYERS
    observer = result["observer"]
    cold = result["preflight"]["initial_cold"]
    memory = result["preflight"]["system_memory"]
    safety = host_safety(envelope)
    checks = {
        "result pass": result["status"] == "pass" and result["exit_status"] == 0,
        "runner pass": envelope["exit_status"] == 0,
        "case": result["case"]["id"] == plan["case_id"],
        "run ordinal": envelope["run_ordinal"] == plan["ordinal"],
        "frozen capacity": cold["actual_bytes"] == EXPECTED_CACHE_BYTES
        and cold["capacity"] == EXPECTED_CACHE_SLOTS,
        "decode": result["output"]["generated_token_count"] == EXPECTED_DECODE_FORWARDS,
        "observer records": observer["record_count"] == expected_records
        and len(observer["records"]) == expected_records,
        "observer selected": observer["selected_occurrence_count"] == expected_records * 16,
        "observer candidates": observer["candidate_occurrence_count"] == expected_records * 32,
        "observer failures": observer["stats"]["failures"] == 0,
        "non-performance": observer["performance_evidence"] is False,
        "binary": envelope["identities"]["binary_sha256"] == EXPECTED_HELPER_SHA256,
        "runner": envelope["identities"]["runner_sha256"] == EXPECTED_RUNNER_SHA256,
        "nested": envelope["identities"]["nested"] == EXPECTED_NESTED_SHA,
        "host safety": all(safety.values()),
    }
    failed = sorted(key for key, value in checks.items() if not value)
    if failed:
        raise ValueError(f"accepted capture validation failed for {plan['case_id']}: {failed}")

    progress_row = next(
        row for row in progress["captures"] if row["ordinal"] == plan["ordinal"]
    )
    artifacts = {
        "result": identity(result_path),
        "envelope": identity(envelope_path),
        "stdout": identity(stdout_path),
        "stderr": identity(stderr_path),
    }
    if (
        artifacts["result"]["sha256"] != progress_row["result"]["sha256"]
        or artifacts["envelope"]["sha256"] != progress_row["envelope"]["sha256"]
    ):
        raise ValueError(f"campaign-progress identity mismatch for {plan['case_id']}")
    return {
        "ordinal": plan["ordinal"],
        "case_id": plan["case_id"],
        "selection_role": plan["selection_role"],
        "execution_project_sha": envelope["identities"]["project"],
        "prompt_tokens": prompt_tokens,
        "generated_tokens": EXPECTED_DECODE_FORWARDS,
        "observer_records": expected_records,
        "admission_safe_pool_bytes": memory["admission_safe_pool_bytes"],
        "admission_margin_bytes": memory["admission_safe_pool_bytes"] - EXPECTED_CACHE_BYTES,
        "artifacts": artifacts,
        "host_safety": safety,
        "provenance": "MEASURED_OBSERVER_NON_PERFORMANCE",
    }


def failed_capture(plan: dict[str, Any], root: pathlib.Path) -> dict[str, Any]:
    resolved = root.resolve()
    result_path = resolved / "result.json"
    envelope_path = resolved / "envelope.json"
    stdout_path = resolved / "stdout.log"
    stderr_path = resolved / "stderr.log"
    if result_path.exists():
        raise ValueError("failed observer attempt unexpectedly has a result payload")
    envelope = json.loads(envelope_path.read_text())
    stderr = stderr_path.read_text()
    safety = host_safety(envelope)
    checks = {
        "case ordinal": envelope["run_ordinal"] == plan["ordinal"],
        "exit status": envelope["exit_status"] == 1,
        "pre-context budget failure": "system-memory cold-cache budget (provider error 6)" in stderr
        and "failed to initialize the context" in stderr,
        "no stdout": stdout_path.stat().st_size == 0,
        "binary": envelope["identities"]["binary_sha256"] == EXPECTED_HELPER_SHA256,
        "runner": envelope["identities"]["runner_sha256"] == EXPECTED_RUNNER_SHA256,
        "nested": envelope["identities"]["nested"] == EXPECTED_NESTED_SHA,
        "host safety": all(safety.values()),
    }
    failed = sorted(key for key, value in checks.items() if not value)
    if failed:
        raise ValueError(f"failed capture validation mismatch: {failed}")
    return {
        "ordinal": plan["ordinal"],
        "case_id": plan["case_id"],
        "execution_project_sha": envelope["identities"]["project"],
        "output_directory": str(resolved),
        "elapsed_s": envelope["elapsed_s"],
        "exit_status": envelope["exit_status"],
        "classification": "HOST_MEMORY_ENVELOPE_DRIFT",
        "failure": "FROZEN_CAPACITY_ADMISSION_FAILURE",
        "provider_error": 6,
        "provider_error_name": "allocation_failed",
        "context_initialized": False,
        "scientific_result_available": False,
        "replacement_allowed": False,
        "artifacts": {
            "envelope": identity(envelope_path),
            "stdout": identity(stdout_path),
            "stderr": identity(stderr_path),
        },
        "host_observation": {
            "minimum_mem_available_kib": envelope["samples"]["minimum_mem_available_kib"],
            "peak_cgroup_memory_current_bytes": envelope["samples"]["peak_cgroup_memory_current_bytes"],
        },
        "host_safety": safety,
        "provenance": "NO_RESULT_PRE_CONTEXT_TECHNICAL_FAILURE",
    }


def main() -> int:
    args = arguments()
    preregistration_path = args.preregistration.resolve()
    progress_path = args.campaign_progress.resolve()
    failure_root = args.failed_attempt_root.resolve()
    output_path = args.output.resolve()
    preregistration = json.loads(preregistration_path.read_text())
    progress = json.loads(progress_path.read_text())
    if sha256(preregistration_path) != EXPECTED_PREREGISTRATION_SHA256:
        raise ValueError("observer preregistration identity changed")
    if preregistration["runtime"]["project_source_target"] != EXPECTED_RUNTIME_SOURCE_TARGET:
        raise ValueError("observer runtime source target changed")
    if preregistration["runtime"]["nested_llama_cpp"] != EXPECTED_NESTED_SHA:
        raise ValueError("nested target changed")
    if (
        progress["disposition"] != "STOPPED_AT_FAILED_CAPTURE_004"
        or progress["accepted_capture_count"] != 3
        or progress["expected_capture_count"] != 44
    ):
        raise ValueError("campaign did not stop at the expected first failed capture")

    plans = preregistration["capture_plan"]
    accepted = [accepted_capture(plans[index], progress) for index in range(3)]
    failure = failed_capture(plans[3], failure_root)
    output = {
        "schema_version": "phase13-6pg-stage-b-observer-technical-return-v1",
        "status": "incomplete",
        "provenance": "MIXED_MEASURED_OBSERVER_AND_PRE_CONTEXT_TECHNICAL_FAILURE",
        "inputs": {
            "preregistration": identity(preregistration_path),
            "campaign_progress": identity(progress_path),
            "generator": identity(pathlib.Path(__file__)),
        },
        "runtime": {
            "runtime_source_target": EXPECTED_RUNTIME_SOURCE_TARGET,
            "nested_llama_cpp": EXPECTED_NESTED_SHA,
            "helper_binary_sha256": EXPECTED_HELPER_SHA256,
            "runner_sha256": EXPECTED_RUNNER_SHA256,
            "cache_slots": EXPECTED_CACHE_SLOTS,
            "cache_bytes": EXPECTED_CACHE_BYTES,
        },
        "campaign": {
            "expected_capture_count": 44,
            "accepted_capture_count": 3,
            "failed_capture_count": 1,
            "not_run_capture_count": 40,
            "first_not_run_ordinal": 5,
            "accepted": accepted,
            "failed": failure,
            "admission_margin_trend_bytes": [
                row["admission_margin_bytes"] for row in accepted
            ],
        },
        "interpretation": {
            "performance_claim_allowed": False,
            "partial_route_diversity_claim_allowed": False,
            "stage_b_disposition": "ROUTE_COVERAGE_NOT_MEASURED",
            "stage_b2_disposition": "FAMILY_LENGTH_ROUTE_ENDPOINTS_NOT_CAPTURED",
            "capacity_or_policy_retuning_authorized": False,
            "failed_capture_replacement_authorized": False,
            "next": "RETURN_TO_DESIGN_FOR_FROZEN_CAPACITY_ADMISSION_DECISION",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "status": output["status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

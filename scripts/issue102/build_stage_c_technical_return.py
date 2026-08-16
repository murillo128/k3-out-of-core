#!/usr/bin/env python3
"""Freeze the pre-context issue-102 Stage-C capacity-admission failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
from typing import Any


EXPECTED_PREREGISTRATION_SHA256 = "c368ca9e8d0e35291e4be9e81747122275c7cd594e4603d570bafc0e7595d256"
EXPECTED_HYGIENE_SHA256 = "0caab39228615412f86ba69f4a58680b0480f99c05673f1e22ab16f55730bf27"
EXPECTED_PROGRESS_SHA256 = "fa516a2eef9f71ac0065516d1dab4ad61b0e43204400ed929a3414737a85d1a0"
EXPECTED_ENVELOPE_SHA256 = "29fbce6a8737f2c2627032fa0c96bdf5286e29d35be4b74934c47ee90d253bbb"
EXPECTED_STDERR_SHA256 = "0f6d3b45b046f4f26da59ca25cecadcaa15a9e5775db736de622def87ba53267"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--hygiene", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--envelope", type=pathlib.Path, required=True)
    parser.add_argument("--stderr", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path, expected: str | None = None) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    result = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if expected is not None and result["sha256"] != expected:
        raise ValueError(f"technical-return input identity changed: {resolved}")
    if resolved.suffix == ".json":
        with resolved.open() as stream:
            document = json.load(stream)
        if "schema_version" in document:
            result["schema_version"] = document["schema_version"]
    return result


def pressure_clean(envelope: dict[str, Any]) -> bool:
    vmstat = envelope["delta"]["vmstat"]
    pressure_prefixes = ("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_")
    pressure_keys = ("pswpin", "pswpout", "oom_kill")
    return (
        all(value == 0 for key, value in vmstat.items() if key in pressure_keys or key.startswith(pressure_prefixes))
        and all(value == 0 for value in envelope["delta"]["cgroup_memory_events"].values())
        and all(value == 0 for value in envelope["memory_pressure_total_delta_usec"].values())
        and envelope["before"]["meminfo"]["SwapTotal"] == 0
        and envelope["after"]["meminfo"]["SwapTotal"] == 0
        and envelope["samples"]["peak_process_swap_kib"] == 0
    )


def main() -> None:
    args = arguments()
    inputs = {
        "stage_c_preregistration": identity(args.preregistration, EXPECTED_PREREGISTRATION_SHA256),
        "final_observer_cache_hygiene": identity(args.hygiene, EXPECTED_HYGIENE_SHA256),
        "failed_progress": identity(args.progress, EXPECTED_PROGRESS_SHA256),
        "failed_envelope": identity(args.envelope, EXPECTED_ENVELOPE_SHA256),
        "failed_stderr": identity(args.stderr, EXPECTED_STDERR_SHA256),
        "generator": identity(pathlib.Path(__file__)),
    }
    prereg = json.loads(args.preregistration.read_text())
    hygiene = json.loads(args.hygiene.read_text())
    progress = json.loads(args.progress.read_text())
    envelope = json.loads(args.envelope.read_text())
    stderr = args.stderr.read_text()
    result_path = args.envelope.parent / "result.json"
    if (
        prereg.get("status") != "frozen"
        or progress.get("status") != "failed"
        or progress.get("accepted_cell_count") != 0
        or progress.get("failed_cell_count") != 1
        or progress.get("retry_budget_remaining") != 0
        or envelope.get("campaign") != "issue102-stage-c"
        or envelope.get("run_ordinal") != 1
        or envelope.get("point") != "EXACT"
        or envelope.get("exit_status") != 1
        or result_path.exists()
        or "provider error 6" not in stderr
        or "context initialization failed" not in stderr
    ):
        raise ValueError("Stage-C failure is not the exact pre-context admission failure")
    observed = {
        "schema_version": "phase13-6pg-stage-c-technical-return-v1",
        "status": "blocked",
        "classification": "PRE_CONTEXT_CAPACITY_ADMISSION_FAILURE",
        "inputs": inputs,
        "attempt": {
            "run_ordinal": 1,
            "prompt_ordinal": 1,
            "case_id": "01-math-b6",
            "point": "EXACT",
            "requested_cache_slots": prereg["configuration"]["cache_slots"],
            "requested_cache_bytes": prereg["configuration"]["cache_bytes"],
            "elapsed_s": envelope["elapsed_s"],
            "helper_exit_status": envelope["exit_status"],
            "result_json_created": False,
            "context_created": False,
            "k3_inference_performed": False,
            "stage_c_performance_outcome_inspected": False,
            "error": "system-memory cold-cache budget failed with provider error 6 before context initialization",
        },
        "host": {
            "mem_available_before_kib": envelope["before"]["meminfo"]["MemAvailable"],
            "minimum_mem_available_kib": envelope["samples"]["minimum_mem_available_kib"],
            "peak_process_rss_kib": envelope["samples"]["peak_process_rss_kib"],
            "peak_process_swap_kib": envelope["samples"]["peak_process_swap_kib"],
            "swap_reclaim_refault_psi_oom_cgroup_clean": pressure_clean(envelope),
            "unused_nvme_read_bytes": envelope["delta"]["nvme"].get("nvme2n1", {}).get("read_bytes", 0),
        },
        "prior_hygiene": {
            "status": hygiene["status"],
            "observer_output_resident_bytes_before": hygiene["files"]["resident_bytes_before"],
            "observer_output_resident_bytes_after": hygiene["files"]["resident_bytes_after"],
            "released_resident_bytes": hygiene["files"]["released_resident_bytes"],
            "projected_admission_margin_after_bytes": hygiene["host"]["guard_projection_after"]["projected_admission_margin_bytes"],
            "production_guard_was_final_authority": True,
            "projection_disposition": "INSUFFICIENT_TO_OVERRIDE_PRODUCTION_REJECTION",
        },
        "postprocessing_file_cache_observation": {
            "classification": "READ_ONLY_MINCORE_AFTER_FAILURE",
            "path_scope": [
                "/mnt/nvme1/issue102/stage-b-analysis-v1/*",
                "/mnt/nvme1/issue102/observer-replay-v1/*",
                "/mnt/nvme1/issue102/posthoc-analysis-v1/*",
            ],
            "regular_file_count": 14,
            "total_bytes": 327971663,
            "resident_bytes": 327971663,
            "advice_applied": False,
            "authority": "No release or retry is authorized by the frozen Stage-C contract.",
        },
        "preservation": {
            "failed_attempt_retained": True,
            "retry_attempted": False,
            "retry_budget_remaining": 0,
            "subsequent_stage_c_processes_started": 0,
            "policy_capacity_runtime_or_selection_changed": False,
        },
        "required_design_decision": (
            "Decide whether to authorize a new clean-host normalization and replacement execution "
            "contract, or a separately bounded release of the 327,971,663 resident bytes belonging "
            "only to issue-102 postprocessing artifacts, followed by a specifically authorized new "
            "attempt. The preserved failed ordinal must not be silently retried."
        ),
        "disposition": "RETURN_TO_DESIGN_NO_STAGE_C_OUTCOME",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(observed, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({"status": "pass", "output": identity(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the final-capable issue 69 Checkpoint-C technical manifest."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

from analyze_checkpoint_a import load_gzip_json, profile_summary, trace_summary
from common import decode_tps, file_identity, output_identity, write_json


T_CRITICAL_95_DF4 = 2.7764451051977987
BASELINE_PROVIDER_P50_MS = 18.024496


def transfer_sum(workload: dict[str, object], device_field: str, local_field: str) -> int:
    devices = workload["multi_gpu"]["devices"]
    if devices:
        return sum(int(device.get(device_field, 0)) for device in devices)
    return int(workload["transfer"].get(local_field, 0))


def terminal_state(workload: dict[str, object]) -> dict[str, int]:
    lifecycle = workload["lifecycle"]
    devices = workload["multi_gpu"]["devices"]
    return {
        "active_background_flights": int(lifecycle["active_background_flights"]),
        "cold_transfer_refs": int(lifecycle["cold_current_transfer_refs"]),
        "cold_request_refs": int(lifecycle["cold_current_request_refs"]),
        "cold_cpu_execution_refs": int(lifecycle["cold_current_cpu_execution_refs"]),
        "hot_pins": int(lifecycle["current_hot_pins"]),
        "ring_live_events": sum(int(device["ring_live_events"]) for device in devices),
        "async_active_reads": int(workload["async_io"]["diagnostics"]["active_read_requests"]),
        "scheduler_active_requests": sum(
            int(device["scheduler"]["active_requests"]) for device in devices
        ),
    }


def summarize_workload(path: Path) -> dict[str, object]:
    workload = load_gzip_json(path)
    direct_bytes = transfer_sum(workload, "ring_direct_storage_bytes", "direct_storage_bytes")
    h2d_bytes = transfer_sum(workload, "ring_h2d_bytes", "h2d_bytes")
    stage_bytes = transfer_sum(workload, "ring_stage_bytes", "stage_bytes")
    direct_reservations = transfer_sum(
        workload, "ring_direct_storage_reservations", "direct_storage_reservations"
    )
    direct_completions = transfer_sum(
        workload, "ring_direct_storage_completions", "direct_storage_completions"
    )
    terminal = terminal_state(workload)
    return {
        "path": str(path),
        "file": file_identity(path),
        "status": workload["status"],
        "decode_tps": decode_tps(workload),
        "generated_identity_sha256": output_identity(workload, include_logits=False),
        "numerical_identity_sha256": output_identity(workload),
        "worker_count": int(workload["async_io"]["diagnostics"]["worker_count"]),
        "storage_read_requests": int(workload["storage"]["read_requests"]),
        "storage_read_bytes": int(workload["storage"]["read_bytes"]),
        "direct_storage_bytes": direct_bytes,
        "direct_storage_reservations": direct_reservations,
        "direct_storage_completions": direct_completions,
        "h2d_bytes": h2d_bytes,
        "stage_bytes": stage_bytes,
        "cold_admissions": int(workload["cold"]["diagnostics"]["mandatory_admissions"]) +
            int(workload["cold"]["diagnostics"]["optional_admission_accepts"]),
        "hierarchy_residency": workload["hierarchy_residency"],
        "cold_prewarm": workload["cold_prewarm"],
        "terminal_state": terminal,
        "terminal_state_zero": not any(terminal.values()),
    }


def raw_runs(directory: Path) -> dict[str, dict[int, dict[str, object]]]:
    result: dict[str, dict[int, dict[str, object]]] = {}
    for path in sorted((directory / "raw").glob("*.json.gz")):
        if path.name.endswith(".log.gz"):
            continue
        cell, pair_text = path.name.removesuffix(".json.gz").rsplit("-", 1)
        result.setdefault(cell, {})[int(pair_text)] = summarize_workload(path)
    return result


def geometric_mean(values: list[float]) -> float:
    return math.exp(statistics.fmean(math.log(value) for value in values))


def paired_interval(ratios: list[float]) -> dict[str, object]:
    if len(ratios) != 5:
        raise ValueError(f"checkpoint C requires exactly five pairs, got {len(ratios)}")
    logs = [math.log(value) for value in ratios]
    mean = statistics.fmean(logs)
    margin = T_CRITICAL_95_DF4 * statistics.stdev(logs) / math.sqrt(len(logs))
    return {
        "method": "two-sided paired Student-t interval on log throughput ratios",
        "confidence": 0.95,
        "degrees_of_freedom": 4,
        "lower": math.exp(mean - margin),
        "upper": math.exp(mean + margin),
    }


def paired_summary(directory: Path) -> dict[str, object]:
    capture_path = directory / "paired-matrix.json"
    capture = json.loads(capture_path.read_text())
    baseline = raw_runs(directory / "baseline")
    final = raw_runs(directory / "final")
    cells: dict[str, object] = {}
    for cell in ("S0", "S1", "A1"):
        pair_ids = sorted(set(baseline[cell]) & set(final[cell]))
        ratios = [final[cell][pair]["decode_tps"] / baseline[cell][pair]["decode_tps"]
                  for pair in pair_ids]
        interval = paired_interval(ratios)
        cells[cell] = {
            "baseline_runs": [baseline[cell][pair] for pair in pair_ids],
            "final_runs": [final[cell][pair] for pair in pair_ids],
            "ratios": ratios,
            "baseline_geometric_mean_decode_tps": geometric_mean(
                [baseline[cell][pair]["decode_tps"] for pair in pair_ids]
            ),
            "final_geometric_mean_decode_tps": geometric_mean(
                [final[cell][pair]["decode_tps"] for pair in pair_ids]
            ),
            "geometric_mean_ratio": geometric_mean(ratios),
            "paired_interval": interval,
            "exact_generated_identity": len({
                run["generated_identity_sha256"]
                for candidate in (baseline[cell], final[cell])
                for run in candidate.values()
            }) == 1,
            "exact_numerical_identity": len({
                run["numerical_identity_sha256"]
                for candidate in (baseline[cell], final[cell])
                for run in candidate.values()
            }) == 1,
            "all_workers_two": all(
                run["worker_count"] == 2
                for candidate in (baseline[cell], final[cell])
                for run in candidate.values()
            ),
            "all_terminal_state_zero": all(
                run["terminal_state_zero"]
                for candidate in (baseline[cell], final[cell])
                for run in candidate.values()
            ),
        }
    cells["S0"]["gate"] = (
        cells["S0"]["geometric_mean_ratio"] >= 1.05 and
        cells["S0"]["paired_interval"]["upper"] >= 1.0
    )
    for cell in ("S1", "A1"):
        cells[cell]["gate"] = cells[cell]["geometric_mean_ratio"] >= 0.99
    a1_not_greater = all(
        final["A1"][pair]["storage_read_bytes"] <= final["S1"][pair]["storage_read_bytes"]
        for pair in sorted(final["A1"])
    )
    return {
        "capture": file_identity(capture_path),
        "configuration": capture,
        "cells": cells,
        "a1_storage_bytes_not_greater_than_s1": a1_not_greater,
    }


def mode_c_summary(directory: Path) -> dict[str, object]:
    cells = raw_runs(directory)
    result = {cell: next(iter(runs.values())) for cell, runs in cells.items()}
    required = {"S0", "S1", "D1", "A1"}
    identities = {result[cell]["numerical_identity_sha256"] for cell in required}
    return {
        "capture": file_identity(directory / "matrix.json"),
        "cells": result,
        "required_cells_present": set(result) == required,
        "exact_across_topologies": len(identities) == 1,
        "all_workers_two": all(result[cell]["worker_count"] == 2 for cell in required),
        "all_terminal_state_zero": all(result[cell]["terminal_state_zero"] for cell in required),
    }


def capacity_summary(root: Path, paired: dict[str, object]) -> dict[str, object]:
    controls: dict[str, object] = {
        "16_gib": {
            cell: paired["cells"][cell]["final_runs"] for cell in ("S0", "A1")
        }
    }
    for name in ("32_gib", "64_gib", "full"):
        controls[name] = {
            cell: list(runs.values()) for cell, runs in raw_runs(root / name).items()
        }
        controls[name]["capture"] = file_identity(root / name / "matrix.json")
    full_s0 = controls["full"]["S0"][0]
    zero_storage = (
        full_s0["cold_prewarm"]["completed"] and
        full_s0["cold_prewarm"]["measured_read_requests"] == 0 and
        full_s0["cold_prewarm"]["measured_read_bytes"] == 0 and
        full_s0["storage_read_requests"] == 0 and
        full_s0["storage_read_bytes"] == 0
    )
    return {"controls": controls, "s0_full_cold_zero_storage_gate": zero_storage}


def profile_policy_summary(profiles: dict[str, object]) -> dict[str, object]:
    process = profiles["S0"]["process"]["attribution"]
    buckets = process["buckets"]
    policy_fraction = (
        buckets["cold_cache_policy_victim"]["fraction"] +
        buckets["hot_cache_policy_victim"]["fraction"]
    )
    return {
        "s0_process_policy_bucket_fraction": policy_fraction,
        "five_percent_threshold": 0.05,
        "policy_bucket_at_or_above_threshold": policy_fraction >= 0.05,
        "top_inclusive_functions": process["top_inclusive_functions"],
        "decision": "OBSERVED after the structural change; optimization is conditional, not a completion gate",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode-c-dir", type=Path, required=True)
    parser.add_argument("--paired-dir", type=Path, required=True)
    parser.add_argument("--capacity-root", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--ctest-log", type=Path, required=True)
    parser.add_argument("--model-verification", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--baseline-nested-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    mode_c = mode_c_summary(args.mode_c_dir)
    paired = paired_summary(args.paired_dir)
    capacity = capacity_summary(args.capacity_root, paired)
    profiles = profile_summary(args.profile_dir)
    traces = trace_summary(args.trace_dir, args.trace_processor)
    provider_final = traces["cells"]["S0"]["provider_p50_ms"]
    provider_ratio = provider_final / BASELINE_PROVIDER_P50_MS
    provider_gate = provider_ratio <= 0.80
    profile_policy = profile_policy_summary(profiles)
    errors: list[str] = []
    if not mode_c["required_cells_present"] or not mode_c["exact_across_topologies"]:
        errors.append("Mode-C S0/S1/D1/A1 is incomplete or not exact")
    if not mode_c["all_workers_two"] or not mode_c["all_terminal_state_zero"]:
        errors.append("Mode-C worker or terminal-state gate failed")
    for cell in ("S0", "S1", "A1"):
        if not paired["cells"][cell]["gate"]:
            errors.append(f"{cell} paired throughput gate failed")
        if not paired["cells"][cell]["exact_generated_identity"]:
            errors.append(f"{cell} paired generated identity differs")
        if not paired["cells"][cell]["all_terminal_state_zero"]:
            errors.append(f"{cell} paired terminal state is not zero")
    if not paired["a1_storage_bytes_not_greater_than_s1"]:
        errors.append("A1 issued more backing bytes than S1")
    if not capacity["s0_full_cold_zero_storage_gate"]:
        errors.append("full-cold S0 zero-storage gate failed")
    if not provider_gate:
        errors.append("matched S0 provider p50 did not improve by at least 20 percent")

    result = {
        "schema_version": "issue69-checkpoint-c-manifest-v1",
        "status": "pass" if not errors else "fail",
        "checkpoint": "C_FINAL_CAPABLE",
        "final_capable": True,
        "revisions": {
            "project_head": args.project_head,
            "nested_head": args.nested_head,
            "baseline_nested_head": args.baseline_nested_head,
        },
        "fixture": {
            "model_revision": "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1",
            "model_verification": file_identity(args.model_verification),
            "io_workers": 2,
            "cuda_architectures": "86",
        },
        "mode_c": mode_c,
        "paired_performance": paired,
        "capacity_controls": capacity,
        "profiles": profiles,
        "post_structural_policy_decision": profile_policy,
        "traces": traces,
        "provider_wall_gate": {
            "baseline_s0_p50_ms": BASELINE_PROVIDER_P50_MS,
            "final_s0_p50_ms": provider_final,
            "final_over_baseline": provider_ratio,
            "required_maximum_ratio": 0.80,
            "gate": provider_gate,
        },
        "validation": {"focused_ctest_log": file_identity(args.ctest_log)},
        "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "paired": {cell: paired["cells"][cell]["geometric_mean_ratio"]
                   for cell in ("S0", "S1", "A1")},
        "provider_ratio": provider_ratio,
        "zero_storage": capacity["s0_full_cold_zero_storage_gate"],
        "errors": errors,
    }, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build the issue 73 matched CPU/GPU/hot-VRAM causal comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import write_json


def role_ordinals(roles: str) -> list[int]:
    return [int(item.split(":", 1)[0]) for item in roles.split(",") if item]


def aggregate_service_per_token(case: dict[str, object], path: tuple[str, ...]) -> float:
    total = 0
    generated = 0
    for run in case["runs"]:
        value: object = run
        for key in path:
            value = value.get(key, {}) if isinstance(value, dict) else {}
        total += int(value) if isinstance(value, (int, float)) else 0
        generated += int(run["generated_tokens"])
    return total / generated


def cell_summary(case: dict[str, object], execution: str) -> dict[str, object]:
    pooled = case["pooled"]
    runs = case["runs"]
    gpu_rows: dict[int, dict[str, float]] = {}
    for run in runs:
        for gpu in run["resources"]["gpus"]:
            ordinal = int(gpu["cuda_ordinal"])
            row = gpu_rows.setdefault(ordinal, {
                "memory_used_max_mib": 0.0, "memory_free_min_mib": float("inf")})
            row["memory_used_max_mib"] = max(
                row["memory_used_max_mib"], float(gpu["memory_used_max_mib"]))
            row["memory_free_min_mib"] = min(
                row["memory_free_min_mib"], float(gpu["memory_free_min_mib"]))
    first = runs[0]
    capacities = first["capacities"]
    return {
        "execution": execution,
        "processes": pooled["processes"],
        "decode_tps": pooled["decode_tps"],
        "ttft_us": pooled["ttft_us"],
        "decode_latency_us": pooled["decode_latency_us"],
        "hot": pooled["hot"], "cold": pooled["cold"],
        "hot_requested_slots": capacities["hot_requested_slots"],
        "hot_effective_slots": capacities["hot_effective_slots"],
        "h2d_bytes_per_generated_token": pooled["bytes_per_generated_token"]["h2d"],
        "h2d_service_us_per_generated_token": aggregate_service_per_token(
            case, ("transfer", "h2d_time_us")),
        "logical_storage_bytes_per_generated_token":
            pooled["bytes_per_generated_token"]["logical_storage"],
        "guest_block_bytes_per_generated_token":
            pooled["bytes_per_generated_token"]["guest_block"],
        "storage_queue_wait_us_per_generated_token": aggregate_service_per_token(
            case, ("async_io", "diagnostics", "read_queue_wait_us")),
        "process_rss_max_kib": max(
            run["resources"]["process_rss_max_kib"] for run in runs),
        "gpus": [
            {"cuda_ordinal": ordinal, **row}
            for ordinal, row in sorted(gpu_rows.items())
        ],
        "fallback": {
            "pageable_transfer": any(
                bool(run["transfer"].get("pageable_fallback")) for run in runs),
            "buffered_io_operations": sum(
                int(run["async_io"].get("diagnostics", {}).get(
                    "buffered_fallback_operations", 0)) for run in runs),
            "direct_unsupported_sources": sum(
                int(run["storage"].get("direct_unsupported_source_count", 0)) for run in runs),
        },
        "swap_empty_all_processes": pooled["swap_empty_all_processes"],
        "oom_kill_delta": pooled["oom_kill_delta"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-case", default="CPU_CONTROL")
    parser.add_argument("--gpu-hot-0-case", default="K3_INITIAL")
    parser.add_argument("--gpu-hot-max-case", default="GPU_HOT_MAX")
    args = parser.parse_args()

    source = json.loads(args.summary.read_text())
    if source.get("status") != "pass" or not source.get(
            "identity", {}).get("production_output_exact_across_all_processes"):
        raise SystemExit("matched controls require a passing exact-output matrix summary")
    cases = source["cases"]
    cpu = cases[args.cpu_case]
    gpu0 = cases[args.gpu_hot_0_case]
    gpu_max = cases[args.gpu_hot_max_case]
    if (cpu["miss_policy"] != "CPU_FALLBACK" or
            gpu0["miss_policy"] != "PROMOTE_AND_GPU" or
            gpu_max["miss_policy"] != "PROMOTE_AND_GPU"):
        raise SystemExit("matched controls have inconsistent miss policies")
    if (role_ordinals(cpu["roles"]) != [0] or role_ordinals(gpu0["roles"]) != [0] or
            role_ordinals(gpu_max["roles"]) != [0] or
            len({case["n_gpu_layers"] for case in (cpu, gpu0, gpu_max)}) != 1 or
            len({case["n_ubatch"] for case in (cpu, gpu0, gpu_max)}) != 1):
        raise SystemExit("matched controls are not the same T1 workload shape")
    if gpu0["pooled"]["hot"]["hits"] != 0:
        raise SystemExit("GPU_HOT_0 reuse is not effectively disabled")

    cells = {
        "CPU_CONTROL": cell_summary(cpu, "CPU_FALLBACK_HOST"),
        "GPU_HOT_0": cell_summary(gpu0, "PROMOTE_AND_GPU_NO_HOT_HITS"),
        "GPU_HOT_MAX": cell_summary(gpu_max, "PROMOTE_AND_GPU_MAX_SAFE_HOT"),
    }
    cpu_tps = cells["CPU_CONTROL"]["decode_tps"]
    gpu0_tps = cells["GPU_HOT_0"]["decode_tps"]
    gpu_max_tps = cells["GPU_HOT_MAX"]["decode_tps"]
    result = {
        "schema_version": "issue73-matched-controls-v1", "status": "pass",
        "source_summary": str(args.summary),
        "output_identity": source["identity"], "cells": cells,
        "ratios": {
            "gpu_hot_0_over_cpu_control": gpu0_tps / cpu_tps,
            "gpu_hot_max_over_gpu_hot_0": gpu_max_tps / gpu0_tps,
            "gpu_hot_max_over_cpu_control": gpu_max_tps / cpu_tps,
        },
        "attribution_boundary": {
            "service_counters_are_not_exposed_stall": True,
            "exposed_storage_h2d_and_compute_time": "DEFERRED_TO_MATCHED_P_TRACE",
        },
    }
    write_json(args.output, result)
    print("ISSUE73_MATCHED_CONTROLS status=pass "
          f"gpu0_cpu={result['ratios']['gpu_hot_0_over_cpu_control']:.6f} "
          f"hotmax_gpu0={result['ratios']['gpu_hot_max_over_gpu_hot_0']:.6f}")


if __name__ == "__main__":
    main()

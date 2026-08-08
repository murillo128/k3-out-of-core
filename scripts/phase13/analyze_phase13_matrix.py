#!/usr/bin/env python3
"""Validate identity and summarize the fixed Phase 13 A/B matrix."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--cells", default="A,B")
    return parser.parse_args()


def load_gzip(path: Path) -> dict:
    with gzip.open(path, "rt") as stream:
        return json.load(stream)


def nearest_rank(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile*len(ordered)) - 1)]


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile*(len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (position - lower)*(ordered[upper] - ordered[lower])


def identity(run: dict) -> dict:
    return {
        "prompt_ids": run["prompt_ids"],
        "generated_ids": run["generated_ids"],
        "generated_text": run["generated_text"],
        "logits_fnv64": run["logits_fnv64"],
        "routes": run["routes"],
    }


def identity_sha(value: dict) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def resource_summary(path: Path) -> dict:
    data = json.loads(path.read_text())
    gpu: dict[int, dict[str, list[float]]] = {}
    vm_hwm: list[int] = []
    vm_pin: list[int] = []
    mem_available: list[int] = []
    for sample in data["samples"]:
        process = sample["process"]
        host = sample["host"]
        if "VmHWM" in process:
            vm_hwm.append(process["VmHWM"])
        if "VmPin" in process:
            vm_pin.append(process["VmPin"])
        if "MemAvailable" in host:
            mem_available.append(host["MemAvailable"])
        for item in sample["gpus"]:
            ordinal = item["cuda_ordinal"]
            fields = gpu.setdefault(ordinal, {
                "gpu_utilization_percent": [], "memory_used_mib": [], "power_watts": []})
            fields["gpu_utilization_percent"].append(item["gpu_utilization_percent"])
            fields["memory_used_mib"].append(item["memory_used_mib"])
            if item["power_watts"] is not None:
                fields["power_watts"].append(item["power_watts"])
    gpu_result = []
    for ordinal, fields in sorted(gpu.items()):
        gpu_result.append({
            "cuda_ordinal": ordinal,
            "gpu_utilization_mean_percent": statistics.fmean(fields["gpu_utilization_percent"]),
            "gpu_utilization_max_percent": max(fields["gpu_utilization_percent"]),
            "memory_used_max_mib": max(fields["memory_used_mib"]),
            "power_max_watts": max(fields["power_watts"]) if fields["power_watts"] else None,
        })
    return {
        "elapsed_seconds": data["elapsed_seconds"],
        "sample_count": len(data["samples"]),
        "process_vm_hwm_max_kib": max(vm_hwm, default=0),
        "process_vm_pin_max_kib": max(vm_pin, default=0),
        "host_mem_available_min_kib": min(mem_available, default=0),
        "gpus": gpu_result,
    }


def summarize_run(run: dict, resources: dict) -> dict:
    latencies = run["latency_us"]
    decode = latencies[1:]
    devices = run["multi_gpu"]["devices"]
    hot_hits = sum(item["hot_hits"] for item in devices)
    hot_misses = sum(item["hot_misses"] for item in devices)
    h2d_bytes = sum(item["h2d_bytes"] for item in devices)
    first = [item["ring_first_h2d_enqueue_us"] for item in devices if item["ring_first_h2d_enqueue_us"]]
    last = [item["ring_last_h2d_complete_us"] for item in devices if item["ring_last_h2d_complete_us"]]
    overlap_us = max(0, min(last) - max(first)) if len(first) == len(devices) and len(last) == len(devices) else 0
    return {
        "status": run["status"],
        "generated_tokens": len(run["generated_ids"]),
        "ttft_us": latencies[0],
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode)/(sum(decode)/1_000_000),
        "token_latency_us": {
            "p50": nearest_rank(decode, 0.50), "p95": nearest_rank(decode, 0.95),
            "p99": nearest_rank(decode, 0.99), "max": max(decode),
        },
        "hot_hits": hot_hits,
        "hot_misses": hot_misses,
        "hot_hit_rate": hot_hits/(hot_hits + hot_misses),
        "h2d_bytes": h2d_bytes,
        "h2d_bytes_per_generated_token": h2d_bytes/len(run["generated_ids"]),
        "storage": {
            key: run["storage"][key] for key in (
                "read_bytes", "read_operations", "read_requests", "io_errors", "short_reads",
                "cancelled_reads", "integrity_mismatches")
        },
        "peer_host_staged_bytes": sum(item["host_staged_bytes"] for item in run["multi_gpu"]["peer_diagnostics"]),
        "peer_bytes": sum(item["peer_bytes"] for item in run["multi_gpu"]["peer_diagnostics"]),
        "physical_feasibility_skips": run["multi_gpu"]["physical_feasibility_skips"],
        "device_h2d_overlap_us": overlap_us,
        "device_diagnostics": devices,
        "lifecycle": run["lifecycle"],
        "resources": resources,
    }


def pooled(cell_runs: list[dict]) -> dict:
    decode = [value for run in cell_runs for value in run["latency_us"][1:]]
    devices = [device for run in cell_runs for device in run["multi_gpu"]["devices"]]
    hits = sum(device["hot_hits"] for device in devices)
    misses = sum(device["hot_misses"] for device in devices)
    h2d = sum(device["h2d_bytes"] for device in devices)
    generated = sum(len(run["generated_ids"]) for run in cell_runs)
    return {
        "processes": len(cell_runs),
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode)/(sum(decode)/1_000_000),
        "ttft_us": {
            "p50": nearest_rank([run["latency_us"][0] for run in cell_runs], 0.50),
            "p95": nearest_rank([run["latency_us"][0] for run in cell_runs], 0.95),
        },
        "token_latency_us": {
            "p50": nearest_rank(decode, 0.50), "p95": nearest_rank(decode, 0.95),
            "p99": nearest_rank(decode, 0.99), "max": max(decode),
        },
        "hot_hits": hits,
        "hot_misses": misses,
        "hot_hit_rate": hits/(hits + misses),
        "h2d_bytes": h2d,
        "h2d_bytes_per_generated_token": h2d/generated,
    }


def paired_interval(a_runs: list[dict], b_runs: list[dict]) -> tuple[list[float], list[float]]:
    ratios = [sum(a["latency_us"][1:])/sum(b["latency_us"][1:]) for a, b in zip(a_runs, b_runs)]
    rng = random.Random(61)
    bootstrap: list[float] = []
    count = len(a_runs)
    for _ in range(100_000):
        indices = [rng.randrange(count) for _ in range(count)]
        a_wall = sum(sum(a_runs[index]["latency_us"][1:]) for index in indices)
        b_wall = sum(sum(b_runs[index]["latency_us"][1:]) for index in indices)
        bootstrap.append(a_wall/b_wall)
    return ratios, [percentile(bootstrap, 0.025), percentile(bootstrap, 0.975)]


def main() -> None:
    args = parse_args()
    cells = [cell.strip() for cell in args.cells.split(",") if cell.strip()]
    runs: dict[str, list[dict]] = {cell: [] for cell in cells}
    summaries: dict[str, list[dict]] = {cell: [] for cell in cells}
    baseline_identity = None
    digests: list[str] = []
    for pair in range(1, args.pairs + 1):
        for cell in cells:
            stem = f"{cell}-{pair:02d}"
            run = load_gzip(args.input_dir / "raw" / f"{stem}.json.gz")
            if run.get("status") != "pass" or len(run.get("generated_ids", [])) != 24:
                raise SystemExit(f"{stem}: incomplete evidence")
            current_identity = identity(run)
            if baseline_identity is None:
                baseline_identity = current_identity
            if current_identity != baseline_identity:
                raise SystemExit(f"{stem}: exact output identity mismatch")
            digests.append(identity_sha(current_identity))
            resources = resource_summary(args.input_dir / "raw" / f"{stem}-resources.json")
            runs[cell].append(run)
            summaries[cell].append(summarize_run(run, resources))

    pooled_cells = {cell: pooled(values) for cell, values in runs.items()}
    result: dict[str, object] = {
        "schema_version": "phase13-matrix-summary-v1",
        "status": "pass",
        "fixture_transport": "POSITIONAL",
        "cache_state": "PROVIDER_COLD_OS_TMPFS_RESIDENT",
        "identity": {
            "exact_across_all_processes": len(set(digests)) == 1,
            "sha256": digests[0],
            "processes": len(digests),
            "route_records_per_process": len(baseline_identity["routes"]),
            "generated_tokens_per_process": len(baseline_identity["generated_ids"]),
        },
        "cells": pooled_cells,
        "runs": summaries,
    }
    if "A" in runs and "B" in runs:
        ratios, interval = paired_interval(runs["A"], runs["B"])
        speedup = pooled_cells["B"]["decode_tps"]/pooled_cells["A"]["decode_tps"]
        classification = (
            "SCALING_POSITIVE" if interval[0] > 1.0 else
            "SCALING_NEGATIVE" if interval[1] < 1.0 else "SCALING_INCONCLUSIVE")
        hit_delta = pooled_cells["B"]["hot_hit_rate"] - pooled_cells["A"]["hot_hit_rate"]
        h2d_change = (
            pooled_cells["B"]["h2d_bytes_per_generated_token"] /
            pooled_cells["A"]["h2d_bytes_per_generated_token"] - 1.0)
        result["scaling"] = {
            "single_gpu_tps": pooled_cells["A"]["decode_tps"],
            "dual_gpu_tps": pooled_cells["B"]["decode_tps"],
            "speedup": speedup,
            "efficiency": speedup/2,
            "paired_process_speedups": ratios,
            "paired_bootstrap_95_percent_interval": interval,
            "bootstrap_resamples": 100_000,
            "classification": classification,
        }
        result["capacity_matched_trigger"] = {
            "hot_hit_rate_delta_percentage_points": hit_delta*100,
            "expert_h2d_bytes_per_token_relative_change": h2d_change,
            "trigger_hot_hit_rate": abs(hit_delta) > 0.05,
            "trigger_h2d_bytes_per_token": abs(h2d_change) > 0.10,
            "required": abs(hit_delta) > 0.05 or abs(h2d_change) > 0.10,
        }
    if "A" in runs and "Bprime" in runs:
        ratios, interval = paired_interval(runs["A"], runs["Bprime"])
        speedup = pooled_cells["Bprime"]["decode_tps"]/pooled_cells["A"]["decode_tps"]
        result["capacity_matched_comparator"] = {
            "single_gpu_tps": pooled_cells["A"]["decode_tps"],
            "capacity_matched_dual_gpu_tps": pooled_cells["Bprime"]["decode_tps"],
            "speedup": speedup,
            "efficiency": speedup/2,
            "paired_process_speedups": ratios,
            "paired_bootstrap_95_percent_interval": interval,
            "bootstrap_resamples": 100_000,
            "hot_hit_rate_delta_percentage_points": (
                pooled_cells["Bprime"]["hot_hit_rate"] - pooled_cells["A"]["hot_hit_rate"])*100,
            "expert_h2d_bytes_per_token_relative_change": (
                pooled_cells["Bprime"]["h2d_bytes_per_generated_token"] /
                pooled_cells["A"]["h2d_bytes_per_generated_token"] - 1.0),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "status": result["status"], "identity": result["identity"],
        "scaling": result.get("scaling"), "capacity_matched_trigger": result.get("capacity_matched_trigger"),
        "capacity_matched_comparator": result.get("capacity_matched_comparator")},
        indent=2))


if __name__ == "__main__":
    main()

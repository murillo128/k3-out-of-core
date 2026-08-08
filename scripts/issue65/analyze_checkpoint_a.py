#!/usr/bin/env python3
"""Validate and summarize issue 65 Checkpoint A evidence."""

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
    return parser.parse_args()


def load(path: Path) -> dict:
    with gzip.open(path, "rt") as stream:
        return json.load(stream)


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def identity(run: dict) -> dict:
    return {
        "prompt_ids": run["prompt_ids"],
        "generated_ids": run["generated_ids"],
        "generated_text": run["generated_text"],
    }


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def summarize(run: dict) -> dict:
    decode = run["latency_us"][1:]
    return {
        "status": run["status"],
        "identity_sha256": digest(identity(run)),
        "ttft_us": run["latency_us"][0],
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode) / (sum(decode) / 1_000_000),
        "decode_latency_us": {
            "p50": percentile(decode, 0.50),
            "p95": percentile(decode, 0.95),
            "p99": percentile(decode, 0.99),
        },
        "expert_roles": run["expert_roles"],
        "role_path_structure": run["role_path_structure"],
        "capacities": run["capacities"],
        "mechanism": run["mechanism"],
        "storage": run["storage"],
        "transfer": run["transfer"],
        "high_water": run["high_water"],
        "cold_residency": run["cold_residency"],
        "lifecycle": run["lifecycle"],
        "device_diagnostics": run["multi_gpu"]["devices"],
        "peer_diagnostics": run["multi_gpu"]["peer_diagnostics"],
        "peak_rss_kib": run["peak_rss_kib"],
        "cpu_user_time_us": run["cpu_user_time_us"],
        "cpu_system_time_us": run["cpu_system_time_us"],
    }


def resource_summary(path: Path) -> dict:
    data = json.loads(path.read_text())
    hwm = [sample["process"].get("VmHWM", 0) for sample in data["samples"]]
    pin = [sample["process"].get("VmPin", 0) for sample in data["samples"]]
    available = [sample["host"].get("MemAvailable", 0) for sample in data["samples"]]
    gpu_memory: dict[int, list[float]] = {}
    for sample in data["samples"]:
        for gpu in sample["gpus"]:
            gpu_memory.setdefault(gpu["cuda_ordinal"], []).append(gpu["memory_used_mib"])
    return {
        "elapsed_seconds": data["elapsed_seconds"],
        "process_vm_hwm_max_kib": max(hwm, default=0),
        "process_vm_pin_max_kib": max(pin, default=0),
        "host_mem_available_min_kib": min(available, default=0),
        "gpu_memory_used_max_mib": {
            str(key): max(values) for key, values in sorted(gpu_memory.items())
        },
    }


def pooled(runs: list[dict]) -> dict:
    decode = [latency for run in runs for latency in run["latency_us"][1:]]
    hot_hits = sum(run["mechanism"]["hot_hits"] for run in runs)
    hot_misses = sum(run["mechanism"]["hot_misses"] for run in runs)
    cold_hits = sum(run["mechanism"]["cold_hits"] for run in runs)
    cold_misses = sum(run["mechanism"]["cold_misses"] for run in runs)
    return {
        "processes": len(runs),
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode) / (sum(decode) / 1_000_000),
        "ttft_us": {
            "p50": percentile([run["latency_us"][0] for run in runs], 0.50),
            "p95": percentile([run["latency_us"][0] for run in runs], 0.95),
        },
        "decode_latency_us": {
            "p50": percentile(decode, 0.50),
            "p95": percentile(decode, 0.95),
            "p99": percentile(decode, 0.99),
        },
        "hot_hits": hot_hits,
        "hot_misses": hot_misses,
        "hot_hit_rate": hot_hits / (hot_hits + hot_misses),
        "cold_hits": cold_hits,
        "cold_misses": cold_misses,
        "cold_hit_rate": cold_hits / (cold_hits + cold_misses),
        "h2d_bytes": sum(run["transfer"]["h2d_bytes"] for run in runs),
        "storage_read_bytes": sum(run["storage"]["read_bytes"] for run in runs),
        "useful_prefetches": sum(run["mechanism"]["background_useful"] for run in runs),
        "wasted_prefetches": sum(run["mechanism"]["background_wasted"] for run in runs),
    }


def main() -> None:
    args = parse_args()
    runs: dict[str, list[dict]] = {"legacy": [], "explicit": []}
    summaries: dict[str, list[dict]] = {"legacy": [], "explicit": []}
    resources: dict[str, list[dict]] = {"legacy": [], "explicit": []}
    expected_identity = None
    expected_structure = None
    for pair in range(1, args.pairs + 1):
        for role in ("legacy", "explicit"):
            stem = f"{role}-{pair:02d}"
            run = load(args.input_dir / "raw" / f"{stem}.json.gz")
            if run.get("status") != "pass" or len(run.get("generated_ids", [])) != 24:
                raise SystemExit(f"{stem}: incomplete evidence")
            if run.get("expert_runtime_mode") != "PRODUCTION_PERFORMANCE":
                raise SystemExit(f"{stem}: wrong runtime mode")
            if run.get("role_config_source") != role.upper():
                raise SystemExit(f"{stem}: wrong role configuration source")
            if run.get("routes") or run["async_io"]["diagnostics"]["trace_capacity"] != 0:
                raise SystemExit(f"{stem}: compliance instrumentation leaked into Mode P")
            current_identity = identity(run)
            if expected_identity is None:
                expected_identity = current_identity
            elif current_identity != expected_identity:
                raise SystemExit(f"{stem}: generated identity mismatch")
            if expected_structure is None:
                expected_structure = run.get("role_path_structure")
            elif run.get("role_path_structure") != expected_structure:
                raise SystemExit(f"{stem}: local role path structure mismatch")
            resolved = run.get("expert_roles", {})
            if resolved.get("shape") != "LOCAL_SINGLE" or resolved.get("total_hot_slots") != 268:
                raise SystemExit(f"{stem}: unexpected resolved role plan")
            if (role == "explicit") != bool(resolved.get("explicit")):
                raise SystemExit(f"{stem}: explicit role marker mismatch")
            devices = run.get("multi_gpu", {}).get("devices", [])
            if len(devices) != 1 or devices[0].get("hot_requested_slots") != 268 or \
                    devices[0].get("hot_effective_slots") != 268:
                raise SystemExit(f"{stem}: provider did not construct exact requested capacity")
            lifecycle = run.get("lifecycle", {})
            if any(lifecycle.get(key, 0) != 0 for key in (
                    "active_background_flights", "current_hot_pins", "cold_current_transfer_refs",
                    "cold_current_request_refs", "cold_current_cpu_execution_refs")):
                raise SystemExit(f"{stem}: non-terminal provider lifecycle")
            runs[role].append(run)
            summaries[role].append(summarize(run))
            resources[role].append(resource_summary(
                args.input_dir / "raw" / f"{stem}-resources.json"))

    ratios = [
        sum(legacy["latency_us"][1:]) / sum(explicit["latency_us"][1:])
        for legacy, explicit in zip(runs["legacy"], runs["explicit"])
    ]
    rng = random.Random(65)
    bootstrap = []
    for _ in range(100_000):
        indices = [rng.randrange(args.pairs) for _ in range(args.pairs)]
        legacy_wall = sum(sum(runs["legacy"][i]["latency_us"][1:]) for i in indices)
        explicit_wall = sum(sum(runs["explicit"][i]["latency_us"][1:]) for i in indices)
        bootstrap.append(legacy_wall / explicit_wall)
    paired_geometric_mean = statistics.geometric_mean(ratios)
    interval = [percentile(bootstrap, 0.025), percentile(bootstrap, 0.975)]
    pooled_result = {role: pooled(role_runs) for role, role_runs in runs.items()}
    gate_pass = paired_geometric_mean >= 0.99 and interval[1] >= 0.99
    result = {
        "schema_version": "issue65-checkpoint-a-analysis-v1",
        "status": "pass" if gate_pass else "fail",
        "acceptance": {
            "metric": "geometric-mean paired explicit/legacy decode throughput",
            "minimum_ratio": 0.99,
            "observed_ratio": paired_geometric_mean,
            "observed_regression_fraction": 1 - paired_geometric_mean,
            "interval_does_not_establish_regression_over_one_percent": interval[1] >= 0.99,
            "pass": gate_pass,
        },
        "identity_sha256": digest(expected_identity),
        "identity_exact_across_processes": True,
        "processes": args.pairs * 2,
        "generated_tokens_per_process": 24,
        "role_path_structure_sha256": digest(expected_structure),
        "role_path_structure": expected_structure,
        "paired_explicit_speedups": ratios,
        "paired_geometric_mean_explicit_speedup": paired_geometric_mean,
        "paired_bootstrap_95_percent_interval": interval,
        "bootstrap_resamples": 100_000,
        "pooled": pooled_result,
        "runs": summaries,
        "resources": resources,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if result["status"] != "pass":
        raise SystemExit("Checkpoint A performance gate failed")


if __name__ == "__main__":
    main()

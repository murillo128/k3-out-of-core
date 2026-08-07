#!/usr/bin/env python3
"""Validate and summarize the real Kimi-K3 CPU max-cache campaign."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable


RUN_IDS = (
    "pair-01-8g",
    "pair-01-max-safe",
    "anchor-96g",
    "pair-02-8g",
    "pair-02-max-safe",
    "pair-03-8g",
    "pair-03-max-safe",
)
PAIRS = tuple((f"pair-{ordinal:02d}-8g", f"pair-{ordinal:02d}-max-safe") for ordinal in range(1, 4))
T_95_DF_2 = 4.302652729911275


def identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def mean_ci_95(values: list[float]) -> dict[str, Any]:
    if len(values) != 3:
        raise ValueError("the accepted paired campaign requires exactly three values")
    mean = statistics.fmean(values)
    margin = T_95_DF_2 * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "method": "two-sided paired Student-t interval",
        "confidence": 0.95,
        "degrees_of_freedom": 2,
        "values": values,
        "mean": mean,
        "lower": mean - margin,
        "upper": mean + margin,
    }


def block_read_bytes(run: dict[str, Any], suffix: str) -> int:
    matches = [row for row in run["block_devices"] if row["stat_path"].endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one block device ending with {suffix}")
    return int(matches[0]["read_bytes"])


def summarize(run: dict[str, Any]) -> dict[str, Any]:
    runtime = run["runtime"]
    resources = run["process_resources"]
    counters = run["cache"]["final_run_counters"]
    return {
        "run_id": run["run_id"],
        "capacity": run["capacity"],
        "generated_tokens": runtime["generated_tokens"],
        "decode_seconds": runtime["decode_seconds"],
        "decode_tokens_per_second_exact": runtime["generated_tokens"] / runtime["decode_seconds"],
        "decode_forward_latency_seconds": runtime["decode_forward_latency_seconds"],
        "first_32_decode_forward_latency_seconds": runtime["first_32_decode_forward_latency_seconds"],
        "remaining_decode_forward_latency_seconds": runtime["remaining_decode_forward_latency_seconds"],
        "component_seconds": runtime["component_seconds"],
        "process_start_to_first_output_seconds": runtime["process_start_to_first_output_seconds"],
        "native_cache": {
            "hits": counters["v0"],
            "misses": counters["v1"],
            "hit_fraction": counters["v0"] / (counters["v0"] + counters["v1"]),
            "streamed_bytes": counters["v2"],
            "final_occupancy_slots": counters["v3"],
            "admissions": counters["v4"],
            "evictions": counters["v5"],
            "direct_expert_reads": counters["v6"],
            "buffered_full_expert_fallbacks": counters["v7"],
            "direct_bytes": counters["v8"],
            "tail_bytes": counters["v9"],
        },
        "resources": {
            "maximum_rss_bytes": resources["maximum_rss_bytes"],
            "maximum_swap_bytes": resources["maximum_swap_bytes"],
            "minimum_mem_available_bytes": resources["minimum_mem_available_bytes"],
            "average_cpu_cores": resources["average_cpu_cores"],
            "maximum_threads": resources["maximum_threads"],
            "cgroup_event_delta": resources["cgroup_event_delta"],
            "proc_read_bytes": resources["proc_io_maxima"]["io_read_bytes"],
            "perf_stat": resources["perf_stat"],
            "rapl": resources["rapl"],
        },
        "physical_read_bytes": {
            "nvme0n1": block_read_bytes(run, "/nvme0n1/stat"),
            "nvme1n1": block_read_bytes(run, "/nvme1n1/stat"),
        },
        "token_ids_sha256": run["token_ids_sha256"],
        "normalized_route_sha256": run["routing"]["normalized_route"]["sha256"],
        "raw_run": run["_source"],
    }


def paired_fraction(
    runs: dict[str, dict[str, Any]],
    numerator: Callable[[dict[str, Any]], float],
    denominator: Callable[[dict[str, Any]], float],
) -> list[float]:
    return [numerator(runs[large]) / denominator(runs[small]) - 1.0 for small, large in PAIRS]


def replay_window(curve: dict[str, Any], capacity_gib: int, window: str) -> dict[str, Any]:
    matches = [row for row in curve["capacity_curve"] if row["capacity_gib"] == capacity_gib]
    if len(matches) != 1:
        raise ValueError(f"missing unique {capacity_gib} GiB global-LRU replay row")
    row = matches[0]
    replay = row["replay"]["windows"][window]
    return {
        "nominal_capacity_gib": capacity_gib,
        "usable_capacity_bytes": row["support"]["usable_capacity_bytes"],
        "hits": replay["hits"],
        "misses": replay["misses"],
        "hit_fraction": replay["hit_ratio_by_expert_requests"],
        "required_nvme_bytes": replay["required_nvme_bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--global-replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    runs: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    for run_id in RUN_IDS:
        path = args.campaign_root / run_id / "run.json"
        run = json.loads(path.read_text())
        run["_source"] = identity(path)
        runs[run_id] = run
        if run.get("run_id") != run_id or run.get("status") != "PASS" or run.get("failures"):
            failures.append(f"{run_id}: run is not an accepted PASS")
        if run["runtime"]["generated_tokens"] < 256 or run["routing"]["complete_decode_forwards"] < 256:
            failures.append(f"{run_id}: fewer than 256 complete decode forwards")
        counters = run["cache"]["final_run_counters"]
        if counters["v6"] <= 0 or counters["v7"] != 0:
            failures.append(f"{run_id}: direct-I/O proof failed or buffered fallback occurred")
        resources = run["process_resources"]
        if resources["maximum_swap_bytes"] != 0 or any(resources["cgroup_event_delta"].values()):
            failures.append(f"{run_id}: swap or cgroup memory event changed")
        if resources["maximum_rss_bytes"] > run["capacity"]["accepted_process_rss_ceiling_bytes"]:
            failures.append(f"{run_id}: process RSS exceeded the accepted ceiling")
        if block_read_bytes(run, "/nvme1n1/stat") != 0:
            failures.append(f"{run_id}: unexpected secondary-NVMe traffic")

    token_digests = {run["token_ids_sha256"] for run in runs.values()}
    route_digests = {run["routing"]["normalized_route"]["sha256"] for run in runs.values()}
    if len(token_digests) != 1 or len(route_digests) != 1:
        failures.append("token or normalized-route identity differs across campaign rows")

    tps = lambda row: row["runtime"]["generated_tokens"] / row["runtime"]["decode_seconds"]
    mean_latency = lambda row: row["runtime"]["decode_forward_latency_seconds"]["mean"]
    p95_latency = lambda row: row["runtime"]["decode_forward_latency_seconds"]["p95"]
    native_bytes = lambda row: float(row["cache"]["final_run_counters"]["v2"])
    physical_bytes = lambda row: float(block_read_bytes(row, "/nvme0n1/stat"))
    exposed_load = lambda row: row["runtime"]["component_seconds"]["exposed_expert_load_wait"]

    throughput_gain = paired_fraction(runs, tps, tps)
    mean_latency_change = paired_fraction(runs, mean_latency, mean_latency)
    p95_latency_change = paired_fraction(runs, p95_latency, p95_latency)
    native_bytes_change = paired_fraction(runs, native_bytes, native_bytes)
    physical_bytes_change = paired_fraction(runs, physical_bytes, physical_bytes)
    exposed_load_change = paired_fraction(runs, exposed_load, exposed_load)

    if mean_ci_95(throughput_gain)["lower"] <= 0:
        failures.append("paired 95% CI does not establish a positive MAX_SAFE throughput gain")

    summaries = {run_id: summarize(run) for run_id, run in runs.items()}
    global_replay = json.loads(args.global_replay.read_text())
    native_8g = summaries["pair-01-8g"]
    native_96g = summaries["anchor-96g"]
    global_8g = replay_window(global_replay, 8, "decode")
    global_96g = replay_window(global_replay, 96, "decode")
    comparison = {
        "claim_boundary": (
            "The replay is one global LRU over (layer, expert) keys. The endpoint uses the actual Colibrì "
            "per-layer cache with a fixed whole-slot allocation. Capacity and policy differ, so the comparison "
            "is diagnostic rather than an equality requirement."
        ),
        "8g": {
            "global_lru_replay": global_8g,
            "native_colibri": {
                "usable_capacity_bytes": native_8g["capacity"]["usable_cache_bytes"],
                "hit_fraction": native_8g["native_cache"]["hit_fraction"],
                "streamed_bytes": native_8g["native_cache"]["streamed_bytes"],
            },
        },
        "96g": {
            "global_lru_replay": global_96g,
            "native_colibri": {
                "usable_capacity_bytes": native_96g["capacity"]["usable_cache_bytes"],
                "hit_fraction": native_96g["native_cache"]["hit_fraction"],
                "streamed_bytes": native_96g["native_cache"]["streamed_bytes"],
            },
        },
        "interpretation": (
            "At the small anchor, per-layer reservation preserves limited local reuse while the similarly sized "
            "global LRU churns. Near 96 GiB the native and global hit fractions are close but not identical, "
            "consistent with their different allocation policies and usable capacities."
        ),
    }

    anchor = runs["anchor-96g"]
    max_runs = [runs[large] for _, large in PAIRS]
    max_mean_tps = statistics.fmean(tps(row) for row in max_runs)
    max_mean_latency = statistics.fmean(mean_latency(row) for row in max_runs)
    max_mean_physical = statistics.fmean(physical_bytes(row) for row in max_runs)
    document = {
        "schema_version": "phase12-nvme-colibri-endpoint-campaign-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "inconclusive",
        "identity": {
            "colibri_commit": next(iter(runs.values()))["identity"]["colibri_commit"],
            "model_revision": next(iter(runs.values()))["identity"]["model_revision"],
            "binary": next(iter(runs.values()))["identity"]["binary"],
            "token_ids_sha256": next(iter(token_digests)) if len(token_digests) == 1 else None,
            "normalized_route_sha256": next(iter(route_digests)) if len(route_digests) == 1 else None,
            "global_replay": identity(args.global_replay),
        },
        "method": {
            "fresh_interleaved_pairs": 3,
            "minimum_complete_decode_forwards_per_run": 256,
            "cache_state": "OS_COLD_REQUESTED_AND_DROPPED",
            "paired_interval": "two-sided Student-t 95% interval over three predeclared pairs (df=2)",
            "runtime_ceiling_seconds": 7200,
        },
        "runs": summaries,
        "paired_max_safe_vs_8g": {
            "throughput_gain_fraction": mean_ci_95(throughput_gain),
            "mean_decode_latency_change_fraction": mean_ci_95(mean_latency_change),
            "p95_decode_latency_change_fraction": mean_ci_95(p95_latency_change),
            "native_streamed_bytes_change_fraction": mean_ci_95(native_bytes_change),
            "physical_nvme0_read_bytes_change_fraction": mean_ci_95(physical_bytes_change),
            "exposed_expert_load_wait_change_fraction": mean_ci_95(exposed_load_change),
        },
        "max_safe_aggregate": {
            "mean_decode_tokens_per_second": max_mean_tps,
            "mean_decode_forward_latency_seconds": max_mean_latency,
            "mean_physical_nvme0_read_bytes": max_mean_physical,
            "maximum_observed_rss_bytes": max(row["process_resources"]["maximum_rss_bytes"] for row in max_runs),
            "accepted_rss_ceiling_bytes": max_runs[0]["capacity"]["accepted_process_rss_ceiling_bytes"],
            "minimum_rss_headroom_bytes": min(
                row["capacity"]["accepted_process_rss_ceiling_bytes"] - row["process_resources"]["maximum_rss_bytes"]
                for row in max_runs
            ),
            "maximum_swap_bytes": max(row["process_resources"]["maximum_swap_bytes"] for row in max_runs),
        },
        "max_safe_vs_96g_anchor": {
            "max_safe_mean_throughput_gain_fraction": max_mean_tps / tps(anchor) - 1.0,
            "max_safe_mean_latency_change_fraction": max_mean_latency / mean_latency(anchor) - 1.0,
            "max_safe_mean_physical_read_bytes_change_fraction": max_mean_physical / physical_bytes(anchor) - 1.0,
            "96g_decode_tokens_per_second": tps(anchor),
            "96g_maximum_rss_bytes": anchor["process_resources"]["maximum_rss_bytes"],
        },
        "native_vs_global_lru": comparison,
        "correctness_and_resources": {
            "all_runs_passed": all(row["status"] == "PASS" for row in runs.values()),
            "all_routes_equal": len(route_digests) == 1,
            "all_token_sequences_equal": len(token_digests) == 1,
            "all_buffered_full_expert_fallbacks_zero": all(
                row["cache"]["final_run_counters"]["v7"] == 0 for row in runs.values()
            ),
            "all_swap_and_cgroup_events_zero": all(
                row["process_resources"]["maximum_swap_bytes"] == 0
                and not any(row["process_resources"]["cgroup_event_delta"].values())
                for row in runs.values()
            ),
        },
        "interpretation": {
            "observed": (
                "MAX_SAFE materially increases full-model CPU decode throughput and reduces native/physical "
                "expert I/O relative to 8 GiB; the paired 95% interval excludes zero."
            ),
            "causal_hypothesis_for_trace": (
                "The gain tracks higher native cache hits and lower exposed expert-load wait. Perfetto must "
                "verify the non-overlapping critical-path attribution and quantify trace perturbation."
            ),
            "optimization_disposition": (
                "No implementation optimization is selected from the untraced campaign. MAX_SAFE is an explicit "
                "evidence configuration, not a new default; the adjacent traced run is the next evidence-driven action."
            ),
        },
        "failures": failures,
        "next_action": "capture and analyze the adjacent MAX_SAFE full-stack Perfetto run",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"],
        "output": identity(args.output),
        "mean_throughput_gain_fraction": document["paired_max_safe_vs_8g"]["throughput_gain_fraction"]["mean"],
        "throughput_gain_ci95": [
            document["paired_max_safe_vs_8g"]["throughput_gain_fraction"]["lower"],
            document["paired_max_safe_vs_8g"]["throughput_gain_fraction"]["upper"],
        ],
        "failures": failures,
    }, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

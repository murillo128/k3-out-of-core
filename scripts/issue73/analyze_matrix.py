#!/usr/bin/env python3
"""Summarize full-K3 run matrices into portable decision evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

from common import validate_workload, write_json


def nearest_rank(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def workload_identity(workload: dict[str, object], compliance: bool) -> dict[str, object]:
    result = {
        "prompt_ids": workload["prompt_ids"],
        "generated_ids": workload["generated_ids"],
        "generated_text": workload["generated_text"],
    }
    if compliance:
        result["logits_fnv64"] = workload["logits_fnv64"]
        result["routes"] = workload["routes"]
    return result


def resource_summary(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    samples = value["samples"]
    gpu_fields: dict[int, dict[str, list[float]]] = {}
    process_rss: list[int] = []
    process_hwm: list[int] = []
    process_pin: list[int] = []
    process_minor_faults: list[int] = []
    process_major_faults: list[int] = []
    voluntary_switches: list[int] = []
    involuntary_switches: list[int] = []
    host_available: list[int] = []
    host_cached: list[int] = []
    host_mlocked: list[int] = []
    cgroup_current: list[int] = []
    cgroup_peak: list[int] = []
    cgroup_stats: dict[str, list[int]] = {}
    for sample in samples:
        process = sample["process"]
        host = sample["host"]
        if "VmRSS" in process:
            process_rss.append(process["VmRSS"])
        if "VmHWM" in process:
            process_hwm.append(process["VmHWM"])
        if "VmPin" in process:
            process_pin.append(process["VmPin"])
        if "minor_faults" in process:
            process_minor_faults.append(process["minor_faults"])
        if "major_faults" in process:
            process_major_faults.append(process["major_faults"])
        if "voluntary_ctxt_switches" in process:
            voluntary_switches.append(process["voluntary_ctxt_switches"])
        if "nonvoluntary_ctxt_switches" in process:
            involuntary_switches.append(process["nonvoluntary_ctxt_switches"])
        if "MemAvailable" in host:
            host_available.append(host["MemAvailable"])
        if "Cached" in host:
            host_cached.append(host["Cached"])
        if "Mlocked" in host:
            host_mlocked.append(host["Mlocked"])
        cgroup = sample.get("cgroup", {})
        if isinstance(cgroup.get("memory_current"), int):
            cgroup_current.append(cgroup["memory_current"])
        if isinstance(cgroup.get("memory_peak"), int):
            cgroup_peak.append(cgroup["memory_peak"])
        for key, amount in cgroup.get("memory_stat", {}).items():
            cgroup_stats.setdefault(key, []).append(amount)
        for gpu in sample["gpus"]:
            fields = gpu_fields.setdefault(gpu["cuda_ordinal"], {
                "utilization": [], "memory_used": [], "memory_free": [], "power": []})
            fields["utilization"].append(gpu["gpu_utilization_percent"])
            fields["memory_used"].append(gpu["memory_used_mib"])
            fields["memory_free"].append(gpu["memory_free_mib"])
            if gpu["power_watts"] is not None:
                fields["power"].append(gpu["power_watts"])
    gpus = []
    for ordinal, fields in sorted(gpu_fields.items()):
        gpus.append({
            "cuda_ordinal": ordinal,
            "utilization_mean_percent": statistics.fmean(fields["utilization"]),
            "utilization_max_percent": max(fields["utilization"]),
            "memory_used_max_mib": max(fields["memory_used"]),
            "memory_free_min_mib": min(fields["memory_free"]),
            "power_max_watts": max(fields["power"]) if fields["power"] else None,
        })
    event_delta: dict[str, int] = {}
    if samples:
        before = samples[0].get("cgroup", {}).get("memory_events", {})
        after = samples[-1].get("cgroup", {}).get("memory_events", {})
        event_delta = {key: after.get(key, 0) - before.get(key, 0) for key in set(before) | set(after)}
    return {
        "elapsed_seconds": value["elapsed_seconds"],
        "sample_count": len(samples),
        "process_rss_max_kib": max(process_rss, default=0),
        "process_hwm_max_kib": max(process_hwm, default=0),
        "process_pin_max_kib": max(process_pin, default=0),
        "process_minor_faults_final": process_minor_faults[-1] if process_minor_faults else 0,
        "process_major_faults_final": process_major_faults[-1] if process_major_faults else 0,
        "voluntary_context_switches_final": voluntary_switches[-1] if voluntary_switches else 0,
        "involuntary_context_switches_final": involuntary_switches[-1] if involuntary_switches else 0,
        "host_mem_available_min_kib": min(host_available, default=0),
        "host_cached_max_kib": max(host_cached, default=0),
        "host_mlocked_max_kib": max(host_mlocked, default=0),
        "cgroup_memory_current_max_bytes": max(cgroup_current, default=0),
        "cgroup_memory_peak_max_bytes": max(cgroup_peak, default=0),
        "cgroup_memory_stat_max_bytes": {
            key: max(amounts) for key, amounts in sorted(cgroup_stats.items())},
        "cgroup_memory_event_delta": event_delta,
        "swap_empty_before_and_after": not value["swap"]["before"] and not value["swap"]["after"],
        "block_delta": value["block_device"]["delta"],
        "gpus": gpus,
    }


def run_summary(workload: dict[str, object], resources: dict[str, object]) -> dict[str, object]:
    latencies = workload["latency_us"]
    decode = latencies[1:]
    generated = len(workload["generated_ids"])
    mechanism = workload["mechanism"]
    devices = workload["multi_gpu"]["devices"]
    peers = workload["multi_gpu"]["peer_diagnostics"]
    return {
        "generated_tokens": generated,
        "ttft_us": latencies[0],
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode) * 1_000_000.0 / sum(decode),
        "decode_latency_us": {
            "p50": nearest_rank(decode, 0.50), "p95": nearest_rank(decode, 0.95),
            "p99": nearest_rank(decode, 0.99), "max": max(decode),
        },
        "cpu": {
            "user_time_us": workload["cpu_user_time_us"],
            "system_time_us": workload["cpu_system_time_us"],
            "peak_rss_kib": workload["peak_rss_kib"],
        },
        "mechanism": mechanism,
        "storage": workload["storage"],
        "transfer": workload["transfer"],
        "capacities": workload["capacities"],
        "hierarchy_residency": workload["hierarchy_residency"],
        "per_device": devices,
        "peer": {
            "host_staged_bytes": sum(item["host_staged_bytes"] for item in peers),
            "peer_bytes": sum(item["peer_bytes"] for item in peers),
            "host_staged_blocking_us": sum(item["host_staged_blocking_us"] for item in peers),
            "host_staging_reuse_waits": sum(item["host_staging_reuse_waits"] for item in peers),
        },
        "bytes_per_generated_token": {
            "logical_storage": workload["storage"]["read_bytes"] / generated,
            "h2d": mechanism["h2d_bytes"] / generated,
            "peer": sum(item["peer_bytes"] for item in peers) / generated,
            "guest_block": resources["block_delta"]["read_bytes"] / generated,
        },
        "resources": resources,
    }


def pooled(workloads: list[dict[str, object]], summaries: list[dict[str, object]]) -> dict[str, object]:
    decode = [latency for workload in workloads for latency in workload["latency_us"][1:]]
    generated = sum(len(workload["generated_ids"]) for workload in workloads)
    mechanisms = [workload["mechanism"] for workload in workloads]
    hot_hits = sum(item["hot_hits"] for item in mechanisms)
    hot_misses = sum(item["hot_misses"] for item in mechanisms)
    cold_hits = sum(item["cold_hits"] for item in mechanisms)
    cold_misses = sum(item["cold_misses"] for item in mechanisms)
    return {
        "processes": len(workloads),
        "generated_tokens": generated,
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode) * 1_000_000.0 / sum(decode),
        "per_process_decode_tps": [summary["decode_tps"] for summary in summaries],
        "ttft_us": {
            "p50": nearest_rank([workload["latency_us"][0] for workload in workloads], 0.50),
            "p95": nearest_rank([workload["latency_us"][0] for workload in workloads], 0.95),
        },
        "decode_latency_us": {
            "p50": nearest_rank(decode, 0.50), "p95": nearest_rank(decode, 0.95),
            "p99": nearest_rank(decode, 0.99), "max": max(decode),
        },
        "hot": {"hits": hot_hits, "misses": hot_misses,
                "hit_rate": hot_hits / (hot_hits + hot_misses)},
        "cold": {"hits": cold_hits, "misses": cold_misses,
                 "hit_rate": cold_hits / (cold_hits + cold_misses)},
        "bytes_per_generated_token": {
            key: sum(summary["bytes_per_generated_token"][key] * summary["generated_tokens"]
                     for summary in summaries) / generated
            for key in ("logical_storage", "h2d", "peer", "guest_block")
        },
        "swap_empty_all_processes": all(
            summary["resources"]["swap_empty_before_and_after"] for summary in summaries),
        "oom_kill_delta": sum(
            summary["resources"]["cgroup_memory_event_delta"].get("oom_kill", 0)
            for summary in summaries),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cases: dict[str, object] = {}
    identities: list[str] = []
    compliance_identities: list[str] = []
    for matrix_path in args.matrix:
        matrix = json.loads(matrix_path.read_text())
        if matrix.get("status") != "complete":
            raise SystemExit(f"incomplete matrix: {matrix_path}")
        workloads = []
        summaries = []
        commands = []
        for entry in matrix["runs"]:
            workload_path = Path(entry["workload"])
            resources_path = Path(entry["resources"])
            expected_tokens = json.loads(workload_path.read_text())["runtime"]["max_generate"]
            workload = validate_workload(workload_path, expected_tokens)
            resources = resource_summary(resources_path)
            workloads.append(workload)
            summaries.append(run_summary(workload, resources))
            commands.append(json.loads(resources_path.read_text())["command"])
            identities.append(digest(workload_identity(workload, False)))
            if workload.get("expert_runtime_mode") == "COMPLIANCE":
                compliance_identities.append(digest(workload_identity(workload, True)))
        key = matrix["case"]
        if key in cases:
            raise SystemExit(f"duplicate matrix case: {key}")
        cases[key] = {
            "source_matrix": str(matrix_path), "roles": matrix["roles"],
            "n_gpu_layers": matrix["n_gpu_layers"], "commands": commands,
            "pooled": pooled(workloads, summaries), "runs": summaries,
        }
    result = {
        "schema_version": "issue73-matrix-summary-v1", "status": "pass",
        "identity": {
            "production_output_exact_across_all_processes": len(set(identities)) == 1,
            "production_output_sha256": identities[0], "processes": len(identities),
            "compliance_identity_exact": (
                len(set(compliance_identities)) == 1 if compliance_identities else None),
        },
        "cases": cases,
    }
    write_json(args.output, result)
    print(f"ISSUE73_MATRIX_ANALYSIS status=pass cases={len(cases)} processes={len(identities)}")


if __name__ == "__main__":
    main()

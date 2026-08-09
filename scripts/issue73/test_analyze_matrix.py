#!/usr/bin/env python3
"""Deterministic checks for the issue 73 matrix summarizer."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from analyze_matrix import nearest_rank, pooled, resource_summary, run_summary


def main() -> None:
    workload = {
        "generated_ids": [1, 2, 3], "latency_us": [100, 10, 20],
        "cpu_user_time_us": 40, "cpu_system_time_us": 5, "peak_rss_kib": 10,
        "mechanism": {
            "hot_hits": 3, "hot_misses": 1, "cold_hits": 2, "cold_misses": 2,
            "h2d_bytes": 300,
        },
        "storage": {"read_bytes": 600}, "transfer": {}, "capacities": {},
        "hierarchy_residency": {},
        "multi_gpu": {"devices": [], "peer_diagnostics": []},
    }
    resources = {
        "block_delta": {"read_bytes": 900}, "swap_empty_before_and_after": True,
        "cgroup_memory_event_delta": {},
    }
    summary = run_summary(workload, resources)
    assert summary["decode_tps"] == 2 * 1_000_000 / 30
    assert summary["bytes_per_generated_token"] == {
        "logical_storage": 200, "h2d": 100, "peer": 0, "guest_block": 300}
    aggregate = pooled([workload, workload], [summary, summary])
    assert aggregate["decode_tokens"] == 4
    assert aggregate["hot"]["hit_rate"] == 0.75
    assert aggregate["bytes_per_generated_token"]["guest_block"] == 300
    assert nearest_rank([3, 1, 2], 0.95) == 3

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "resources.json"
        path.write_text(json.dumps({
            "elapsed_seconds": 1.0,
            "swap": {"before": "", "after": ""},
            "block_device": {"delta": {"read_bytes": 42}},
            "samples": [{
                "process": {"VmRSS": 10, "VmHWM": 11, "VmPin": 1,
                            "minor_faults": 2, "major_faults": 1,
                            "voluntary_ctxt_switches": 4, "nonvoluntary_ctxt_switches": 3},
                "host": {"MemAvailable": 100, "Cached": 20, "Mlocked": 5},
                "cgroup": {"memory_current": 30, "memory_peak": 31,
                           "memory_stat": {"anon": 21, "file": 9},
                           "memory_events": {"oom_kill": 0}},
                "gpus": [{
                    "cuda_ordinal": 0, "gpu_utilization_percent": 50,
                    "memory_used_mib": 12, "memory_free_mib": 8, "power_watts": 20,
                }],
            }],
        }))
        captured = resource_summary(path)
        assert captured["process_rss_max_kib"] == 10
        assert captured["process_major_faults_final"] == 1
        assert captured["cgroup_memory_stat_max_bytes"]["anon"] == 21
        assert captured["gpus"][0]["memory_free_min_mib"] == 8
        assert captured["swap_empty_before_and_after"]

    print("ISSUE73_MATRIX_ANALYSIS_TEST status=pass pooled=pass resources=pass")


if __name__ == "__main__":
    main()

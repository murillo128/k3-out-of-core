#!/usr/bin/env python3
"""Deterministic checks for the issue 73 matched-control analyzer."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


def case(
        name: str, policy: str, tps: float, slots: int, hot_hits: int,
        h2d_bytes: int = 3) -> dict[str, object]:
    run = {
        "generated_tokens": 2,
        "capacities": {"hot_requested_slots": slots, "hot_effective_slots": slots},
        "transfer": {"h2d_time_us": 20 if h2d_bytes else 0, "pageable_fallback": False},
        "async_io": {"diagnostics": {
            "read_queue_wait_us": 40, "buffered_fallback_operations": 0}},
        "storage": {"direct_unsupported_source_count": 0},
        "resources": {
            "process_rss_max_kib": 100,
            "gpus": [{"cuda_ordinal": 0, "memory_used_max_mib": 10,
                      "memory_free_min_mib": 20}],
        },
    }
    return {
        "roles": f"0:{slots}", "n_gpu_layers": 8, "n_ubatch": 4,
        "miss_policy": policy, "measurement_tier": "P0",
        "pooled": {
            "processes": 1, "decode_tps": tps,
            "ttft_us": {"p50": 1, "p95": 1},
            "decode_latency_us": {"p50": 2, "p95": 2, "p99": 2, "max": 2},
            "hot": {"hits": hot_hits, "misses": 2, "hit_rate": hot_hits / (hot_hits + 2)},
            "cold": {"hits": 0, "misses": 2, "hit_rate": 0},
            "bytes_per_generated_token": {
                "h2d": h2d_bytes, "logical_storage": 4, "guest_block": 5, "peer": 0},
            "swap_empty_all_processes": True, "oom_kill_delta": 0,
        },
        "runs": [run], "case": name,
    }


def main() -> None:
    source = {
        "status": "pass",
        "identity": {"production_output_exact_across_all_processes": True},
        "cases": {
            "CPU_CONTROL": case("CPU_CONTROL", "CPU_FALLBACK", 1.0, 64, 0, 0),
            "K3_INITIAL": case("K3_INITIAL", "PROMOTE_AND_GPU", 2.0, 64, 0),
            "GPU_HOT_MAX": case("GPU_HOT_MAX", "PROMOTE_AND_GPU", 3.0, 549, 1),
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        summary = temporary / "summary.json"
        output = temporary / "controls.json"
        summary.write_text(json.dumps(source))
        subprocess.run([
            sys.executable, str(Path(__file__).with_name("analyze_matched_controls.py")),
            "--summary", str(summary), "--output", str(output),
        ], check=True, capture_output=True, text=True)
        result = json.loads(output.read_text())
        assert result["ratios"] == {
            "gpu_hot_0_over_cpu_control": 2.0,
            "gpu_hot_max_over_gpu_hot_0": 1.5,
            "gpu_hot_max_over_cpu_control": 3.0,
        }
        assert result["cells"]["GPU_HOT_MAX"]["h2d_service_us_per_generated_token"] == 10
        assert result["cells"]["CPU_CONTROL"]["storage_queue_wait_us_per_generated_token"] == 20
    print("ISSUE73_MATCHED_CONTROLS_TEST status=pass ratios=pass resources=pass")


if __name__ == "__main__":
    main()

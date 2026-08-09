#!/usr/bin/env python3
"""Quantify bounded scheduler/wakeup evidence for issue 69 decode windows."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

from analyze_delta_d import file_identity, workload_summary
from common import write_json

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.phase13.analyze_iteration_trace import query  # noqa: E402


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return sorted(values)[math.floor((len(values) - 1) * fraction)]


def latency_summary(values: list[int]) -> dict[str, int]:
    return {
        "samples": len(values), "total_ns": sum(values),
        "p50_ns": percentile(values, 0.50), "p95_ns": percentile(values, 0.95),
        "p99_ns": percentile(values, 0.99), "max_ns": max(values, default=0),
    }


def cell_summary(trace_processor: Path, directory: Path) -> dict[str, object]:
    trace = directory / "scheduler.pftrace"
    lifecycle = query(trace_processor, trace, """
        SELECT name, ts FROM slice
        WHERE category = 'k3.lifecycle'
          AND name IN ('decode_window_start', 'decode_window_end')
        ORDER BY ts
    """)
    bounds = {row["name"]: int(row["ts"]) for row in lifecycle}
    begin = bounds["decode_window_start"]
    end = bounds["decode_window_end"]
    window_ns = end - begin
    thread_rows = query(trace_processor, trace, """
        SELECT t.utid, t.tid, COALESCE(t.name, '') AS thread_name,
          p.pid, COALESCE(p.name, '') AS process_name,
          GROUP_CONCAT(DISTINCT s.category) AS categories
        FROM slice s
        JOIN thread_track tt ON tt.id = s.track_id
        JOIN thread t ON t.utid = tt.utid
        LEFT JOIN process p ON p.upid = t.upid
        WHERE s.category GLOB 'k3.*'
        GROUP BY t.utid, t.tid, t.name, p.pid, p.name
        ORDER BY t.utid
    """)
    threads: dict[int, dict[str, object]] = {}
    for row in thread_rows:
        categories = set(filter(None, row["categories"].split(",")))
        if "k3.graph" in categories:
            role = "inference_main"
        elif "k3.storage" in categories:
            role = "expert_io_provider"
        elif categories & {"k3.provider", "k3.scheduler", "k3.policy", "k3.transfer"}:
            role = "provider_worker"
        else:
            role = "other_k3"
        threads[int(row["utid"])] = {
            "utid": int(row["utid"]), "tid": int(row["tid"]),
            "thread_name": row["thread_name"], "pid": int(row["pid"]),
            "process_name": row["process_name"], "categories": sorted(categories),
            "role": role,
        }
    if not threads:
        raise RuntimeError(f"no K3 application threads found in {trace}")
    utids = ",".join(str(value) for value in sorted(threads))
    state_rows = query(trace_processor, trace, f"""
        SELECT utid, ts, dur, state
        FROM thread_state
        WHERE utid IN ({utids}) AND dur > 0
          AND ts < {end} AND ts + dur > {begin}
        ORDER BY utid, ts
    """)
    runnable: dict[int, list[int]] = defaultdict(list)
    wake_to_run: dict[int, list[int]] = defaultdict(list)
    preempted: dict[int, list[int]] = defaultdict(list)
    for row in state_rows:
        duration = min(end, int(row["ts"]) + int(row["dur"])) - max(begin, int(row["ts"]))
        if duration <= 0 or row["state"] not in {"R", "R+"}:
            continue
        utid = int(row["utid"])
        runnable[utid].append(duration)
        (preempted if row["state"] == "R+" else wake_to_run)[utid].append(duration)
    sched_rows = query(trace_processor, trace, f"""
        SELECT utid, ts, dur, cpu, end_state
        FROM sched
        WHERE utid IN ({utids}) AND dur > 0
          AND ts < {end} AND ts + dur > {begin}
        ORDER BY utid, ts
    """)
    running_ns: dict[int, int] = defaultdict(int)
    switches: dict[int, dict[str, int]] = defaultdict(lambda: {
        "voluntary": 0, "involuntary": 0, "migrations": 0,
    })
    last_cpu: dict[int, int] = {}
    for row in sched_rows:
        utid = int(row["utid"])
        running_ns[utid] += min(end, int(row["ts"]) + int(row["dur"])) - max(begin, int(row["ts"]))
        state = row["end_state"]
        key = "involuntary" if state.startswith("R") else "voluntary"
        switches[utid][key] += 1
        cpu = int(row["cpu"])
        if utid in last_cpu and last_cpu[utid] != cpu:
            switches[utid]["migrations"] += 1
        last_cpu[utid] = cpu
    roles: dict[str, dict[str, object]] = {}
    for role in sorted({str(value["role"]) for value in threads.values()}):
        selected = [utid for utid, value in threads.items() if value["role"] == role]
        roles[role] = {
            "threads": [threads[utid] for utid in selected],
            "wake_to_run": latency_summary([
                value for utid in selected for value in wake_to_run[utid]
            ]),
            "preempted_runqueue": latency_summary([
                value for utid in selected for value in preempted[utid]
            ]),
            "all_runnable": latency_summary([
                value for utid in selected for value in runnable[utid]
            ]),
            "running_ns": sum(running_ns[utid] for utid in selected),
            "voluntary_context_switches": sum(switches[utid]["voluntary"] for utid in selected),
            "involuntary_context_switches": sum(switches[utid]["involuntary"] for utid in selected),
            "cpu_migrations": sum(switches[utid]["migrations"] for utid in selected),
        }
    raw_rows = query(trace_processor, trace, f"""
        SELECT name, COUNT(*) AS records
        FROM raw WHERE ts BETWEEN {begin} AND {end}
          AND name IN ('sched_switch', 'sched_waking', 'sched_wakeup',
            'sys_enter_pread64', 'sys_exit_pread64', 'block_rq_issue',
            'block_rq_complete', 'irq_handler_entry', 'irq_handler_exit',
            'softirq_entry', 'softirq_exit')
        GROUP BY name ORDER BY name
    """)
    loss_rows = query(trace_processor, trace, """
        SELECT COALESCE(SUM(ABS(value)), 0) AS total FROM stats
        WHERE (severity = 'data_loss' AND name != 'config_write_into_file_discard')
           OR name IN ('traced_buf_incremental_sequences_dropped',
             'traced_buf_sequence_packet_loss', 'traced_buf_trace_writer_packet_loss',
             'traced_final_flush_failed', 'traced_flushes_failed')
    """)
    model_runnable_ns = sum(sum(values) for values in runnable.values())
    main_runnable_ns = int(roles.get("inference_main", {}).get(
        "all_runnable", {}
    ).get("total_ns", 0))
    role_tail_material = any(
        int(value["all_runnable"]["samples"]) >= 10 and
        int(value["all_runnable"]["p95_ns"]) >= 2_000_000
        for value in roles.values()
    )
    material = main_runnable_ns >= window_ns // 20 or role_tail_material
    return {
        "trace": file_identity(trace),
        "capture": file_identity(directory / "capture.json"),
        "workload": workload_summary(directory / "workload.json"),
        "window_ns": window_ns,
        "roles": roles,
        "kernel_event_counts": {row["name"]: int(row["records"]) for row in raw_rows},
        "trace_data_loss": int(loss_rows[0]["total"]),
        "model_runnable_ns": model_runnable_ns,
        "model_runnable_fraction_of_window": model_runnable_ns / window_ns,
        "inference_main_runnable_fraction_of_window": main_runnable_ns / window_ns,
        "scheduler_material": material,
        "materiality_rule": (
            "inference-main runnable delay >=5% of window, or a role with >=10 samples "
            "has p95 >=2 ms; concurrent worker runnable sums are not treated as wall time"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cells = {
        cell: cell_summary(args.trace_processor, args.trace_dir / cell)
        for cell in ("S0", "A1")
    }
    errors: list[str] = []
    if any(cell["trace_data_loss"] != 0 for cell in cells.values()):
        errors.append("scheduler trace reports data loss")
    if any(not cell["workload"]["terminal_state_zero"] for cell in cells.values()):
        errors.append("scheduler workload terminal resource state is nonzero")
    result = {
        "schema_version": "issue69-delta-d2c-scheduler-v1",
        "status": "pass" if not errors else "fail",
        "cells": cells,
        "selection": {
            "affinity_comparator_required": any(
                cell["scheduler_material"] for cell in cells.values()
            ),
            "reason": (
                "run the bounded affinity comparator only when traced runnable delay is material"
            ),
        },
        "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "affinity_comparator_required": result["selection"]["affinity_comparator_required"],
        "errors": errors,
    }, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

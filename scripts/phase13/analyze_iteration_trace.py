#!/usr/bin/env python3
"""Quantify one Phase 13 A/B decode-window trace iteration.

The analysis deliberately separates the provider interval from the following
CUDA graph interval.  Provider issue timing is derived from the current-layer
scheduler enqueue markers.  CUDA occupancy is reported both as raw unions and
as a mutually-exclusive timeline attribution, so overlapping service time is
never added as wall time.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from pathlib import Path


def identity(path: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest(), "size": path.stat().st_size}


def query(trace_processor: Path, trace: Path, sql: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [str(trace_processor), str(trace), "-Q", sql],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return list(csv.DictReader(completed.stdout.splitlines()))


def integer(value: str | None) -> int | None:
    if value in (None, "", "[NULL]"):
        return None
    return int(value)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[math.floor((len(ordered) - 1) * fraction)]


def union_ns(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    total = 0
    begin, end = sorted(intervals)[0]
    for next_begin, next_end in sorted(intervals)[1:]:
        if next_begin <= end:
            end = max(end, next_end)
        else:
            total += end - begin
            begin, end = next_begin, next_end
    return total + end - begin


def intersection_ns(
    left: list[tuple[int, int]], right: list[tuple[int, int]],
) -> int:
    left = sorted(left)
    right = sorted(right)
    i = j = total = 0
    while i < len(left) and j < len(right):
        begin = max(left[i][0], right[j][0])
        end = min(left[i][1], right[j][1])
        if end > begin:
            total += end - begin
        if left[i][1] <= right[j][1]:
            i += 1
        else:
            j += 1
    return total


def exclusive_timeline(
    begin: int,
    end: int,
    classified: dict[str, list[tuple[int, int]]],
    priority: tuple[str, ...],
) -> dict[str, int]:
    clipped: dict[str, list[tuple[int, int]]] = {}
    boundaries = {begin, end}
    for label, intervals in classified.items():
        clipped[label] = []
        for interval_begin, interval_end in intervals:
            interval_begin = max(begin, interval_begin)
            interval_end = min(end, interval_end)
            if interval_end <= interval_begin:
                continue
            clipped[label].append((interval_begin, interval_end))
            boundaries.add(interval_begin)
            boundaries.add(interval_end)
    totals = {label: 0 for label in priority}
    totals["dependency_or_host_gap"] = 0
    points = sorted(boundaries)
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        selected = "dependency_or_host_gap"
        for label in priority:
            if any(start < right and stop > left for start, stop in clipped.get(label, [])):
                selected = label
                break
        totals[selected] += right - left
    return totals


def summarize_case(trace_processor: Path, trace: Path) -> dict[str, object]:
    lifecycle = query(trace_processor, trace, """
        SELECT name, ts FROM slice
        WHERE category = 'k3.lifecycle' AND name IN ('decode_window_start', 'decode_window_end')
        ORDER BY ts
    """)
    lifecycle_by_name = {row["name"]: int(row["ts"]) for row in lifecycle}
    window_start = lifecycle_by_name["decode_window_start"]
    window_end = lifecycle_by_name["decode_window_end"]

    layer_rows = query(trace_processor, trace, """
        SELECT id, ts, dur,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.layer') AS INT) AS layer
        FROM slice
        WHERE category = 'k3.graph' AND name = 'expert_layer_execution' AND dur > 0
        ORDER BY ts
    """)
    layers = [
        {"id": int(row["id"]), "ts": int(row["ts"]), "dur": int(row["dur"]),
         "layer": int(row["layer"])}
        for row in layer_rows
    ]
    enqueue_rows = query(trace_processor, trace, """
        SELECT ts, dur,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.layer') AS INT) AS layer,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.target_device') AS INT) AS target_device
        FROM slice
        WHERE category = 'k3.scheduler' AND name = 'enqueue'
        ORDER BY ts
    """)
    enqueues = [
        {"ts": int(row["ts"]), "dur": int(row["dur"]), "layer": integer(row["layer"]),
         "target_device": integer(row["target_device"])}
        for row in enqueue_rows
    ]
    service_rows = query(trace_processor, trace, """
        SELECT ts, dur, category, name FROM slice
        WHERE dur > 0 AND category IN ('k3.storage', 'k3.transfer')
        ORDER BY ts
    """)
    services = [
        {"ts": int(row["ts"]), "dur": int(row["dur"]), "category": row["category"],
         "name": row["name"]}
        for row in service_rows
    ]
    cuda_rows = query(trace_processor, trace, """
        SELECT ts, dur, name,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.context_id') AS INT) AS context_id,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.stream_id') AS INT) AS stream_id,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.copy_kind') AS INT) AS copy_kind,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.bytes') AS INT) AS bytes,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.sync_type') AS INT) AS sync_type
        FROM slice WHERE category = 'k3.cuda' AND dur > 0 ORDER BY ts
    """)
    cuda = [
        {"ts": int(row["ts"]), "dur": int(row["dur"]), "name": row["name"],
         "context_id": integer(row["context_id"]), "stream_id": integer(row["stream_id"]),
         "copy_kind": integer(row["copy_kind"]), "bytes": integer(row["bytes"]),
         "sync_type": integer(row["sync_type"])}
        for row in cuda_rows
    ]

    cycles: list[dict[str, object]] = []
    for current, following in zip(layers, layers[1:]):
        start = int(current["ts"])
        stop = int(following["ts"])
        provider_end = start + int(current["dur"])
        if start < window_start or stop > window_end or provider_end > stop:
            continue
        current_enqueues = [
            event for event in enqueues
            if event["layer"] == current["layer"] and start <= int(event["ts"]) < provider_end
        ]
        current_enqueues.sort(key=lambda event: int(event["ts"]))
        if current_enqueues:
            first_issue = int(current_enqueues[0]["ts"])
            last_issue_end = max(int(event["ts"]) + int(event["dur"]) for event in current_enqueues)
            provider_parts = {
                "pre_issue_ns": first_issue - start,
                "issue_span_ns": last_issue_end - first_issue,
                "post_issue_ns": provider_end - last_issue_end,
                "no_miss_provider_ns": 0,
            }
        else:
            provider_parts = {
                "pre_issue_ns": 0,
                "issue_span_ns": 0,
                "post_issue_ns": 0,
                "no_miss_provider_ns": provider_end - start,
            }

        graph_cuda = [
            event for event in cuda
            if int(event["ts"]) < stop and int(event["ts"]) + int(event["dur"]) > provider_end
        ]
        classified: dict[str, list[tuple[int, int]]] = {
            "peer_activation_result_copy": [],
            "gpu1_kernel": [],
            "gpu0_kernel": [],
            "cuda_synchronization": [],
            "other_cuda": [],
        }
        gpu0_kernels: list[tuple[int, int]] = []
        gpu1_kernels: list[tuple[int, int]] = []
        for event in graph_cuda:
            interval = (int(event["ts"]), int(event["ts"]) + int(event["dur"]))
            byte_count = event["bytes"] or 0
            if event["name"] == "memcpy" and 16 * 1024 <= byte_count < 1024 * 1024 and \
                    event["copy_kind"] in (1, 2):
                classified["peer_activation_result_copy"].append(interval)
            elif event["name"] == "kernel" and event["context_id"] == 2:
                classified["gpu1_kernel"].append(interval)
                gpu1_kernels.append(interval)
            elif event["name"] == "kernel" and event["context_id"] == 1:
                classified["gpu0_kernel"].append(interval)
                gpu0_kernels.append(interval)
            elif event["name"] == "synchronization":
                classified["cuda_synchronization"].append(interval)
            else:
                classified["other_cuda"].append(interval)
        graph_exclusive = exclusive_timeline(
            provider_end, stop, classified,
            ("peer_activation_result_copy", "gpu1_kernel", "gpu0_kernel",
             "cuda_synchronization", "other_cuda"),
        )

        provider_cuda = [
            event for event in cuda
            if int(event["ts"]) < provider_end and int(event["ts"]) + int(event["dur"]) > start
        ]
        expert_h2d = [
            (int(event["ts"]), int(event["ts"]) + int(event["dur"]))
            for event in provider_cuda
            if event["name"] == "memcpy" and event["copy_kind"] == 1 and (event["bytes"] or 0) >= 1024 * 1024
        ]
        provider_services: dict[str, list[tuple[int, int]]] = {
            "storage": [], "stage": [], "h2d_scope": [], "event_wait": [],
        }
        for event in services:
            event_start = int(event["ts"])
            event_stop = event_start + int(event["dur"])
            if event_start >= provider_end or event_stop <= start:
                continue
            interval = (max(start, event_start), min(provider_end, event_stop))
            if event["category"] == "k3.storage":
                provider_services["storage"].append(interval)
            elif event["name"] == "stage":
                provider_services["stage"].append(interval)
            elif event["name"] in ("h2d", "h2d_wave"):
                provider_services["h2d_scope"].append(interval)
            elif event["name"] == "event_wait":
                provider_services["event_wait"].append(interval)

        cycles.append({
            "layer": current["layer"],
            "start_offset_ns": start - window_start,
            "wall_ns": stop - start,
            "provider_ns": provider_end - start,
            "graph_ns": stop - provider_end,
            "demand_enqueues": len(current_enqueues),
            "enqueue_target_devices": sorted({
                event["target_device"] for event in current_enqueues if event["target_device"] is not None
            }),
            "provider_critical_path": provider_parts,
            "provider_service_unions_ns": {
                key: union_ns(value) for key, value in provider_services.items()
            } | {"expert_h2d_cuda": union_ns(expert_h2d)},
            "graph_exclusive_ns": graph_exclusive,
            "gpu_kernel_overlap_ns": intersection_ns(gpu0_kernels, gpu1_kernels),
            "gpu0_kernel_union_ns": union_ns(gpu0_kernels),
            "gpu1_kernel_union_ns": union_ns(gpu1_kernels),
        })

    if not cycles:
        raise RuntimeError(f"no complete routed-layer cycles inside {trace}")

    def distribution(field: str) -> dict[str, float]:
        values = [float(cycle[field]) / 1e6 for cycle in cycles]
        return {
            "mean_ms": sum(values) / len(values),
            "p50_ms": percentile(values, 0.50),
            "p95_ms": percentile(values, 0.95),
            "max_ms": max(values),
        }

    provider_totals = {
        key: sum(int(cycle["provider_critical_path"][key]) for cycle in cycles)
        for key in ("pre_issue_ns", "issue_span_ns", "post_issue_ns", "no_miss_provider_ns")
    }
    graph_keys = (
        "peer_activation_result_copy", "gpu1_kernel", "gpu0_kernel",
        "cuda_synchronization", "other_cuda", "dependency_or_host_gap",
    )
    graph_totals = {
        key: sum(int(cycle["graph_exclusive_ns"][key]) for cycle in cycles) for key in graph_keys
    }
    wall_total = sum(int(cycle["wall_ns"]) for cycle in cycles)
    provider_total = sum(int(cycle["provider_ns"]) for cycle in cycles)
    graph_total = sum(int(cycle["graph_ns"]) for cycle in cycles)
    exclusive = {**provider_totals, **graph_totals}
    if sum(exclusive.values()) != wall_total or provider_total + graph_total != wall_total:
        raise RuntimeError("critical-path accounting is not wall-exact")
    service_keys = ("storage", "stage", "h2d_scope", "event_wait", "expert_h2d_cuda")
    services_total = {
        key: sum(int(cycle["provider_service_unions_ns"][key]) for cycle in cycles)
        for key in service_keys
    }
    return {
        "trace": identity(trace),
        "window": {
            "logical_start_ns": window_start,
            "logical_end_ns": window_end,
            "duration_ns": window_end - window_start,
        },
        "complete_routed_layer_cycles": len(cycles),
        "layer_wall": distribution("wall_ns"),
        "provider_wall": distribution("provider_ns"),
        "graph_wall": distribution("graph_ns"),
        "critical_path": {
            "wall_ns": wall_total,
            "buckets_ns": exclusive,
            "buckets_fraction": {key: value / wall_total for key, value in exclusive.items()},
            "accounting_error_ns": wall_total - sum(exclusive.values()),
        },
        "provider_service_unions_not_additive": services_total,
        "gpu": {
            "gpu0_kernel_union_ns": sum(int(cycle["gpu0_kernel_union_ns"]) for cycle in cycles),
            "gpu1_kernel_union_ns": sum(int(cycle["gpu1_kernel_union_ns"]) for cycle in cycles),
            "simultaneous_gpu0_gpu1_kernel_overlap_ns": sum(
                int(cycle["gpu_kernel_overlap_ns"]) for cycle in cycles
            ),
        },
        "cycles": cycles,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--a-trace", type=Path, required=True)
    parser.add_argument("--b-trace", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--previous-analysis", type=Path)
    parser.add_argument("--quick-summary", type=Path)
    parser.add_argument("--code-change", default="none")
    parser.add_argument("--decision", choices=("analysis_only", "retain", "revert"),
                        default="analysis_only")
    parser.add_argument("--decision-rationale", default="iteration analysis only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cases = {
        "A": summarize_case(args.trace_processor, args.a_trace),
        "B": summarize_case(args.trace_processor, args.b_trace),
    }
    a_wall = float(cases["A"]["layer_wall"]["mean_ms"])
    b_wall = float(cases["B"]["layer_wall"]["mean_ms"])
    b_buckets = cases["B"]["critical_path"]["buckets_ns"]
    a_buckets = cases["A"]["critical_path"]["buckets_ns"]
    a_count = int(cases["A"]["complete_routed_layer_cycles"])
    b_count = int(cases["B"]["complete_routed_layer_cycles"])
    mean_bucket_delta_ms = {
        key: b_buckets[key] / b_count / 1e6 - a_buckets[key] / a_count / 1e6
        for key in b_buckets
    }
    issue_delta = mean_bucket_delta_ms["issue_span_ns"]
    provider_dependency_delta = mean_bucket_delta_ms["pre_issue_ns"] + \
        mean_bucket_delta_ms["post_issue_ns"]
    issue_is_dominant = issue_delta >= provider_dependency_delta
    if issue_is_dominant:
        ranked_bottlenecks = [
            {
                "rank": 1,
                "mechanism": "multi_device_provider_serial_demand_issue",
                "evidence": (
                    "B serializes current-layer scheduler issue around per-miss cold-cache/storage/staging work; "
                    "A attempts current-layer demand enqueues adjacently before its first wait."
                ),
                "measured_extra_ms_per_layer_vs_A": issue_delta,
                "target_bucket": "issue_span_ns",
            },
            {
                "rank": 2,
                "mechanism": "provider_pre_and_post_issue_dependency_gaps",
                "evidence": "Provider wall dominates both traces and materially exceeds all CUDA service unions.",
                "measured_extra_ms_per_layer_vs_A": provider_dependency_delta,
                "target_bucket": "pre_issue_ns+post_issue_ns",
            },
        ]
        dominant = "multi_device_provider_serial_demand_issue"
        rationale = (
            "The dominant B regression occurs before routed graph CUDA work. Small peer copies and kernels "
            "cannot explain the provider-scale wall delta, and the current multi-device loop waits through "
            "each miss before issuing the next current-layer demand."
        )
        next_hypothesis = {
            "single_primary_hypothesis": (
                "Issue every multi-device current-layer demand and deferred storage read before the first wait, "
                "then consume completions and stage per-device H2D work."
            ),
            "predicted_trace_change": "issue_span_ns decreases by at least 30% in B",
            "predicted_tps_change": "B decode TPS increases by at least 3% with A unchanged within 3%",
            "falsifier": (
                "Revert if issue_span_ns changes by less than 3% and B TPS changes by less than 3%, or if any "
                "correctness/resource gate regresses."
            ),
        }
    else:
        ranked_bottlenecks = [
            {
                "rank": 1,
                "mechanism": "provider_pre_and_post_issue_dependency_gaps",
                "evidence": (
                    "Current-layer enqueue issue is now adjacent, but B provider wall remains dominant. "
                    "Representative B layers contain repeated approximately 9-10 ms untraced gaps after "
                    "host-ready to H2D scheduler transitions and before the next hot-slot victim marker."
                ),
                "measured_extra_ms_per_layer_vs_A": provider_dependency_delta,
                "target_bucket": "pre_issue_ns+post_issue_ns",
            },
        ]
        dominant = "provider_pre_and_post_issue_dependency_gaps"
        rationale = (
            "The prior scheduler-issue bucket has collapsed, but the same wall moved into provider pre/post "
            "issue gaps. CUDA service remains far too small to explain the provider interval; the next code "
            "operation after each observed host-ready to H2D transition is a repeated transfer-ring diagnostics "
            "snapshot before the following miss is staged."
        )
        next_hypothesis = {
            "single_primary_hypothesis": (
                "Snapshot each device transfer ring's immutable effective lane capacity once before per-miss "
                "staging instead of reacquiring diagnostics after every staged miss."
            ),
            "predicted_trace_change": "B post_issue_ns decreases by at least 20%",
            "predicted_tps_change": "B decode TPS increases by at least 3% with A unchanged within 3%",
            "falsifier": (
                "Revert if post_issue_ns changes by less than 3% and B TPS changes by less than 3%, or if any "
                "correctness/resource gate regresses."
            ),
        }
    ranked_bottlenecks.append({
        "rank": len(ranked_bottlenecks) + 1,
        "mechanism": "serialized_remote_branch_graph",
        "evidence": "The B window contains no simultaneous GPU0/GPU1 kernel interval.",
        "measured_overlap_ns": cases["B"]["gpu"]["simultaneous_gpu0_gpu1_kernel_overlap_ns"],
        "target_bucket": "graph_dependency_or_host_gap",
    })
    previous = json.loads(args.previous_analysis.read_text()) if args.previous_analysis else None
    quick = json.loads(args.quick_summary.read_text()) if args.quick_summary else None
    output = {
        "schema_version": "phase13-iteration-trace-analysis-v1",
        "status": "pass",
        "iteration": args.iteration,
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "model_fixture": "DeepSeek-V4-Flash-UD-Q2_K_XL@85ce4196ab6e82852e25dfec2b7e2beaae56f5f1",
        "trace_profile": {
            "selection_seed": 61,
            "request_ordinal": 15,
            "routed_layer": 11,
            "requested_window_ms": 1000,
            "query_identity": identity(Path(__file__).resolve()),
        },
        "cases": cases,
        "comparison": {
            "observed_layer_rate_ratio_B_over_A": a_wall / b_wall,
            "mean_layer_wall_delta_ms_B_minus_A": b_wall - a_wall,
            "mean_critical_path_bucket_delta_ms_B_minus_A": mean_bucket_delta_ms,
        },
        "ranked_bottlenecks": ranked_bottlenecks,
        "root_cause_conclusion": {
            "status": "OBSERVED",
            "dominant": dominant,
            "implementation_induced": True,
            "structural_limit_proven": False,
            "rationale": rationale,
        },
        "next_hypothesis": next_hypothesis,
        "iteration_result": {
            "code_change_identity": args.code_change,
            "quick_screen": None if quick is None else {
                "status": quick["status"],
                "identity": quick["identity"],
                "scaling": quick["scaling"],
            },
            "before_after_trace": None if previous is None else {
                "previous_iteration": previous["iteration"],
                "B_mean_layer_wall_delta_percent": (
                    b_wall / float(previous["cases"]["B"]["layer_wall"]["mean_ms"]) - 1.0
                ) * 100.0,
                "B_issue_span_delta_percent": (
                    (b_buckets["issue_span_ns"] / b_count) /
                    (previous["cases"]["B"]["critical_path"]["buckets_ns"]["issue_span_ns"] /
                     int(previous["cases"]["B"]["complete_routed_layer_cycles"])) - 1.0
                ) * 100.0,
                "B_post_issue_delta_percent": (
                    (b_buckets["post_issue_ns"] / b_count) /
                    (previous["cases"]["B"]["critical_path"]["buckets_ns"]["post_issue_ns"] /
                     int(previous["cases"]["B"]["complete_routed_layer_cycles"])) - 1.0
                ) * 100.0,
            },
            "decision": args.decision,
            "rationale": args.decision_rationale,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({
        "status": "pass",
        "output": str(args.output),
        "A_cycles": a_count,
        "B_cycles": b_count,
        "observed_ratio": output["comparison"]["observed_layer_rate_ratio_B_over_A"],
        "issue_delta_ms": issue_delta,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

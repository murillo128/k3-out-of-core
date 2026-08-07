#!/usr/bin/env python3
"""Verify and attribute the real Kimi-K3 MAX_SAFE Perfetto endpoint run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path
from typing import Any


MAX_TRACE_BYTES = 2 * 1024 * 1024 * 1024
EXPECTED_FORWARDS = 256
NVME0_DEV = 66305
LEAF_SCOPES = (
    "nvme_wait",
    "expert_compute",
    "cache_lookup",
    "cache_management",
    "router",
    "moe_nonexpert",
    "attention",
    "dense_trunk",
    "head",
)
LOSS_STATS = (
    "ftrace_cpu_dropped_events_delta",
    "ftrace_cpu_commit_overrun_delta",
    "traced_buf_incremental_sequences_dropped",
    "traced_buf_sequence_packet_loss",
    "traced_buf_trace_writer_packet_loss",
    "traced_final_flush_failed",
    "traced_flushes_failed",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256_file(path)}


def build_attribution(scope_ns: dict[str, int], wall_ns: int) -> tuple[dict[str, dict[str, Any]], int, int]:
    attributed_ns = sum(scope_ns.get(scope, 0) for scope in LEAF_SCOPES)
    residual_ns = max(0, wall_ns - attributed_ns)
    categories = {scope: {"duration_ns": scope_ns.get(scope, 0),
                          "fraction": scope_ns.get(scope, 0) / wall_ns if wall_ns else 0.0}
                  for scope in LEAF_SCOPES}
    categories["scheduler_or_uninstrumented_residual"] = {
        "duration_ns": residual_ns,
        "fraction": residual_ns / wall_ns if wall_ns else 1.0,
    }
    return categories, attributed_ns, residual_ns


def run_query(tool: Path, trace: Path, model_pid: int) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    loss_names = ",".join(f"'{name}'" for name in LOSS_STATS)
    sql = f"""
WITH
decode_forwards AS MATERIALIZED (
  SELECT ts, dur FROM slice
  WHERE name GLOB 'k3.forward:decode:*:-1' AND dur >= 0
),
decode_bounds AS MATERIALIZED (
  SELECT MIN(ts) AS start_ts, MAX(ts + dur) AS end_ts FROM decode_forwards
),
decode_scopes AS MATERIALIZED (
  SELECT name, ts, dur, SUBSTR(name, 4, INSTR(name, ':') - 4) AS scope
  FROM slice WHERE name GLOB 'k3.*:decode:*:*' AND dur >= 0
),
model_threads AS MATERIALIZED (
  SELECT t.utid FROM thread t JOIN process p USING (upid) WHERE p.pid = {model_pid}
)
SELECT 'summary' AS kind, 'decode_forward_count' AS label, CAST(COUNT(*) AS INT) AS value
FROM decode_forwards
UNION ALL SELECT 'summary', 'decode_forward_wall_ns', CAST(COALESCE(SUM(dur), 0) AS INT)
FROM decode_forwards
UNION ALL SELECT 'summary', 'incomplete_k3_slices', CAST(COUNT(*) AS INT)
FROM slice WHERE name GLOB 'k3.*' AND dur < 0
UNION ALL SELECT 'summary', 'all_k3_slices', CAST(COUNT(*) AS INT)
FROM slice WHERE name GLOB 'k3.*'
UNION ALL SELECT 'scope_count', scope, CAST(COUNT(*) AS INT)
FROM decode_scopes GROUP BY scope
UNION ALL SELECT 'scope_ns', scope, CAST(SUM(dur) AS INT)
FROM decode_scopes GROUP BY scope
UNION ALL SELECT 'loss', 'total', CAST(COALESCE(SUM(value), 0) AS INT)
FROM stats WHERE name IN ({loss_names})
UNION ALL SELECT 'kernel_count', 'block_rq_issue_all', CAST(COUNT(*) AS INT)
FROM raw WHERE name = 'block_rq_issue' AND CAST(EXTRACT_ARG(arg_set_id, 'dev') AS INT) = {NVME0_DEV}
UNION ALL SELECT 'kernel_bytes', 'block_rq_issue_all',
  CAST(COALESCE(SUM(COALESCE(CAST(EXTRACT_ARG(arg_set_id, 'bytes') AS INT),
    CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) * 512, 0)), 0) AS INT)
FROM raw WHERE name = 'block_rq_issue' AND CAST(EXTRACT_ARG(arg_set_id, 'dev') AS INT) = {NVME0_DEV}
UNION ALL SELECT 'kernel_count', 'block_rq_issue_decode', CAST(COUNT(*) AS INT)
FROM raw r
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'block_rq_issue' AND CAST(EXTRACT_ARG(r.arg_set_id, 'dev') AS INT) = {NVME0_DEV}
UNION ALL SELECT 'kernel_bytes', 'block_rq_issue_decode',
  CAST(COALESCE(SUM(COALESCE(CAST(EXTRACT_ARG(r.arg_set_id, 'bytes') AS INT),
    CAST(EXTRACT_ARG(r.arg_set_id, 'nr_sector') AS INT) * 512, 0)), 0) AS INT)
FROM raw r
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'block_rq_issue' AND CAST(EXTRACT_ARG(r.arg_set_id, 'dev') AS INT) = {NVME0_DEV}
UNION ALL SELECT 'kernel_count', 'pread_enter_decode', CAST(COUNT(*) AS INT)
FROM raw r JOIN model_threads mt USING (utid)
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sys_enter_pread64'
UNION ALL SELECT 'kernel_count', 'pread_exit_decode', CAST(COUNT(*) AS INT)
FROM raw r JOIN model_threads mt USING (utid)
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sys_exit_pread64'
UNION ALL SELECT 'kernel_bytes', 'pread_requested_decode',
  CAST(COALESCE(SUM(CAST(EXTRACT_ARG(r.arg_set_id, 'count') AS INT)), 0) AS INT)
FROM raw r JOIN model_threads mt USING (utid)
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sys_enter_pread64'
UNION ALL SELECT 'kernel_bytes', 'pread_returned_decode',
  CAST(COALESCE(SUM(MAX(0, CAST(EXTRACT_ARG(r.arg_set_id, 'ret') AS INT))), 0) AS INT)
FROM raw r JOIN model_threads mt USING (utid)
WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sys_exit_pread64'
UNION ALL SELECT 'kernel_count', 'sched_switch_decode', CAST(COUNT(*) AS INT)
FROM raw r WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sched_switch'
UNION ALL SELECT 'kernel_count', 'sched_waking_decode', CAST(COUNT(*) AS INT)
FROM raw r WHERE r.ts BETWEEN (SELECT start_ts FROM decode_bounds) AND (SELECT end_ts FROM decode_bounds)
  AND r.name = 'sched_waking'
UNION ALL SELECT 'thread_state_ns', COALESCE(s.state, 'unknown'),
  CAST(COALESCE(SUM(MAX(0, MIN(s.ts + s.dur, d.end_ts) - MAX(s.ts, d.start_ts))), 0) AS INT)
FROM thread_state s JOIN model_threads mt USING (utid)
JOIN decode_bounds d ON s.ts < d.end_ts AND s.ts + s.dur > d.start_ts
GROUP BY s.state
ORDER BY kind, label
"""
    completed = subprocess.run([str(tool), "-Q", sql, str(trace)], text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    rows = list(csv.DictReader(io.StringIO(completed.stdout)))
    if not rows:
        raise RuntimeError("trace processor returned no analysis rows")
    scalar: dict[str, int] = {}
    scope_ns: dict[str, int] = {}
    scope_count: dict[str, int] = {}
    for row in rows:
        value = int(row["value"])
        if row["kind"] == "scope_ns":
            scope_ns[row["label"]] = value
        elif row["kind"] == "scope_count":
            scope_count[row["label"]] = value
        else:
            scalar[f"{row['kind']}.{row['label']}"] = value
    return scalar, scope_ns, scope_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--traced-run", type=Path, required=True)
    parser.add_argument("--untraced-run", type=Path, required=True)
    parser.add_argument("--smoke-trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    tool = args.trace_processor.resolve()
    trace = args.trace.resolve()
    capture = json.loads(args.capture.read_text())
    traced = json.loads(args.traced_run.read_text())
    untraced = json.loads(args.untraced_run.read_text())
    model_pid = int(traced["process_resources"]["final_process_snapshot"]["pid"])
    scalar, scope_ns, scope_count = run_query(tool, trace, model_pid)

    failures: list[str] = []
    captured_trace = capture.get("files", {}).get("trace", {})
    if capture.get("status") != "complete" or captured_trace.get("sha256") != sha256_file(trace):
        failures.append("capture/trace identity mismatch")
    if trace.stat().st_size > MAX_TRACE_BYTES:
        failures.append("trace exceeds the fixed 2 GiB limit")
    if traced.get("status") != "PASS" or untraced.get("status") != "PASS":
        failures.append("traced or adjacent untraced run did not pass")
    if traced.get("token_ids_sha256") != untraced.get("token_ids_sha256"):
        failures.append("traced token sequence differs from adjacent untraced control")
    traced_route = traced.get("routing", {})
    untraced_route = untraced.get("routing", {})
    if traced_route.get("complete_decode_forwards") != EXPECTED_FORWARDS:
        failures.append("traced run has fewer than 256 complete decode forwards")
    if traced_route.get("normalized_route", {}).get("sha256") != untraced_route.get("normalized_route", {}).get("sha256"):
        failures.append("traced normalized route differs from adjacent untraced control")
    if int(traced.get("process_resources", {}).get("maximum_swap_bytes", -1)) != 0:
        failures.append("traced run used swap")
    if int(traced.get("process_resources", {}).get("maximum_rss_bytes", 0)) > int(
        traced.get("capacity", {}).get("accepted_process_rss_ceiling_bytes", 0)
    ):
        failures.append("traced run crossed the accepted RSS ceiling")
    if traced.get("cache", {}).get("final_run_counters", {}).get("v7") != 0:
        failures.append("traced run used buffered full-expert fallback")
    if traced.get("perf_stat_enabled") is not False or traced.get("trace_marker_transport") != "direct_tracefs_fd":
        failures.append("traced runner did not use the qualified direct marker transport")

    forward_count = scalar.get("summary.decode_forward_count", 0)
    wall_ns = scalar.get("summary.decode_forward_wall_ns", 0)
    if forward_count != EXPECTED_FORWARDS or scalar.get("summary.incomplete_k3_slices", -1) != 0:
        failures.append("Perfetto application-scope coverage is incomplete")
    if scope_count.get("forward") != EXPECTED_FORWARDS:
        failures.append("Perfetto forward-scope count differs from the endpoint run")
    missing_scopes = [scope for scope in LEAF_SCOPES if scope_count.get(scope, 0) == 0]
    if missing_scopes:
        failures.append("missing decode scope coverage: " + ",".join(missing_scopes))
    if scalar.get("loss.total", -1) != 0:
        failures.append("Perfetto/ftrace loss counters are nonzero")
    if scalar.get("kernel_count.pread_enter_decode", 0) != scalar.get("kernel_count.pread_exit_decode", -1):
        failures.append("decode pread64 enter/exit coverage differs")
    if scalar.get("kernel_bytes.pread_requested_decode", 0) != scalar.get("kernel_bytes.pread_returned_decode", -1):
        failures.append("decode pread64 requested/returned bytes differ")
    if scalar.get("kernel_count.block_rq_issue_decode", 0) == 0:
        failures.append("no decode NVMe block-dispatch evidence was captured")

    categories, attributed_ns, residual_ns = build_attribution(scope_ns, wall_ns)
    if attributed_ns > wall_ns:
        failures.append("application critical-path scopes overlap")
    accounted_ns = attributed_ns + residual_ns
    if accounted_ns != wall_ns:
        failures.append("non-overlapping attribution does not close to decode wall time")

    traced_runtime = traced.get("runtime", {})
    untraced_runtime = untraced.get("runtime", {})
    traced_tps = EXPECTED_FORWARDS / float(traced_runtime["decode_seconds"])
    untraced_tps = EXPECTED_FORWARDS / float(untraced_runtime["decode_seconds"])
    perturbation = {
        "traced_decode_tokens_per_second": traced_tps,
        "untraced_decode_tokens_per_second": untraced_tps,
        "decode_throughput_fraction": traced_tps / untraced_tps - 1.0,
        "mean_forward_latency_fraction": (
            float(traced_runtime["decode_forward_latency_seconds"]["mean"])
            / float(untraced_runtime["decode_forward_latency_seconds"]["mean"]) - 1.0
        ),
        "p95_forward_latency_fraction": (
            float(traced_runtime["decode_forward_latency_seconds"]["p95"])
            / float(untraced_runtime["decode_forward_latency_seconds"]["p95"]) - 1.0
        ),
    }
    block_stat_bytes = next(
        int(row["read_bytes"]) for row in traced["block_devices"]
        if row["stat_path"] == "/sys/class/block/nvme0n1/stat"
    )
    dispatched_bytes = scalar.get("kernel_bytes.block_rq_issue_all", 0)
    block_coverage_fraction = dispatched_bytes / block_stat_bytes if block_stat_bytes else 0.0
    if not 0.98 <= block_coverage_fraction <= 1.02:
        failures.append("trace block-dispatch bytes do not cover the run-level NVMe delta")

    ranked = sorted(
        ({"target": name, **values} for name, values in categories.items()),
        key=lambda row: row["duration_ns"], reverse=True,
    )[:3]
    document = {
        "schema_version": "phase12-nvme-colibri-endpoint-perfetto-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "rejected",
        "trace": identity(trace),
        "capture": identity(args.capture.resolve()),
        "trace_processor": identity(tool),
        "traced_run": identity(args.traced_run.resolve()),
        "adjacent_untraced_run": identity(args.untraced_run.resolve()),
        "qualified_smoke_trace": identity(args.smoke_trace.resolve()),
        "identity": {"model_pid": model_pid, "complete_decode_forwards": forward_count,
                     "token_ids_sha256": traced.get("token_ids_sha256"),
                     "normalized_route_sha256": traced_route.get("normalized_route", {}).get("sha256")},
        "application_trace": {"all_k3_slices": scalar.get("summary.all_k3_slices", 0),
                              "scope_counts": scope_count, "incomplete_slices": scalar.get("summary.incomplete_k3_slices", -1)},
        "loss_counters": {"sum": scalar.get("loss.total", -1)},
        "kernel_coverage": {
            "nvme0_block_issue_events_all": scalar.get("kernel_count.block_rq_issue_all", 0),
            "nvme0_block_issue_bytes_all": dispatched_bytes,
            "nvme0_run_stat_read_bytes": block_stat_bytes,
            "nvme0_block_dispatch_coverage_fraction": block_coverage_fraction,
            "nvme0_block_issue_events_decode": scalar.get("kernel_count.block_rq_issue_decode", 0),
            "nvme0_block_issue_bytes_decode": scalar.get("kernel_bytes.block_rq_issue_decode", 0),
            "pread64_enter_decode": scalar.get("kernel_count.pread_enter_decode", 0),
            "pread64_exit_decode": scalar.get("kernel_count.pread_exit_decode", 0),
            "pread64_requested_bytes_decode": scalar.get("kernel_bytes.pread_requested_decode", 0),
            "pread64_returned_bytes_decode": scalar.get("kernel_bytes.pread_returned_decode", 0),
            "sched_switch_decode": scalar.get("kernel_count.sched_switch_decode", 0),
            "sched_waking_decode": scalar.get("kernel_count.sched_waking_decode", 0),
            "thread_state_duration_ns": {key.split('.', 1)[1]: value for key, value in scalar.items()
                                          if key.startswith("thread_state_ns.")},
            "claim_boundary": "block_rq_issue proves physical dispatch count/bytes; completion/service duration was intentionally omitted after bounded trace-volume qualification",
        },
        "critical_path_attribution": {
            "decode_forward_wall_ns": wall_ns,
            "categories": categories,
            "accounted_ns": accounted_ns,
            "non_overlapping": accounted_ns == wall_ns and attributed_ns <= wall_ns,
        },
        "trace_perturbation": perturbation,
        "ranked_optimization_targets": ranked,
        "resource_correctness": {
            "maximum_rss_bytes": traced.get("process_resources", {}).get("maximum_rss_bytes"),
            "accepted_rss_ceiling_bytes": traced.get("capacity", {}).get("accepted_process_rss_ceiling_bytes"),
            "maximum_swap_bytes": traced.get("process_resources", {}).get("maximum_swap_bytes"),
            "buffered_full_expert_fallbacks": traced.get("cache", {}).get("final_run_counters", {}).get("v7"),
        },
        "failures": failures,
        "next_action": (
            "freeze the bounded endpoint attribution and refresh the Phase 12 #44 handoff"
            if not failures else "exclude the trace and repeat only after correcting the identified capture-integrity cause"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "failures": failures,
                      "trace_perturbation": perturbation}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

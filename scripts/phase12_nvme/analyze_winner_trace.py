#!/usr/bin/env python3
"""Verify and attribute the issue #58 winner Perfetto capture."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path

USEFUL_BYTES = 25_829_572_608
OPERATIONS = 1_472
EXPECTED_SINK = "205a762e95ada0c9d731c7d47ef41adda5a4ef9fbd8ea650eb91a74b9207956d"
MAX_TRACE_BYTES = 2 * 1024 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def query(tool: Path, trace: Path, sql: str) -> list[dict[str, str]]:
    completed = subprocess.run(
        [str(tool), "-Q", sql, str(trace)], text=True, capture_output=True,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return list(csv.DictReader(io.StringIO(completed.stdout)))


def one(tool: Path, trace: Path, sql: str) -> dict[str, int]:
    rows = query(tool, trace, sql)
    if len(rows) != 1:
        raise ValueError(f"expected one query row, observed {len(rows)}")
    return {key: int(value) for key, value in rows[0].items()}


def union_sql(intervals: str) -> str:
    return f"""
WITH intervals AS MATERIALIZED ({intervals}),
events AS MATERIALIZED (
  SELECT start_ts AS ts, 1 AS delta FROM intervals WHERE end_ts > start_ts
  UNION ALL SELECT end_ts, -1 FROM intervals WHERE end_ts > start_ts
), points AS MATERIALIZED (SELECT ts, SUM(delta) AS delta FROM events GROUP BY ts),
sweep AS MATERIALIZED (
  SELECT ts, LEAD(ts) OVER (ORDER BY ts) AS end_ts,
    SUM(delta) OVER (ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS active
  FROM points
)
SELECT CAST(COALESCE(SUM(CASE WHEN active > 0 THEN end_ts - ts ELSE 0 END), 0) AS INT) AS union_ns
FROM sweep
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--traced-cell", type=Path, required=True)
    parser.add_argument("--untraced-cell", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    tool = args.trace_processor.resolve()
    trace = args.trace.resolve()
    capture = json.loads(args.capture.read_text())
    traced = json.loads(args.traced_cell.read_text())
    untraced = json.loads(args.untraced_cell.read_text())
    workload_pid = int(capture["capture"]["workload_pid"])

    request = one(tool, trace, """
SELECT CAST(COUNT(*) AS INT) AS count, CAST(COALESCE(MIN(ts), 0) AS INT) AS start_ts,
  CAST(COALESCE(MAX(ts + dur), 0) AS INT) AS end_ts, CAST(COALESCE(SUM(dur), 0) AS INT) AS wall_ns
FROM slice WHERE category = 'k3.request' AND name = 'request' AND dur >= 0
""")
    start = request["start_ts"]
    end = request["end_ts"]
    application = one(tool, trace, f"""
SELECT
  CAST((SELECT COUNT(*) FROM slice WHERE category = 'k3.storage' AND name = 'storage_operation') AS INT) AS storage_operations,
  CAST((SELECT COUNT(*) FROM slice WHERE category = 'k3.storage' AND name = 'storage_operation' AND dur < 0) AS INT) AS incomplete_storage_operations,
  CAST((SELECT COUNT(*) FROM slice WHERE category = 'k3.provider' AND name = 'provider_request' AND dur >= 0) AS INT) AS provider_requests,
  CAST((SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_start') AS INT) AS trace_starts,
  CAST((SELECT COUNT(*) FROM slice WHERE category = 'k3.lifecycle' AND name = 'trace_session_stop') AS INT) AS trace_stops,
  CAST((SELECT COUNT(*) FROM counter c JOIN counter_track ct ON ct.id = c.track_id
    WHERE ct.name IN ('storage_requested_qd', 'storage_max_active')) AS INT) AS resource_counters
""")
    syscalls = one(tool, trace, f"""
SELECT
  CAST(SUM(CASE WHEN r.name = 'sys_enter_pread64' THEN 1 ELSE 0 END) AS INT) AS pread_enters,
  CAST(SUM(CASE WHEN r.name = 'sys_exit_pread64' THEN 1 ELSE 0 END) AS INT) AS pread_exits,
  CAST(COALESCE(SUM(CASE WHEN r.name = 'sys_enter_pread64' THEN CAST(EXTRACT_ARG(r.arg_set_id, 'count') AS INT) ELSE 0 END), 0) AS INT) AS requested_bytes,
  CAST(COALESCE(SUM(CASE WHEN r.name = 'sys_exit_pread64' THEN CAST(EXTRACT_ARG(r.arg_set_id, 'ret') AS INT) ELSE 0 END), 0) AS INT) AS returned_bytes,
  CAST(SUM(CASE WHEN r.name = 'sys_enter_futex' THEN 1 ELSE 0 END) AS INT) AS futex_enters
FROM raw r JOIN thread t ON t.utid = r.utid JOIN process p ON p.upid = t.upid
WHERE p.pid = {workload_pid} AND r.ts BETWEEN {start} AND {end}
""")
    block = one(tool, trace, f"""
SELECT
  CAST(SUM(CASE WHEN name = 'block_rq_issue' THEN 1 ELSE 0 END) AS INT) AS issues,
  CAST(SUM(CASE WHEN name = 'block_rq_complete' THEN 1 ELSE 0 END) AS INT) AS completes,
  CAST(COALESCE(SUM(CASE WHEN name = 'block_rq_issue' THEN CAST(EXTRACT_ARG(arg_set_id, 'bytes') AS INT) ELSE 0 END), 0) AS INT) AS issued_bytes,
  CAST(COALESCE(SUM(CASE WHEN name = 'block_rq_complete' THEN CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) * 512 ELSE 0 END), 0) AS INT) AS completed_bytes,
  CAST(SUM(CASE WHEN name = 'block_rq_complete' AND CAST(EXTRACT_ARG(arg_set_id, 'error') AS INT) != 0 THEN 1 ELSE 0 END) AS INT) AS errors
FROM raw WHERE name IN ('block_rq_issue', 'block_rq_complete')
  AND ts BETWEEN {start} AND {end} AND CAST(EXTRACT_ARG(arg_set_id, 'dev') AS INT) = 66305
""")
    losses = one(tool, trace, """
SELECT CAST(COALESCE(SUM(value), 0) AS INT) AS count FROM stats
WHERE name IN ('ftrace_cpu_dropped_events_delta', 'ftrace_cpu_commit_overrun_delta',
  'traced_buf_incremental_sequences_dropped', 'traced_buf_sequence_packet_loss',
  'traced_buf_trace_writer_packet_loss', 'traced_final_flush_failed', 'traced_flushes_failed')
""")
    storage_union = one(tool, trace, union_sql(f"""
SELECT MAX(ts, {start}) AS start_ts, MIN(ts + dur, {end}) AS end_ts
FROM slice WHERE category = 'k3.storage' AND name = 'storage_operation'
  AND dur >= 0 AND ts < {end} AND ts + dur > {start}
"""))["union_ns"]
    syscall_union = one(tool, trace, union_sql(f"""
WITH enters AS (
  SELECT r.utid, r.ts AS start_ts, ROW_NUMBER() OVER (PARTITION BY r.utid ORDER BY r.ts) AS ordinal
  FROM raw r JOIN thread t ON t.utid = r.utid JOIN process p ON p.upid = t.upid
  WHERE p.pid = {workload_pid} AND r.name = 'sys_enter_pread64' AND r.ts BETWEEN {start} AND {end}
), exits AS (
  SELECT r.utid, r.ts AS end_ts, ROW_NUMBER() OVER (PARTITION BY r.utid ORDER BY r.ts) AS ordinal
  FROM raw r JOIN thread t ON t.utid = r.utid JOIN process p ON p.upid = t.upid
  WHERE p.pid = {workload_pid} AND r.name = 'sys_exit_pread64' AND r.ts BETWEEN {start} AND {end}
)
SELECT e.start_ts, x.end_ts FROM enters e JOIN exits x USING (utid, ordinal)
"""))["union_ns"]
    block_union = one(tool, trace, union_sql(f"""
WITH issues AS (
  SELECT ts AS start_ts, CAST(EXTRACT_ARG(arg_set_id, 'sector') AS INT) AS sector,
    CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) AS sectors,
    ROW_NUMBER() OVER (PARTITION BY CAST(EXTRACT_ARG(arg_set_id, 'sector') AS INT),
      CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) ORDER BY ts) AS ordinal
  FROM raw WHERE name = 'block_rq_issue' AND ts BETWEEN {start} AND {end}
    AND CAST(EXTRACT_ARG(arg_set_id, 'dev') AS INT) = 66305
), completes AS (
  SELECT ts AS end_ts, CAST(EXTRACT_ARG(arg_set_id, 'sector') AS INT) AS sector,
    CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) AS sectors,
    ROW_NUMBER() OVER (PARTITION BY CAST(EXTRACT_ARG(arg_set_id, 'sector') AS INT),
      CAST(EXTRACT_ARG(arg_set_id, 'nr_sector') AS INT) ORDER BY ts) AS ordinal
  FROM raw WHERE name = 'block_rq_complete' AND ts BETWEEN {start} AND {end}
    AND CAST(EXTRACT_ARG(arg_set_id, 'dev') AS INT) = 66305
)
SELECT i.start_ts, c.end_ts FROM issues i JOIN completes c USING (sector, sectors, ordinal)
WHERE c.end_ts >= i.start_ts
"""))["union_ns"]
    thread_states = query(tool, trace, f"""
SELECT state, CAST(COUNT(*) AS INT) AS intervals,
  CAST(SUM(MAX(0, MIN(ts + dur, {end}) - MAX(ts, {start}))) AS INT) AS duration_ns
FROM thread_state s JOIN thread t USING (utid) JOIN process p USING (upid)
WHERE p.pid = {workload_pid} AND s.ts < {end} AND s.ts + s.dur > {start}
GROUP BY state ORDER BY duration_ns DESC
""")
    faults = one(tool, trace, f"""
SELECT CAST(COUNT(*) AS INT) AS count FROM raw r
JOIN thread t ON t.utid = r.utid JOIN process p ON p.upid = t.upid
WHERE p.pid = {workload_pid} AND r.name IN ('page_fault_user', 'page_fault_kernel')
  AND r.ts BETWEEN {start} AND {end}
""")["count"]

    failures: list[str] = []
    captured_trace = capture.get("files", {}).get("trace", {})
    if capture.get("status") != "complete" or captured_trace.get("sha256") != sha256_file(trace):
        failures.append("capture/trace identity mismatch")
    if trace.stat().st_size > MAX_TRACE_BYTES:
        failures.append("trace exceeds 2 GiB")
    for label, cell in (("traced", traced), ("untraced", untraced)):
        if cell.get("status") != "PASS" or cell.get("checksum_sink_sha256") != EXPECTED_SINK:
            failures.append(f"{label} cell correctness failure")
        if int(cell.get("useful_bytes", 0)) != USEFUL_BYTES or int(cell.get("short_reads", 0)) != 0:
            failures.append(f"{label} cell byte-count/short-read failure")
        if int(cell.get("requested_qd", 0)) != 32 or int(cell.get("maximum_active_operations", 0)) != 32:
            failures.append(f"{label} cell effective-QD failure")
        if int(cell.get("swap_used_bytes", 0)) or cell.get("lifetime_resources") != {"fd_delta": 0, "thread_delta": 0}:
            failures.append(f"{label} cell resource failure")
    expected_application = {
        "storage_operations": OPERATIONS, "incomplete_storage_operations": 0,
        "provider_requests": 1, "trace_starts": 1, "trace_stops": 1, "resource_counters": 2,
    }
    if application != expected_application or request["count"] != 1:
        failures.append("application trace coverage mismatch")
    if syscalls["pread_enters"] != OPERATIONS or syscalls["pread_exits"] != OPERATIONS:
        failures.append("pread64 trace boundary loss")
    if syscalls["requested_bytes"] != USEFUL_BYTES or syscalls["returned_bytes"] != USEFUL_BYTES:
        failures.append("pread64 byte coverage mismatch")
    if block["issued_bytes"] != USEFUL_BYTES or block["completed_bytes"] != USEFUL_BYTES or block["errors"]:
        failures.append("NVMe block trace byte/error mismatch")
    if losses["count"]:
        failures.append("Perfetto/ftrace loss counters are nonzero")
    request_wall = request["wall_ns"]
    attribution = {
        "request_wall_ns": request_wall,
        "block_device_service_union_ns": block_union,
        "syscall_non_block_union_ns": max(0, syscall_union - block_union),
        "checksum_copy_union_ns": max(0, storage_union - syscall_union),
        "scheduler_or_unattributed_union_ns": max(0, request_wall - storage_union),
        "storage_operation_union_ns": storage_union,
        "pread_syscall_union_ns": syscall_union,
    }
    attribution["accounted_ns"] = sum(attribution[key] for key in (
        "block_device_service_union_ns", "syscall_non_block_union_ns",
        "checksum_copy_union_ns", "scheduler_or_unattributed_union_ns",
    ))
    residual_fraction = attribution["scheduler_or_unattributed_union_ns"] / request_wall if request_wall else 1.0
    if residual_fraction > 0.05:
        failures.append("material scheduler/unattributed critical-path residual")
    perturbation = float(traced["useful_gbps"]) / float(untraced["useful_gbps"]) - 1.0
    document = {
        "schema_version": "phase12-nvme-winner-trace-analysis-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "rejected",
        "trace": identity(trace),
        "capture": identity(args.capture),
        "trace_processor": identity(tool),
        "traced_cell": identity(args.traced_cell),
        "untraced_cell": identity(args.untraced_cell),
        "application": application,
        "request": request,
        "syscalls": syscalls,
        "block_device": block,
        "loss_counters": losses,
        "thread_state_totals": [
            {"state": row["state"], "intervals": int(row["intervals"]), "duration_ns": int(row["duration_ns"])}
            for row in thread_states
        ],
        "page_fault_events": faults,
        "critical_path_attribution": attribution,
        "unexplained_residual_fraction": residual_fraction,
        "trace_throughput_perturbation": perturbation,
        "correctness": {"checksum_sink_sha256": EXPECTED_SINK, "useful_bytes": USEFUL_BYTES},
        "failures": failures,
        "next_action": (
            "freeze trace and continue to dual-NVMe evidence" if not failures
            else "exclude this trace and repeat only after correcting the identified capture-integrity cause"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "failures": failures, "perturbation": perturbation}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify a Phase 12.5 full-stack trace and emit machine-readable evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import ROOT, file_identity, query_trace, scalar, sha256, trace_processor_version, write_json

SQL = ROOT / "scripts/phase12_5/sql/verify_full_stack.sql"
MAX_TRACE_BYTES = 2 * 1024 * 1024 * 1024
MAX_CUPTI_BYTES = 256 * 1024 * 1024


def check(row: dict[str, str], profile: str) -> list[str]:
    errors: list[str] = []
    exact_zero = (
        "incomplete_application_slices", "cuda_packet_order_regressions", "incomplete_cuda_slices",
        "invalid_cuda_intervals", "invalid_flows", "cuda_raw_clock_mismatches",
        "required_source_loss", "cupti_errors",
        "cupti_dropped_records", "cupti_unknown_timestamps",
    )
    for key in exact_zero:
        if scalar(row, key) != 0:
            errors.append(f"{key}={row[key]} (expected zero)")
    minimums = {
        "slice_count": 1, "application_category_count": 3, "trace_start_count": 1,
        "trace_stop_count": 1, "teardown_slice_count": 1, "cuda_kernel_count": 1,
        "cuda_memcpy_count": 1, "cuda_sync_count": 1, "cuda_kernel_api_matches": 1,
        "cuda_memcpy_api_matches": 1, "cuda_application_nonzero": 1, "cuda_application_matches": 1,
        "graph_kernel_matches": 1, "cuda_clock_sample_count": 1, "common_clock_snapshots": 1,
        "sched_switch_count": 1,
        "sched_wake_count": 1, "syscall_enter_count": 1, "fault_event_count": 1,
        "process_stat_count": 1, "system_stat_count": 1,
    }
    if profile == "provider":
        minimums.update({"application_category_count": 10, "flow_count": 1,
            "flight_memcpy_matches": 1, "storage_syscall_count": 1, "filemap_event_count": 1})
        if scalar(row, "zero_correlated_sync_in_request") != 0:
            errors.append("zero-correlated CUDA synchronization overlaps a provider request")
    for key, minimum in minimums.items():
        if scalar(row, key) < minimum:
            errors.append(f"{key}={row[key]} (expected >= {minimum})")
    if scalar(row, "trace_start_count") != 1 or scalar(row, "trace_stop_count") != 1:
        errors.append("trace lifecycle must contain exactly one start and one stop")
    if scalar(row, "cuda_application_matches") != scalar(row, "cuda_application_nonzero"):
        errors.append("one or more non-zero CUPTI application correlations do not resolve")
    if scalar(row, "cuda_kernel_api_matches") != scalar(row, "cuda_kernel_count"):
        errors.append("one or more kernels do not resolve to a CUPTI API correlation")
    if scalar(row, "clock_anchor_residual_ns") > 1_000_000:
        errors.append(f"common-clock anchor residual exceeds 1 ms: {row['clock_anchor_residual_ns']}")
    if scalar(row, "cupti_peak_total_bytes") < 0 or scalar(row, "cupti_peak_total_bytes") > MAX_CUPTI_BYTES:
        errors.append("CUPTI shared peak exceeds the 256 MiB hard bound or is absent")
    if scalar(row, "cupti_retained_capacity_bytes") < 0 or scalar(row, "cupti_retained_capacity_bytes") > MAX_CUPTI_BYTES:
        errors.append("CUPTI retained capacity exceeds the 256 MiB hard bound or is absent")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--capture-metadata", type=Path, required=True)
    parser.add_argument("--profile", choices=("tiny", "provider"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    capture = json.loads(args.capture_metadata.read_text())
    errors: list[str] = []
    if capture.get("schema_version") != "phase12-5-capture-v1" or capture.get("status") != "complete":
        errors.append("capture metadata is incomplete or has the wrong schema")
    captured_trace = capture.get("files", {}).get("trace", {})
    if captured_trace.get("size") != args.trace.stat().st_size or captured_trace.get("sha256") != sha256(args.trace):
        errors.append("trace identity differs from capture metadata")
    unavailable = [item["event"] for item in capture.get("config", {}).get("ftrace_events", [])
        if not item.get("available")]
    if unavailable:
        errors.append("capture preflight reported unavailable ftrace events: " + ",".join(unavailable))
    if args.trace.stat().st_size > MAX_TRACE_BYTES:
        errors.append("trace exceeds the 2 GiB hard maximum")
    rows = query_trace(args.trace_processor, args.trace, SQL)
    if len(rows) != 1:
        raise ValueError(f"verification SQL returned {len(rows)} rows")
    errors.extend(check(rows[0], args.profile))
    result = {
        "schema_version": "phase12-5-trace-verification-v1",
        "status": "valid" if not errors else "invalid",
        "profile": args.profile,
        "trace": file_identity(args.trace),
        "capture_metadata": file_identity(args.capture_metadata),
        "trace_processor": {**file_identity(args.trace_processor),
            "version": trace_processor_version(args.trace_processor)},
        "sql": file_identity(SQL),
        "metrics": {key: int(value) if value != "[NULL]" else None for key, value in rows[0].items()},
        "errors": errors,
    }
    write_json(args.output, result, replace=args.replace)
    print(json.dumps({"status": result["status"], "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail closed on the amended Phase 13 filtered decode-window trace contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.phase12_5.common import file_identity, query_trace, scalar, trace_processor_version, write_json

SQL = ROOT / "scripts/phase13/sql/verify_decode_window.sql"
MAX_TRACE_BYTES = 256 * 1024 * 1024
MAX_CUPTI_BYTES = 128 * 1024 * 1024


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--case", choices=("A", "B"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text())
    workload = json.loads(args.workload.read_text())
    rows = query_trace(args.trace_processor, args.trace, SQL)
    if len(rows) != 1:
        raise RuntimeError(f"trace verification returned {len(rows)} rows")
    metrics = {key: int(value) if value != "[NULL]" else None for key, value in rows[0].items()}
    errors: list[str] = []
    if capture.get("status") != "complete" or capture.get("case") != args.case:
        errors.append("capture metadata is incomplete or identifies another case")
    if workload.get("status") != "pass" or len(workload.get("generated_ids", [])) != 24:
        errors.append("workload is incomplete")
    if args.trace.stat().st_size > MAX_TRACE_BYTES:
        errors.append("trace exceeds 256 MiB")
    exact_one = ("window_start_count", "window_end_count", "session_start_count", "session_stop_count")
    for key in exact_one:
        if metrics[key] != 1:
            errors.append(f"{key}={metrics[key]} (expected 1)")
    exact_zero = (
        "initialization_slice_count", "forbidden_cuda_slice_count", "unexpected_cuda_slice_count",
        "external_correlation_argument_count", "invalid_cuda_interval_count", "trace_data_loss",
        "cupti_errors", "cupti_dropped_records", "cupti_unknown_timestamps",
        "cupti_unmatched_correlations",
    )
    for key in exact_zero:
        if metrics[key] != 0:
            errors.append(f"{key}={metrics[key]} (expected 0)")
    for key in ("cuda_kernel_count", "cuda_memcpy_count", "cuda_sync_count"):
        if not metrics[key] or metrics[key] < 1:
            errors.append(f"{key}={metrics[key]} (expected >=1)")
    if not metrics["complete_routed_layer_intervals"] or metrics["complete_routed_layer_intervals"] < 3:
        errors.append("fewer than three complete routed-layer intervals")
    for trace_count, retained_count in (
            ("cuda_kernel_count", "cupti_kernel_records"),
            ("cuda_memcpy_count", "cupti_memcpy_records"),
            ("cuda_sync_count", "cupti_sync_records")):
        if metrics[trace_count] != metrics[retained_count]:
            errors.append(f"{trace_count} differs from {retained_count}")
    if metrics["cupti_peak_total_bytes"] is None or not 0 < metrics["cupti_peak_total_bytes"] <= MAX_CUPTI_BYTES:
        errors.append("CUPTI peak is absent or exceeds 128 MiB")

    diagnostics = workload.get("perfetto", {})
    requested_ms = diagnostics.get("decode_window_requested_ms")
    actual_ns = diagnostics.get("clock_stop_ns", 0) - diagnostics.get("clock_start_ns", 0)
    if requested_ms not in {1000, 500, 250}:
        errors.append("workload reports an invalid requested window")
    elif not requested_ms * 900_000 <= actual_ns <= requested_ms * 1_250_000:
        errors.append(f"decode-window duration {actual_ns} ns is outside the bounded tolerance")
    for key in ("decode_window_armed", "decode_window_triggered", "decode_window_complete", "shutdown"):
        if diagnostics.get(key) is not True:
            errors.append(f"workload Perfetto diagnostic {key} is not true")
    if diagnostics.get("cupti_enabled_kind_count") != 3:
        errors.append("CUPTI enabled-kind count is not exactly three")
    peers = workload.get("multi_gpu", {}).get("peer_diagnostics", [])
    if any(peer.get("unexpected_host_synchronizations") != 0 for peer in peers):
        errors.append("unexpected host synchronization appears in workload telemetry")
    if args.case == "B":
        staged_copies = sum(peer.get("host_staged_copies", 0) for peer in peers)
        if staged_copies < 1 or metrics["h2d_memcpy_count"] < 1 or metrics["d2h_memcpy_count"] < 1:
            errors.append("B lacks a traced activation/result transfer through HOST_STAGED")

    result = {
        "schema_version": "phase13-decode-window-verification-v1",
        "status": "valid" if not errors else "invalid",
        "case": args.case,
        "trace": file_identity(args.trace),
        "workload": file_identity(args.workload),
        "capture": file_identity(args.capture),
        "trace_processor": {**file_identity(args.trace_processor),
            "version": trace_processor_version(args.trace_processor)},
        "sql": file_identity(SQL),
        "metrics": metrics,
        "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({"status": result["status"], "case": args.case, "errors": errors}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

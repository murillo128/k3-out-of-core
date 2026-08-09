#!/usr/bin/env python3
"""Summarize issue 69 Delta D cold-cache decision and final SSD evidence."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import gzip
import json
from pathlib import Path
import re

from analyze_delta_d import (BLOCK_STAT, aggregate_cells, file_identity, matrix_runs,
                             traces, workload_summary)
from common import write_json


def load_gzip(path: Path) -> dict[str, object]:
    with gzip.open(path, "rt") as source:
        return json.load(source)


def one_run(directory: Path, cell: str) -> dict[str, object]:
    workload_path = next((directory / "raw").glob(f"{cell}-*.json.gz"))
    resource_path = workload_path.with_name(
        workload_path.name.removesuffix(".json.gz") + "-resources.json"
    )
    workload = load_gzip(workload_path)
    resource = json.loads(resource_path.read_text())
    summary = workload_summary(workload_path)
    summary["resources"] = {
        "file": file_identity(resource_path),
        "elapsed_seconds": resource["elapsed_seconds"],
        "cache_state": resource["cache_state"],
        "process_io_maxima": resource["process_io_maxima"],
        "block_device_delta": resource["block_devices"]["delta"][BLOCK_STAT],
        "build": resource["build"],
    }
    summary["candidate_mechanism"] = {
        key: workload["mechanism"].get(key, 0)
        for key in (
            "async_cold_fill_configured", "async_cold_fill_attempts",
            "async_cold_fill_queued", "async_cold_fill_dropped",
            "async_cold_fill_completed", "async_cold_fill_failed",
            "async_cold_fill_bytes", "async_cold_fill_time_us",
            "async_cold_fill_active", "async_cold_fill_peak_active",
        )
    }
    return summary


def candidate_comparison(direct_dir: Path, candidate_dir: Path) -> dict[str, object]:
    cells: dict[str, object] = {}
    for cell in ("S0", "A1"):
        direct = one_run(direct_dir, cell)
        candidate = one_run(candidate_dir, cell)
        direct_block = int(direct["resources"]["block_device_delta"]["read_bytes"])
        candidate_block = int(candidate["resources"]["block_device_delta"]["read_bytes"])
        direct_process = int(direct["resources"]["process_io_maxima"]["io_read_bytes"])
        candidate_process = int(candidate["resources"]["process_io_maxima"]["io_read_bytes"])
        cells[cell] = {
            "direct": direct,
            "async_cold_fill": candidate,
            "async_vs_direct_decode_ratio": candidate["decode_tps"] / direct["decode_tps"],
            "logical_read_request_reduction_fraction": 1 -
                candidate["storage_read_requests"] / direct["storage_read_requests"],
            "logical_read_byte_reduction_fraction": 1 -
                candidate["storage_read_bytes"] / direct["storage_read_bytes"],
            "block_read_reduction_fraction": (direct_block - candidate_block) / direct_block,
            "process_physical_read_reduction_fraction":
                (direct_process - candidate_process) / direct_process,
            "exact_generated_identity":
                direct["generated_identity_sha256"] == candidate["generated_identity_sha256"],
            "exact_numerical_identity":
                direct["numerical_identity_sha256"] == candidate["numerical_identity_sha256"],
        }
    return {
        "cells": cells,
        "selected": False,
        "selection_gate": (
            "materially reduce physical backing reads and improve or preserve decode throughput"
        ),
        "decision": (
            "reject async cold fill: logical reads fell, physical reads were flat, and decode regressed"
        ),
        "higher_capacity_candidate_runs_performed": False,
    }


def request_sequence(path: Path) -> list[tuple[tuple[int, int], int]]:
    workload = load_gzip(path)
    grouped: dict[tuple[int, int, int, int, int, int], dict[str, int]] = {}
    for item in workload["async_io"]["read_intervals"]:
        identity = (
            int(item["transport_epoch"]), int(item["request_slot"]),
            int(item["request_generation"]), int(item["layer"]), int(item["expert"]),
            int(item["layout_class_id"]),
        )
        group = grouped.setdefault(identity, {"queued_us": int(item["queued_us"]), "bytes": 0})
        group["queued_us"] = min(group["queued_us"], int(item["queued_us"]))
        group["bytes"] += int(item["useful_bytes"])
    ordered = sorted(grouped.items(), key=lambda item: (item[1]["queued_us"], item[0][1], item[0][2]))
    return [((identity[3], identity[4]), values["bytes"]) for identity, values in ordered]


def simulate_lru(sequence: list[tuple[tuple[int, int], int]], slots: int) -> dict[str, int]:
    resident: OrderedDict[tuple[int, int], None] = OrderedDict()
    hits = 0
    avoided_bytes = 0
    for key, byte_count in sequence:
        if key in resident:
            hits += 1
            avoided_bytes += byte_count
            resident.move_to_end(key)
            continue
        resident[key] = None
        if len(resident) > slots:
            resident.popitem(last=False)
    return {"slots": slots, "hits": hits, "avoided_bytes": avoided_bytes}


def reuse_opportunity(compliance_dir: Path, slot_footprint: int) -> dict[str, object]:
    result: dict[str, object] = {}
    for cell in ("S0", "S1", "A1"):
        path = next((compliance_dir / "raw").glob(f"{cell}-*.json.gz"))
        sequence = request_sequence(path)
        unique = len({key for key, _ in sequence})
        seen: set[tuple[int, int]] = set()
        repeat_bytes = 0
        for key, byte_count in sequence:
            if key in seen:
                repeat_bytes += byte_count
            else:
                seen.add(key)
        result[cell] = {
            "source": file_identity(path),
            "requests": len(sequence),
            "unique_keys": unique,
            "unbounded_repeat_requests": len(sequence) - unique,
            "unbounded_repeat_bytes": repeat_bytes,
            "lru_upper_bounds": {
                str(budget_gib): simulate_lru(
                    sequence, (budget_gib * 1024**3) // slot_footprint
                )
                for budget_gib in (16, 32, 64)
            },
        }
    return {"slot_footprint_bytes": slot_footprint, "cells": result}


def direct_capacity_controls(directory: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for budget in ("32gib", "64gib"):
        result[budget] = aggregate_cells(matrix_runs(directory / budget))
    return {
        "runs": result,
        "inert": all(
            run["workload"]["cold_hits"] == 0 and
            run["workload"]["cold_admissions"] == 0
            for budget in result.values() for cell in budget.values() for run in cell["runs"]
        ),
    }


def profiles(raw_dir: Path, rendered_dir: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for cell in ("S0", "A1"):
        capture_path = raw_dir / cell / "capture.json"
        summary_path = rendered_dir / cell.lower() / "summary.json"
        capture = json.loads(capture_path.read_text())
        summary = json.loads(summary_path.read_text())
        result[cell] = {
            "capture": file_identity(capture_path),
            "summary": file_identity(summary_path),
            "workload": workload_summary(raw_dir / cell / "workload.json"),
            "cache_state": capture["cache_state"],
            "block_device_delta": capture["block_devices"]["delta"][BLOCK_STAT],
            "perf_data": capture["perf_data"],
            "perf_stat": summary["perf_stat"],
            "storage_worker_reliable": summary["selection"]["storage_worker_reliable"],
            "attribution": {
                selector: {
                    "samples": summary["artifacts"][selector]["attribution"]["samples"],
                    "buckets": summary["artifacts"][selector]["attribution"]["buckets"],
                    "top_inclusive_functions":
                        summary["artifacts"][selector]["attribution"]
                        ["top_inclusive_functions"][:10],
                    "top_leaf_functions":
                        summary["artifacts"][selector]["attribution"]
                        ["top_leaf_functions"][:10],
                }
                for selector in ("process", "main", "storage")
            },
            "artifacts": {
                selector: {
                    "folded": summary["artifacts"][selector]["folded"],
                    "svg": summary["artifacts"][selector]["svg"],
                }
                for selector in ("process", "main", "storage")
            },
        }
    return result


def focused_ctest(path: Path) -> dict[str, object]:
    text = path.read_text()
    match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", text)
    if match is None:
        raise ValueError(f"focused CTest summary is missing from {path}")
    percent, failed, total = (int(value) for value in match.groups())
    passed = total - failed
    if percent != 100 or failed != 0 or total != 12:
        raise ValueError(f"focused CTest did not pass 12/12: {path}")
    return {
        "log": file_identity(path),
        "tests_passed": passed,
        "tests_failed": failed,
        "tests_total": total,
        "gate": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--d1-manifest", type=Path, required=True)
    parser.add_argument("--compliance-dir", type=Path, required=True)
    parser.add_argument("--direct-capacity-dir", type=Path, required=True)
    parser.add_argument("--direct-screen-dir", type=Path, required=True)
    parser.add_argument("--candidate-screen-dir", type=Path, required=True)
    parser.add_argument("--candidate-source-dir", type=Path, required=True)
    parser.add_argument("--ctest-log", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--profile-raw-dir", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--slot-footprint-bytes", type=int, default=10878976)
    parser.add_argument("--raw-release")
    parser.add_argument("--raw-asset")
    parser.add_argument("--raw-size", type=int)
    parser.add_argument("--raw-sha256")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    d1 = json.loads(args.d1_manifest.read_text())
    comparison = candidate_comparison(args.direct_screen_dir, args.candidate_screen_dir)
    trace_result = traces(args.trace_dir, args.trace_processor)
    profile_result = profiles(args.profile_raw_dir, args.profile_dir)
    value = {
        "schema_version": "issue69-delta-d-final-v1",
        "status": "pass",
        "checkpoint": "DELTA_D_PHYSICAL_SSD_FINAL",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "d1": {
            "manifest": file_identity(args.d1_manifest),
            "selected_worker_count": d1["worker_screen"]["selection"]["selected_worker_count"],
            "unchanged_runtime": True,
            "physical_vs_tmpfs_decode_ratio": d1["physical_vs_tmpfs_decode_ratio"],
        },
        "d2": {
            "reuse_opportunity": reuse_opportunity(
                args.compliance_dir, args.slot_footprint_bytes
            ),
            "direct_capacity_controls": direct_capacity_controls(args.direct_capacity_dir),
            "candidate_comparison": comparison,
            "candidate_source": {
                path.name: file_identity(path)
                for path in sorted(args.candidate_source_dir.glob("*.patch"))
            },
            "selected_runtime": "UNCHANGED_DIRECT_PROMOTION",
            "cold_admissions_selected": 0,
        },
        "d3": {
            "traces": trace_result,
            "profiles": profile_result,
            "dominant_critical_path": {
                "S0": "exposed ext4 storage/page-cache service with limited useful GPU work",
                "A1": "exposed ext4 storage/page-cache service plus scheduler/policy CPU bookkeeping",
                "basis": (
                    "trace service unions and storage-worker on-CPU attribution; unions are not additive"
                ),
            },
        },
        "decision": {
            "delta_d_pass": True,
            "runtime_changed_from_checkpoint_c": False,
            "merge_gate": "fresh exact-target independent review required",
        },
        "validation": {"focused_ctest": focused_ctest(args.ctest_log)},
        "raw_evidence": {
            "release": args.raw_release,
            "asset": args.raw_asset,
            "size": args.raw_size,
            "sha256": args.raw_sha256,
        },
    }
    write_json(args.output, value)


if __name__ == "__main__":
    main()

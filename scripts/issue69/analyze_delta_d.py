#!/usr/bin/env python3
"""Build the issue 69 Delta-D physical-SSD technical manifest."""

from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
import statistics

from analyze_checkpoint_a import trace_summary
from common import (cmake_build_identity, decode_tps, file_identity, output_identity,
                    write_json)

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.phase13.analyze_iteration_trace import (  # noqa: E402
    intersection_ns, query, summarize_case, union_ns,
)


BLOCK_STAT = "/sys/class/block/vda/stat"


def load_workload(path: Path) -> dict[str, object]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as source:
            return json.load(source)
    return json.loads(path.read_text())


def transfer_sum(workload: dict[str, object], device_field: str, local_field: str) -> int:
    devices = workload["multi_gpu"]["devices"]
    if devices:
        return sum(int(device.get(device_field, 0)) for device in devices)
    return int(workload["transfer"].get(local_field, 0))


def terminal_state(workload: dict[str, object]) -> dict[str, int]:
    lifecycle = workload["lifecycle"]
    devices = workload["multi_gpu"]["devices"]
    return {
        "active_background_flights": int(lifecycle["active_background_flights"]),
        "cold_transfer_refs": int(lifecycle["cold_current_transfer_refs"]),
        "cold_request_refs": int(lifecycle["cold_current_request_refs"]),
        "cold_cpu_execution_refs": int(lifecycle["cold_current_cpu_execution_refs"]),
        "hot_pins": int(lifecycle["current_hot_pins"]),
        "ring_live_events": sum(int(device["ring_live_events"]) for device in devices),
        "async_active_reads": int(workload["async_io"]["diagnostics"]["active_read_requests"]),
        "scheduler_active_requests": sum(
            int(device["scheduler"]["active_requests"]) for device in devices
        ),
    }


def workload_summary(path: Path) -> dict[str, object]:
    workload = load_workload(path)
    terminal = terminal_state(workload)
    return {
        "file": file_identity(path),
        "decode_tps": decode_tps(workload),
        "generated_identity_sha256": output_identity(workload, include_logits=False),
        "numerical_identity_sha256": output_identity(workload),
        "worker_count": int(workload["async_io"]["diagnostics"]["worker_count"]),
        "storage_read_requests": int(workload["storage"]["read_requests"]),
        "storage_read_bytes": int(workload["storage"]["read_bytes"]),
        "hot_hits": int(workload["mechanism"]["hot_hits"]),
        "hot_misses": int(workload["mechanism"]["hot_misses"]),
        "cold_hits": int(workload["mechanism"]["cold_hits"]),
        "cold_misses": int(workload["mechanism"]["cold_misses"]),
        "cold_admissions": int(workload["mechanism"]["cold_admissions"]),
        "hierarchy_residency": workload["hierarchy_residency"],
        "h2d_bytes": transfer_sum(workload, "ring_h2d_bytes", "h2d_bytes"),
        "h2d_time_us": transfer_sum(workload, "ring_h2d_time_us", "h2d_time_us"),
        "stage_bytes": transfer_sum(workload, "ring_stage_bytes", "stage_bytes"),
        "storage_h2d_overlap": {
            key: int(workload["transfer"][key])
            for key in (
                "disk_h2d_overlap_us", "disk_h2d_overlap_pairs",
                "disk_h2d_overlap_flights", "disk_h2d_overlap_bytes",
                "disk_h2d_overlap_read_bytes",
            )
        },
        "terminal_state": terminal,
        "terminal_state_zero": not any(terminal.values()),
    }


def resource_summary(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    return {
        "file": file_identity(path),
        "elapsed_seconds": float(value["elapsed_seconds"]),
        "cache_state": value["cache_state"],
        "process_io_maxima": value["process_io_maxima"],
        "block_device_delta": value["block_devices"]["delta"][BLOCK_STAT],
    }


def matrix_runs(directory: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for path in sorted((directory / "raw").glob("*.json.gz")):
        if path.name.endswith(".log.gz"):
            continue
        cell = path.name.split("-", 1)[0]
        resource = path.with_name(path.name.removesuffix(".json.gz") + "-resources.json")
        result.setdefault(cell, []).append({
            "workload": workload_summary(path),
            "resources": resource_summary(resource),
        })
    return result


def geometric_mean(values: list[float]) -> float:
    return math.exp(statistics.fmean(math.log(value) for value in values))


def aggregate_cells(runs: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for cell, cell_runs in runs.items():
        workloads = [run["workload"] for run in cell_runs]
        resources = [run["resources"] for run in cell_runs]
        result[cell] = {
            "runs": cell_runs,
            "geometric_mean_decode_tps": geometric_mean(
                [float(run["decode_tps"]) for run in workloads]
            ),
            "mean_process_physical_read_bytes": statistics.fmean(
                int(run["process_io_maxima"]["io_read_bytes"]) for run in resources
            ),
            "mean_block_physical_read_bytes": statistics.fmean(
                int(run["block_device_delta"]["read_bytes"]) for run in resources
            ),
            "exact_generated_identity": len({
                run["generated_identity_sha256"] for run in workloads
            }) == 1,
            "exact_numerical_identity": len({
                run["numerical_identity_sha256"] for run in workloads
            }) == 1,
            "all_terminal_state_zero": all(run["terminal_state_zero"] for run in workloads),
        }
    return result


def worker_screen(root: Path) -> dict[str, object]:
    workers: dict[str, object] = {}
    for directory in sorted(root.glob("workers-*"), key=lambda item: int(item.name.rsplit("-", 1)[1])):
        worker = int(directory.name.rsplit("-", 1)[1])
        cells = aggregate_cells(matrix_runs(directory))
        workers[str(worker)] = {
            "capture": file_identity(directory / "matrix.json"),
            "cells": cells,
            "all_effective_workers_exact": all(
                run["workload"]["worker_count"] == worker
                for cell in cells.values() for run in cell["runs"]
            ),
        }
    four = workers["4"]["cells"]
    two = workers["2"]["cells"]
    eight = workers["8"]["cells"]
    selection = {
        "selected_worker_count": 4,
        "four_vs_two_decode_ratios": {
            cell: four[cell]["geometric_mean_decode_tps"] / two[cell]["geometric_mean_decode_tps"]
            for cell in ("S0", "S1", "A1")
        },
        "eight_vs_four_decode_ratios": {
            cell: eight[cell]["geometric_mean_decode_tps"] / four[cell]["geometric_mean_decode_tps"]
            for cell in ("S0", "S1", "A1")
        },
        "rationale": (
            "four is the smallest screened count improving all topologies over two; "
            "eight is flat for S0 and regresses A1"
        ),
    }
    return {"workers": workers, "selection": selection}


def merged(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for begin, end in sorted(intervals):
        if result and begin <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((begin, end))
    return result


def trace_intervals(
    trace_processor: Path, trace: Path, begin: int, end: int, where: str,
) -> list[tuple[int, int]]:
    rows = query(
        trace_processor, trace,
        f"SELECT ts, dur FROM slice WHERE dur > 0 AND {where} ORDER BY ts",
    )
    return merged([
        (max(begin, int(row["ts"])), min(end, int(row["ts"]) + int(row["dur"])))
        for row in rows
        if int(row["ts"]) < end and int(row["ts"]) + int(row["dur"]) > begin
    ])


def trace_cell(trace_processor: Path, directory: Path, matrix_cell: dict[str, object]) -> dict[str, object]:
    trace = directory / "trace.pftrace"
    detailed = summarize_case(trace_processor, trace)
    begin = int(detailed["window"]["logical_start_ns"])
    end = int(detailed["window"]["logical_end_ns"])
    storage = trace_intervals(
        trace_processor, trace, begin, end,
        "category = 'k3.storage' AND name = 'read_request'",
    )
    h2d = trace_intervals(
        trace_processor, trace, begin, end,
        "category = 'k3.transfer' AND name = 'h2d'",
    )
    gpu0 = trace_intervals(
        trace_processor, trace, begin, end,
        "category = 'k3.cuda' AND name = 'kernel' AND "
        "CAST(EXTRACT_ARG(arg_set_id, 'debug.context_id') AS INT) = 1",
    )
    gpu1 = trace_intervals(
        trace_processor, trace, begin, end,
        "category = 'k3.cuda' AND name = 'kernel' AND "
        "CAST(EXTRACT_ARG(arg_set_id, 'debug.context_id') AS INT) = 2",
    )
    useful = merged(h2d + gpu0 + gpu1)
    duration = end - begin
    provider = detailed["provider_wall"]
    return {
        "trace": file_identity(trace),
        "capture": file_identity(directory / "capture.json"),
        "verification": file_identity(directory / "verification.json"),
        "workload": workload_summary(directory / "workload.json"),
        "block_device_delta": matrix_cell["block_devices"]["delta"][BLOCK_STAT],
        "window_ns": duration,
        "complete_routed_layer_cycles": detailed["complete_routed_layer_cycles"],
        "provider_wall_ms": provider,
        "provider_p50_ms": provider["p50_ms"],
        "provider_p95_ms": provider["p95_ms"],
        "critical_path": detailed["critical_path"],
        "provider_service_unions_not_additive": detailed["provider_service_unions_not_additive"],
        "storage_union_ns": union_ns(storage),
        "h2d_union_ns": union_ns(h2d),
        "storage_h2d_overlap_ns": intersection_ns(storage, h2d),
        "storage_gpu_kernel_overlap_ns": intersection_ns(storage, merged(gpu0 + gpu1)),
        "exposed_storage_without_h2d_or_kernel_ns": (
            union_ns(storage) - intersection_ns(storage, useful)
        ),
        "gpu": {
            "gpu0_useful_kernel_ns": union_ns(gpu0),
            "gpu0_idle_without_kernel_ns": duration - union_ns(gpu0),
            "gpu1_useful_kernel_ns": union_ns(gpu1),
            "gpu1_idle_without_kernel_ns": duration - union_ns(gpu1),
        },
    }


def traces(directory: Path, trace_processor: Path) -> dict[str, object]:
    matrix_path = directory / "trace-matrix.json"
    matrix = json.loads(matrix_path.read_text())
    summarized = trace_summary(directory, trace_processor)
    return {
        "capture": file_identity(matrix_path),
        "cells": {
            cell: trace_cell(trace_processor, directory / cell, matrix["cells"][cell])
            for cell in ("S0", "A1")
        },
        "existing_trace_summary": summarized,
    }


def tmpfs_reference(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    return {
        "manifest": file_identity(path),
        "accepted_parent_head": value["revisions"]["project_head"],
        "accepted_nested_head": value["revisions"]["nested_head"],
        "decode_tps": {
            cell: value["paired_performance"]["cells"][cell]["final_geometric_mean_decode_tps"]
            for cell in ("S0", "S1", "A1")
        },
        "provider_p50_ms": {
            cell: value["traces"]["cells"][cell]["provider_p50_ms"]
            for cell in ("S0", "A1")
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--screen-root", type=Path, required=True)
    parser.add_argument("--confirmation-dir", type=Path, required=True)
    parser.add_argument("--compliance-dir", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--tmpfs-manifest", type=Path, required=True)
    parser.add_argument("--model-verification", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--raw-release", required=True)
    parser.add_argument("--raw-asset", required=True)
    parser.add_argument("--raw-size", type=int, required=True)
    parser.add_argument("--raw-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    screen = worker_screen(args.screen_root)
    confirmation = aggregate_cells(matrix_runs(args.confirmation_dir))
    compliance = aggregate_cells(matrix_runs(args.compliance_dir))
    trace_result = traces(args.trace_dir, args.trace_processor)
    tmpfs = tmpfs_reference(args.tmpfs_manifest)
    physical_vs_tmpfs = {
        cell: confirmation[cell]["geometric_mean_decode_tps"] / tmpfs["decode_tps"][cell]
        for cell in ("S0", "S1", "A1")
    }
    identities = {
        run["workload"]["generated_identity_sha256"]
        for collection in (confirmation, compliance)
        for cell in collection.values()
        for run in cell["runs"]
    } | {
        cell["workload"]["generated_identity_sha256"]
        for cell in trace_result["cells"].values()
    }
    errors: list[str] = []
    if set(screen["workers"]) != {"1", "2", "4", "8"}:
        errors.append("worker screen is incomplete")
    if not all(entry["all_effective_workers_exact"] for entry in screen["workers"].values()):
        errors.append("effective worker count differs from requested worker count")
    if any(len(confirmation[cell]["runs"]) != 3 for cell in ("S0", "S1", "A1")):
        errors.append("three-run confirmation matrix is incomplete")
    if any(not confirmation[cell]["all_terminal_state_zero"] for cell in confirmation):
        errors.append("confirmation terminal state is nonzero")
    if any(not compliance[cell]["all_terminal_state_zero"] for cell in compliance):
        errors.append("compliance terminal state is nonzero")
    if len(identities) != 1:
        errors.append("physical-SSD generated output identity differs across captures")
    if any(trace_result["cells"][cell]["workload"]["stage_bytes"] != 0 for cell in ("S0", "A1")):
        errors.append("trace capture reintroduced transfer staging")

    result = {
        "schema_version": "issue69-delta-d-physical-ssd-v1",
        "status": "pass" if not errors else "fail",
        "checkpoint": "D1_UNCHANGED_RUNTIME_PHYSICAL_SSD",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "build": cmake_build_identity(args.probe),
        "fixture": {
            "model_revision": "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1",
            "variant": "UD-Q2_K_XL",
            "accepted_model_verification": file_identity(args.model_verification),
            "backing_store": {
                "model_first_shard": str(
                    Path("/root/models/deepseek-v4-flash-ud-q2-k-xl/UD-Q2_K_XL/") /
                    "DeepSeek-V4-Flash-UD-Q2_K_XL-00001-of-00003.gguf"
                ),
                "mount_target": "/", "block_device": "/dev/vda1", "filesystem": "ext4",
                "cache_rule": "sync then Linux drop_caches=3 before every authoritative process",
                "transport": "POSITIONAL pread",
            },
            "graphs": "disabled", "prefetch": "off", "cold_bytes": 16 * 1024**3,
            "generated_tokens": 24,
        },
        "tmpfs_reference": tmpfs,
        "worker_screen": screen,
        "confirmation": confirmation,
        "physical_vs_tmpfs_decode_ratio": physical_vs_tmpfs,
        "a1_vs_s1_physical_decode_ratio": (
            confirmation["A1"]["geometric_mean_decode_tps"] /
            confirmation["S1"]["geometric_mean_decode_tps"]
        ),
        "compliance": compliance,
        "traces": trace_result,
        "identity": {
            "exact_generated_identity_across_physical_captures": len(identities) == 1,
            "generated_identity_sha256": next(iter(identities)) if len(identities) == 1 else None,
        },
        "d1_conclusion": {
            "selected_worker_count": 4,
            "physical_storage_is_material": all(
                trace_result["cells"][cell]["exposed_storage_without_h2d_or_kernel_ns"] >
                0.25 * trace_result["cells"][cell]["window_ns"]
                for cell in ("S0", "A1")
            ),
            "a1_logical_byte_advantage_translates_to_throughput_advantage": (
                confirmation["A1"]["geometric_mean_decode_tps"] >
                confirmation["S1"]["geometric_mean_decode_tps"]
            ),
            "next": "publish D1 before entering the D2 cold-cache usefulness decision",
        },
        "raw_evidence": {
            "release": args.raw_release, "asset": args.raw_asset,
            "size": args.raw_size, "sha256": args.raw_sha256,
        },
        "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "physical_vs_tmpfs_decode_ratio": physical_vs_tmpfs,
        "a1_vs_s1_physical_decode_ratio": result["a1_vs_s1_physical_decode_ratio"],
        "errors": errors,
    }, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

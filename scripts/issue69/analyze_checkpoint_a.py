#!/usr/bin/env python3
"""Build the issue 69 Checkpoint-A premise-verification manifest."""

from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys

from common import ROOT, cmake_build_identity, decode_tps, file_identity, output_identity, write_json

sys.path.insert(0, str(ROOT))
from scripts.phase13.analyze_iteration_trace import query, summarize_case


def load_gzip_json(path: Path) -> dict[str, object]:
    with gzip.open(path, "rt") as source:
        return json.load(source)


def matrix_summary(directory: Path) -> dict[str, object]:
    cells: dict[str, list[dict[str, object]]] = {}
    for path in sorted((directory / "raw").glob("*.json.gz")):
        if path.name.endswith(".log.gz"):
            continue
        cell = path.stem.split("-")[0]
        workload = load_gzip_json(path)
        cells.setdefault(cell, []).append({
            "path": str(path), "decode_tps": decode_tps(workload),
            "generated_identity_sha256": output_identity(workload, include_logits=False),
            "numerical_identity_sha256": output_identity(workload),
            "worker_count": workload["async_io"]["diagnostics"]["worker_count"],
            "storage_read_requests": workload["storage"]["read_requests"],
            "storage_read_bytes": workload["storage"]["read_bytes"],
            "hierarchy_residency": workload["hierarchy_residency"],
            "transfer_stage_bytes": workload["transfer"]["stage_bytes"],
            "transfer_stage_time_us": workload["transfer"]["stage_time_us"],
        })
    result: dict[str, object] = {}
    for cell, runs in cells.items():
        generated_identities = {run["generated_identity_sha256"] for run in runs}
        numerical_identities = {run["numerical_identity_sha256"] for run in runs}
        workers = {run["worker_count"] for run in runs}
        result[cell] = {
            "runs": runs, "geometric_mean_decode_tps": math.exp(statistics.fmean(
                math.log(float(run["decode_tps"])) for run in runs)),
            "generated_identity_sha256": next(iter(generated_identities))
                if len(generated_identities) == 1 else None,
            "numerical_identity_sha256": next(iter(numerical_identities))
                if len(numerical_identities) == 1 else None,
            "exact_generated_identity_within_cell": len(generated_identities) == 1,
            "exact_numerical_identity_within_cell": len(numerical_identities) == 1,
            "worker_count": next(iter(workers)) if len(workers) == 1 else None,
        }
    matrix = directory / "matrix.json"
    return {"capture": file_identity(matrix), "configuration": json.loads(matrix.read_text()),
            "cells": result}


def profile_summary(directory: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for path in sorted(directory.glob("*/summary.json")):
        value = json.loads(path.read_text())
        result[value["cell"]] = {
            "summary": file_identity(path),
            "build": value["build"], "selection": value["selection"], "perf_stat": value["perf_stat"],
            "process": value["artifacts"]["process"],
            "main": value["artifacts"]["main"],
            "storage": value["artifacts"]["storage"],
        }
    differential = directory / "s0-to-a1-differential.svg"
    if differential.is_file():
        result["S0_TO_A1_DIFFERENTIAL"] = file_identity(differential)
    return result


def phase_barrier(trace_processor: Path, trace: Path) -> dict[str, object]:
    rows = query(trace_processor, trace, """
        WITH providers AS (
          SELECT id, ts, ts + dur AS end_ts,
            CAST(EXTRACT_ARG(arg_set_id, 'debug.layer') AS INT) AS layer
          FROM slice WHERE category = 'k3.provider' AND name = 'acquire_and_remap' AND dur > 0
        ), reads AS (
          SELECT ts, ts + dur AS end_ts FROM slice
          WHERE category = 'k3.storage' AND name = 'read_request' AND dur > 0
        ), h2d AS (
          SELECT ts, ts + dur AS end_ts FROM slice
          WHERE category = 'k3.transfer' AND name = 'h2d' AND dur > 0
        )
        SELECT p.id, p.layer, p.ts, p.end_ts,
          (SELECT COUNT(*) FROM reads r WHERE r.ts >= p.ts AND r.end_ts <= p.end_ts) AS reads,
          (SELECT MIN(r.end_ts) FROM reads r WHERE r.ts >= p.ts AND r.end_ts <= p.end_ts) AS first_read_end,
          (SELECT MAX(r.end_ts) FROM reads r WHERE r.ts >= p.ts AND r.end_ts <= p.end_ts) AS last_read_end,
          (SELECT MIN(h.ts) FROM h2d h WHERE h.ts >= (
              SELECT MIN(r.end_ts) FROM reads r WHERE r.ts >= p.ts AND r.end_ts <= p.end_ts)
            AND h.ts <= p.end_ts) AS first_h2d_after_read
        FROM providers p ORDER BY p.ts
    """)
    rich: list[dict[str, object]] = []
    for row in rows:
        reads = int(row["reads"])
        if reads < 2 or row["first_read_end"] == "[NULL]" or row["last_read_end"] == "[NULL]":
            continue
        first_h2d = None if row["first_h2d_after_read"] == "[NULL]" else int(row["first_h2d_after_read"])
        last_read = int(row["last_read_end"])
        rich.append({
            "layer": int(row["layer"]), "read_requests": reads,
            "first_read_complete_ns": int(row["first_read_end"]),
            "last_read_complete_ns": last_read, "first_h2d_after_read_ns": first_h2d,
            "all_reads_complete_before_next_h2d": first_h2d is not None and first_h2d >= last_read,
        })
    return {
        "miss_rich_layers": len(rich),
        "barrier_layers": sum(bool(row["all_reads_complete_before_next_h2d"]) for row in rich),
        "examples": rich[:12],
    }


def slice_durations(trace_processor: Path, trace: Path, category: str, name: str) -> dict[str, object]:
    rows = query(trace_processor, trace, f"""
        SELECT dur FROM slice
        WHERE category = '{category}' AND name = '{name}' AND dur > 0
        ORDER BY dur
    """)
    durations = [int(row["dur"]) for row in rows]
    if not durations:
        return {"count": 0, "sum_ms": 0.0, "p50_ms": None, "p95_ms": None}
    p95_index = min(len(durations) - 1, math.ceil(len(durations)*0.95) - 1)
    return {
        "count": len(durations), "sum_ms": sum(durations)/1e6,
        "p50_ms": statistics.median(durations)/1e6,
        "p95_ms": durations[p95_index]/1e6,
    }


def trace_summary(directory: Path, trace_processor: Path) -> dict[str, object]:
    matrix_path = directory / "trace-matrix.json"
    matrix = json.loads(matrix_path.read_text())
    cells: dict[str, object] = {}
    for cell_dir in sorted(path for path in directory.iterdir() if path.is_dir()):
        trace = cell_dir / "trace.pftrace"
        if not trace.is_file():
            continue
        detailed = summarize_case(trace_processor, trace)
        provider_values = [float(cycle["provider_ns"]) / 1e6 for cycle in detailed["cycles"]]
        cells[cell_dir.name] = {
            "trace": file_identity(trace),
            "complete_routed_layer_cycles": detailed["complete_routed_layer_cycles"],
            "provider_wall_ms": detailed["provider_wall"],
            "provider_p50_ms": statistics.median(provider_values),
            "critical_path": detailed["critical_path"],
            "slice_durations": {
                "storage_read": slice_durations(trace_processor, trace, "k3.storage", "read_request"),
                "transfer_stage": slice_durations(trace_processor, trace, "k3.transfer", "stage"),
                "h2d": slice_durations(trace_processor, trace, "k3.transfer", "h2d"),
            },
            "phase_barrier": phase_barrier(trace_processor, trace),
        }
    return {"capture": file_identity(matrix_path), "build": matrix["build"], "cells": cells}


def zero_control(directory: Path, probe_sha256: str) -> dict[str, object]:
    workload_path = next((directory / "raw").glob("S0-*.json.gz"))
    workload = load_gzip_json(workload_path)
    resource_path = next((directory / "raw").glob("S0-*-resources.json"))
    resources = json.loads(resource_path.read_text())
    available = [sample["host"]["MemAvailable"] for sample in resources["samples"]]
    swap_total = [sample["host"]["SwapTotal"] for sample in resources["samples"]]
    prewarm = workload["cold_prewarm"]
    return {
        "workload": file_identity(workload_path), "resources": file_identity(resource_path),
        "probe_sha256": probe_sha256,
        "cold_requested_bytes": workload["capacities"]["cold_requested_bytes"],
        "cold_effective_slots": workload["capacities"]["cold_effective_slots"],
        "directory_entry_count": workload["storage"]["directory_entry_count"],
        "prewarm": prewarm, "hierarchy_residency": workload["hierarchy_residency"],
        "minimum_mem_available_kib": min(available), "swap_total_kib": max(swap_total),
        "gate": prewarm["completed"] and prewarm["measured_read_requests"] == 0 and
            prewarm["measured_read_bytes"] == 0 and max(swap_total) == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix-dir", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--zero-control-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--zero-probe-sha256", required=True)
    parser.add_argument("--model-verification", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    matrix = matrix_summary(args.matrix_dir)
    profiles = profile_summary(args.profile_dir)
    traces = trace_summary(args.trace_dir, args.trace_processor)
    zero = zero_control(args.zero_control_dir, args.zero_probe_sha256)
    model_verification = json.loads(args.model_verification.read_text())
    required = {"S0", "S1", "A1"}
    matrix_cells = matrix["cells"]
    trace_cells = traces["cells"]
    generated_identities = {matrix_cells[cell]["generated_identity_sha256"] for cell in required}
    numerical_identities = {matrix_cells[cell]["numerical_identity_sha256"] for cell in required}
    workers = {cell: matrix_cells[cell]["worker_count"] for cell in required}
    hierarchy_runs = [run for cell in required for run in matrix_cells[cell]["runs"]]
    strict_inclusion = all(
        run["hierarchy_residency"]["hot_without_cold_keys"] == 0 and
        run["hierarchy_residency"]["duplicate_keys"] == run["hierarchy_residency"]["hot_keys"]
        for run in hierarchy_runs
    )
    transfer_fractions = {
        cell: profiles[cell]["main"]["attribution"]["buckets"][
            "transfer_ring_stage_memcpy"]["fraction"]
        for cell in required
    }
    maximum_transfer_fraction = max(transfer_fractions.values())
    barrier_cells = [cell for cell in ("S1", "A1")
                     if trace_cells[cell]["phase_barrier"]["barrier_layers"] > 0]
    premises = {
        "storage_worker_topology_coupling": {
            "status": "OBSERVED" if workers == {"S0": 1, "S1": 2, "A1": 2} else "BLOCKED",
            "premise_confirmed": workers == {"S0": 1, "S1": 2, "A1": 2},
            "effective_workers": workers,
        },
        "strict_inclusion_reduces_distinct_capacity": {
            "status": "OBSERVED" if strict_inclusion else "BLOCKED",
            "premise_confirmed": strict_inclusion,
            "all_hot_keys_duplicate_cold_keys": strict_inclusion,
        },
        "cold_to_pinned_stage_has_material_cpu_cost": {
            "status": "OBSERVED" if maximum_transfer_fraction > 0.01 else "BLOCKED",
            "premise_confirmed": maximum_transfer_fraction > 0.01,
            "main_thread_on_cpu_fraction_by_cell": transfer_fractions,
            "maximum_main_thread_on_cpu_fraction": maximum_transfer_fraction,
            "s0_stage_time_us": matrix_cells["S0"]["runs"][0]["transfer_stage_time_us"],
        },
        "multi_device_completion_phase_barrier": {
            "status": "OBSERVED" if barrier_cells else "BLOCKED",
            "premise_confirmed": bool(barrier_cells),
            "cells_with_barrier_layers": barrier_cells,
        },
        "cache_policy_optimization": {"status": "OPEN", "reason": "deferred until post-structural profile"},
    }
    errors: list[str] = []
    if len(generated_identities) != 1:
        errors.append("S0/S1/A1 generated output identities differ")
    if not zero["gate"]:
        errors.append("zero-storage full-cold control failed")
    if not required.issubset(profiles):
        errors.append("profile matrix is incomplete")
    if set(trace_cells) != required:
        errors.append("trace matrix is incomplete")
    result = {
        "schema_version": "issue69-checkpoint-a-manifest-v1",
        "status": "pass" if not errors else "fail", "checkpoint": "A_ATTRIBUTION_BASELINE",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "build": cmake_build_identity(args.probe),
        "fixture": {"model_revision": "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1",
            "variant": "UD-Q2_K_XL", "graphs": "disabled", "transport": "POSITIONAL",
            "verification": file_identity(args.model_verification),
            "verification_result": model_verification},
        "identity": {
            "exact_generated_identity_across_mode_p_cells": len(generated_identities) == 1,
            "exact_numerical_identity_across_mode_p_cells": len(numerical_identities) == 1,
            "note": "Mode-P generated identity is the acceptance identity; topology-specific logits digests are expected per accepted issue #65 evidence.",
        },
        "matrix": matrix, "profiles": profiles, "traces": traces,
        "zero_storage_control": zero, "premises": premises, "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({"status": result["status"], "premises": premises, "errors": errors}, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

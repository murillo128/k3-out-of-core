#!/usr/bin/env python3
"""Build the issue 69 Delta-D2c constrained-memory final manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import statistics

from analyze_delta_d import (aggregate_cells, file_identity, matrix_runs, traces)
from analyze_delta_d_final import focused_ctest, one_run, profiles
from common import write_json


CELLS = ("S0", "S1", "A1")


def capture(directory: Path) -> dict[str, object]:
    return {
        "matrix": file_identity(directory / "matrix.json"),
        "cells": aggregate_cells(matrix_runs(directory)),
    }


def compare(reference: dict[str, object], candidate: dict[str, object]) -> dict[str, object]:
    cells: dict[str, object] = {}
    for cell in sorted(set(reference["cells"]) & set(candidate["cells"])):
        base = reference["cells"][cell]
        trial = candidate["cells"][cell]
        cells[cell] = {
            "decode_ratio": (
                trial["geometric_mean_decode_tps"] / base["geometric_mean_decode_tps"]
            ),
            "process_physical_read_reduction_fraction": 1 - (
                trial["mean_process_physical_read_bytes"] /
                base["mean_process_physical_read_bytes"]
            ),
            "block_physical_read_reduction_fraction": 1 - (
                trial["mean_block_physical_read_bytes"] /
                base["mean_block_physical_read_bytes"]
            ),
            "logical_read_request_reduction_fraction": 1 - (
                statistics.fmean(
                    run["workload"]["storage_read_requests"] for run in trial["runs"]
                ) / statistics.fmean(
                    run["workload"]["storage_read_requests"] for run in base["runs"]
                )
            ),
            "logical_read_byte_reduction_fraction": 1 - (
                statistics.fmean(
                    run["workload"]["storage_read_bytes"] for run in trial["runs"]
                ) / statistics.fmean(
                    run["workload"]["storage_read_bytes"] for run in base["runs"]
                )
            ),
            "mean_candidate_cold_hits": statistics.fmean(
                run["workload"]["cold_hits"] for run in trial["runs"]
            ),
            "mean_candidate_cold_admissions": statistics.fmean(
                run["workload"]["cold_admissions"] for run in trial["runs"]
            ),
            "exact_generated_identity": (
                base["exact_generated_identity"] and trial["exact_generated_identity"] and
                {run["workload"]["generated_identity_sha256"] for run in base["runs"]} ==
                {run["workload"]["generated_identity_sha256"] for run in trial["runs"]}
            ),
            "exact_numerical_identity": (
                base["exact_numerical_identity"] and trial["exact_numerical_identity"] and
                {run["workload"]["numerical_identity_sha256"] for run in base["runs"]} ==
                {run["workload"]["numerical_identity_sha256"] for run in trial["runs"]}
            ),
        }
    return {"cells": cells}


def memory_point(root: Path) -> dict[str, object]:
    normal = capture(root / "buffered-normal")
    random = capture(root / "buffered-random")
    fill = capture(root / "buffered-normal-fill")
    return {
        "buffered_normal": normal,
        "buffered_fadv_random": random,
        "buffered_normal_async_fill_16gib": fill,
        "fadv_random_vs_normal": compare(normal, random),
        "async_fill_vs_normal": compare(normal, fill),
    }


def direct_screens(root: Path) -> dict[str, object]:
    controls = {
        "workers_2": one_run(root / "direct-w2-v3", "S0"),
        "workers_4": one_run(root / "direct-w4", "S0"),
        "workers_8": one_run(root / "direct-w8", "S0"),
        "workers_8_a1": one_run(root / "direct-w8-a1", "A1"),
    }
    fill16 = {
        "S0": one_run(root / "direct-w8-fill", "S0"),
        "A1": one_run(root / "direct-w8-fill-a1", "A1"),
    }
    fill32 = one_run(root / "direct-w8-fill32-s0", "S0")
    comparisons: dict[str, object] = {}
    for cell, base_key in (("S0", "workers_8"), ("A1", "workers_8_a1")):
        base = controls[base_key]
        candidate = fill16[cell]
        comparisons[cell] = {
            "decode_ratio": candidate["decode_tps"] / base["decode_tps"],
            "process_physical_read_reduction_fraction": 1 - (
                candidate["resources"]["process_io_maxima"]["io_read_bytes"] /
                base["resources"]["process_io_maxima"]["io_read_bytes"]
            ),
            "block_physical_read_reduction_fraction": 1 - (
                candidate["resources"]["block_device_delta"]["read_bytes"] /
                base["resources"]["block_device_delta"]["read_bytes"]
            ),
            "logical_read_request_reduction_fraction": 1 - (
                candidate["storage_read_requests"] / base["storage_read_requests"]
            ),
            "exact_generated_identity": (
                candidate["generated_identity_sha256"] == base["generated_identity_sha256"]
            ),
            "exact_numerical_identity": (
                candidate["numerical_identity_sha256"] == base["numerical_identity_sha256"]
            ),
        }
    return {
        "worker_screen": controls,
        "async_fill_16gib": fill16,
        "async_fill_16gib_vs_direct": {"cells": comparisons},
        "async_fill_32gib_s0": fill32,
        "capacity_curve": {
            "fill32_vs_fill16_s0_decode_ratio": (
                fill32["decode_tps"] / fill16["S0"]["decode_tps"]
            ),
            "fill32_vs_fill16_s0_block_read_ratio": (
                fill32["resources"]["block_device_delta"]["read_bytes"] /
                fill16["S0"]["resources"]["block_device_delta"]["read_bytes"]
            ),
            "fill32_vs_fill16_s0_cold_hit_ratio": (
                fill32["cold_hits"] / fill16["S0"]["cold_hits"]
            ),
            "stop_reason": (
                "32 GiB produced no physical-read/reuse capacity curve and regressed decode; "
                "64 GiB was therefore not tested"
            ),
        },
    }


def all_runs(*captures: dict[str, object]) -> list[dict[str, object]]:
    return [
        run
        for item in captures
        for cell in item["cells"].values()
        for run in cell["runs"]
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--abundant-manifest", type=Path, required=True)
    parser.add_argument("--screen-64g", type=Path, required=True)
    parser.add_argument("--screen-96g", type=Path, required=True)
    parser.add_argument("--direct-screen-96g", type=Path, required=True)
    parser.add_argument("--read-ahead-screen-64g", type=Path, required=True)
    parser.add_argument("--confirmation-64g", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--profile-raw-dir", type=Path, required=True)
    parser.add_argument("--profile-dir", type=Path, required=True)
    parser.add_argument("--scheduler-summary", type=Path, required=True)
    parser.add_argument("--ctest-log", type=Path, required=True)
    parser.add_argument("--candidate-ctest-log", type=Path, required=True)
    parser.add_argument("--experimental-source-patch", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--raw-release", required=True)
    parser.add_argument("--raw-asset", required=True)
    parser.add_argument("--raw-size", type=int, required=True)
    parser.add_argument("--raw-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    memory64 = memory_point(args.screen_64g)
    memory96 = memory_point(args.screen_96g)
    read_ahead_path = args.read_ahead_screen_64g / "read-ahead-screen.json"
    read_ahead = json.loads(read_ahead_path.read_text())
    confirmation = {
        policy: capture(args.confirmation_64g / policy)
        for policy in ("buffered", "direct", "direct-fill")
    }
    buffered_vs_direct_fill = compare(
        confirmation["buffered"], confirmation["direct-fill"]
    )
    direct_vs_direct_fill = compare(
        confirmation["direct"], confirmation["direct-fill"]
    )
    confirmation_captures = list(confirmation.values())
    identity_runs = all_runs(*confirmation_captures)
    generated = {run["workload"]["generated_identity_sha256"] for run in identity_runs}
    numerical_by_cell = {
        cell: {
            run["workload"]["numerical_identity_sha256"]
            for item in confirmation_captures
            for run in item["cells"].get(cell, {}).get("runs", [])
        }
        for cell in CELLS
    }
    bounded = all(
        cell["whole_cgroup"]["bounded_without_swap_or_oom"]
        for item in confirmation_captures for cell in item["cells"].values()
    )
    complete = json.loads((args.confirmation_64g / "confirmation.json").read_text())
    errors: list[str] = []
    if complete["status"] != "complete" or len(complete["records"]) != 24:
        errors.append("64 GiB interleaved confirmation is incomplete")
    if any(
        len(item["cells"][cell]["runs"]) != 3
        for item in confirmation_captures for cell in item["cells"]
    ):
        errors.append("a confirmation policy/cell does not contain three runs")
    if not bounded:
        errors.append("confirmation exceeded cgroup/swap/OOM constraints")
    if len(generated) != 1 or any(len(values) != 1 for values in numerical_by_cell.values()):
        errors.append("confirmation output identity differs")
    if any(
        not cell["all_terminal_state_zero"]
        for item in confirmation_captures for cell in item["cells"].values()
    ):
        errors.append("confirmation terminal resource state is nonzero")

    trace_result = traces(args.trace_dir, args.trace_processor)
    for cell in ("S0", "A1"):
        critical = trace_result["cells"][cell]["critical_path"]
        trace_result["cells"][cell]["remaining_unexplained_gap_ns"] = \
            critical["buckets_ns"]["dependency_or_host_gap"]

    result = {
        "schema_version": "issue69-delta-d2c-final-v1",
        "status": "pass" if not errors else "fail",
        "checkpoint": "DELTA_D_K3_CONSTRAINED_MEMORY_PHYSICAL_SSD_FINAL",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "historical_abundant_ram_result": file_identity(args.abundant_manifest),
        "memory_points": {"64_gib": memory64, "96_gib": memory96},
        "host_read_ahead_control_64_gib": {
            "record": file_identity(read_ahead_path),
            "result": read_ahead,
            "selected": False,
            "reason": (
                "the single bounded S0 screen exceeded 12x the normal whole-run elapsed time "
                "without completing, so it was not promoted to S1/A1"
            ),
        },
        "direct_io_control": direct_screens(args.direct_screen_96g),
        "confirmation_64_gib": {
            "capture": file_identity(args.confirmation_64g / "confirmation.json"),
            "policies": confirmation,
            "direct_fill_vs_direct": direct_vs_direct_fill,
            "direct_fill_vs_buffered_e2e": buffered_vs_direct_fill,
            "whole_cgroup_bounded_without_swap_or_oom": bounded,
        },
        "selection": {
            "runtime": "UNCHANGED_BUFFERED_POSITIONAL_DIRECT_PROMOTION",
            "transport": "POSITIONAL",
            "io_access": "NORMAL",
            "worker_count": 4,
            "cold_admissions": 0,
            "runtime_changed_from_checkpoint_c": False,
            "async_cold_fill_selected": False,
            "reason": (
                "the O_DIRECT control proved physical reuse, but async fill did not beat the "
                "best buffered end-to-end path across S0/S1/A1 at the same 64 GiB total cap"
            ),
        },
        "rejected_experimental_source": {
            "patch": file_identity(args.experimental_source_patch),
            "applies_to_nested_head": args.nested_head,
            "candidate_ctest": {
                "log": file_identity(args.candidate_ctest_log),
                "tests_failed": 2,
                "tests_total": 12,
                "failed_tests": ["test-expert-miss-policy", "test-hot-expert-cache"],
                "observed_summary": re.search(
                    r"\d+% tests passed, \d+ tests failed out of \d+",
                    args.candidate_ctest_log.read_text(),
                ).group(0),
                "disposition": (
                    "rejected candidate was not hardened after failing the E2E selection gate; "
                    "all experimental runtime code was removed"
                ),
            },
        },
        "d3": {
            "traces": trace_result,
            "profiles": profiles(args.profile_raw_dir, args.profile_dir),
            "scheduler": {
                "summary": file_identity(args.scheduler_summary),
                "evidence": json.loads(args.scheduler_summary.read_text()),
            },
            "dominant_critical_path": {
                "S0": (
                    "exposed buffered ext4/page-cache storage service; scheduler wake/run "
                    "latency is not material"
                ),
                "A1": (
                    "exposed buffered ext4/page-cache storage service plus the remaining "
                    "dependency/host gap; scheduler wake/run latency is not material"
                ),
                "basis": (
                    "non-additive Perfetto service unions, CUPTI useful/idle intervals, "
                    "scheduler thread-state attribution, and decode-relevant perf samples"
                ),
            },
        },
        "identity": {
            "exact_generated_identity": len(generated) == 1,
            "exact_numerical_identity_within_each_topology": all(
                len(values) == 1 for values in numerical_by_cell.values()
            ),
            "generated_identity_sha256": next(iter(generated)) if len(generated) == 1 else None,
            "numerical_identity_sha256_by_topology": {
                cell: next(iter(values)) if len(values) == 1 else None
                for cell, values in numerical_by_cell.items()
            },
        },
        "validation": {"focused_ctest": focused_ctest(args.ctest_log)},
        "raw_evidence": {
            "release": args.raw_release, "asset": args.raw_asset,
            "size": args.raw_size, "sha256": args.raw_sha256,
        },
        "exit": {
            "delta_d_pass": not errors,
            "merge_gate": "fresh exact-target independent review required",
            "issue44_handoff": (
                "retain fit-in-RAM vs K3 out-of-core distinction and run native io_uring E2E "
                "comparison on the next capable discrete-CUDA host"
            ),
        },
        "errors": errors,
    }
    write_json(args.output, result)
    print(json.dumps({
        "status": result["status"],
        "buffered_vs_direct_fill": buffered_vs_direct_fill,
        "direct_vs_direct_fill": direct_vs_direct_fill,
        "errors": errors,
    }, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

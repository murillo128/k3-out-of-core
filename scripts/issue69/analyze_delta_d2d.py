#!/usr/bin/env python3
"""Build the issue 69 Delta-D2d hardening/configurability manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from analyze_delta_d_final import focused_ctest, one_run
from common import file_identity, write_json


def bounded(run: dict[str, object]) -> bool:
    cgroup = run["resources"].get("cgroup") or {}
    after = cgroup.get("after") or {}
    events = after.get("memory_events") or {}
    return (
        int(after.get("memory_max", 0)) == 64 * 1024**3
        and int(after.get("memory_swap_current", -1)) == 0
        and int(after.get("memory_swap_max", -1)) == 0
        and int(events.get("oom", -1)) == 0
        and int(events.get("oom_kill", -1)) == 0
        and int(events.get("oom_group_kill", -1)) == 0
    )


def compare(direct: dict[str, object], fill: dict[str, object]) -> dict[str, object]:
    direct_block = int(direct["resources"]["block_device_delta"]["read_bytes"])
    fill_block = int(fill["resources"]["block_device_delta"]["read_bytes"])
    direct_process = int(direct["resources"]["process_io_maxima"]["io_read_bytes"])
    fill_process = int(fill["resources"]["process_io_maxima"]["io_read_bytes"])
    return {
        "direct": direct,
        "async_cold_fill": fill,
        "async_fill_vs_direct_decode_ratio": fill["decode_tps"] / direct["decode_tps"],
        "logical_read_request_reduction_fraction": 1 - (
            fill["storage_read_requests"] / direct["storage_read_requests"]
        ),
        "logical_read_byte_reduction_fraction": 1 - (
            fill["storage_read_bytes"] / direct["storage_read_bytes"]
        ),
        "block_read_reduction_fraction": 1 - fill_block / direct_block,
        "process_physical_read_reduction_fraction": 1 - fill_process / direct_process,
        "exact_generated_identity": (
            fill["generated_identity_sha256"] == direct["generated_identity_sha256"]
        ),
        "exact_numerical_identity": (
            fill["numerical_identity_sha256"] == direct["numerical_identity_sha256"]
        ),
        "both_terminal_state_zero": (
            direct["terminal_state_zero"] and fill["terminal_state_zero"]
        ),
        "both_whole_cgroup_bounded_without_swap_or_oom": bounded(direct) and bounded(fill),
    }


def targeted_ctest(path: Path) -> dict[str, object]:
    text = path.read_text()
    match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", text)
    if match is None:
        raise ValueError(f"targeted CTest summary is missing from {path}")
    percent, failed, total = (int(value) for value in match.groups())
    if percent != 100 or failed != 0 or total != 2:
        raise ValueError(f"targeted lifetime CTest did not pass 2/2: {path}")
    return {
        "log": file_identity(path),
        "tests_passed": total,
        "tests_failed": failed,
        "tests_total": total,
        "glibc_heap_check": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--buffered-normal", type=Path, required=True)
    parser.add_argument("--buffered-random", type=Path, required=True)
    parser.add_argument("--direct-s0", type=Path, required=True)
    parser.add_argument("--direct-fill-s0", type=Path, required=True)
    parser.add_argument("--direct-a1", type=Path, required=True)
    parser.add_argument("--direct-fill-a1", type=Path, required=True)
    parser.add_argument("--targeted-ctest-log", type=Path, required=True)
    parser.add_argument("--focused-ctest-log", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--raw-release", required=True)
    parser.add_argument("--raw-asset", required=True)
    parser.add_argument("--raw-size", type=int, required=True)
    parser.add_argument("--raw-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    normal = one_run(args.buffered_normal, "S0")
    random = one_run(args.buffered_random, "S0")
    comparisons = {
        "S0": compare(one_run(args.direct_s0, "S0"), one_run(args.direct_fill_s0, "S0")),
        "A1": compare(one_run(args.direct_a1, "A1"), one_run(args.direct_fill_a1, "A1")),
    }
    errors: list[str] = []
    if normal["io_access_effective"] != "NORMAL" or normal["worker_count"] != 4:
        errors.append("normal buffered positional configuration was not effective")
    if random["io_access_effective"] != "RANDOM" or random["worker_count"] != 4:
        errors.append("POSIX_FADV_RANDOM buffered configuration was not effective")
    if normal["async_cold_fill"]["async_cold_fill_configured"] != 0:
        errors.append("async cold fill is not default off")
    for cell, comparison in comparisons.items():
        direct = comparison["direct"]
        fill = comparison["async_cold_fill"]
        if direct["direct_io"]["direct_read_operations"] == 0:
            errors.append(f"{cell} O_DIRECT control did not perform direct reads")
        if direct["direct_io"]["buffered_fallback_operations"] != 0:
            errors.append(f"{cell} O_DIRECT control silently fell back")
        mechanism = fill["async_cold_fill"]
        if (
            mechanism["async_cold_fill_configured"] != 1
            or mechanism["async_cold_fill_completed"] == 0
            or mechanism["async_cold_fill_failed"] != 0
            or mechanism["async_cold_fill_active"] != 0
            or fill["cold_hits"] == 0
        ):
            errors.append(f"{cell} async cold fill mechanism did not complete cleanly")
        if comparison["block_read_reduction_fraction"] <= 0:
            errors.append(f"{cell} async cold fill did not reduce physical block reads")
        if not comparison["exact_generated_identity"] or not comparison["exact_numerical_identity"]:
            errors.append(f"{cell} output identity differs")
        if not comparison["both_terminal_state_zero"]:
            errors.append(f"{cell} terminal resource state is nonzero")
        if not comparison["both_whole_cgroup_bounded_without_swap_or_oom"]:
            errors.append(f"{cell} whole-cgroup bound/swap/OOM gate failed")

    result = {
        "schema_version": "issue69-delta-d2d-final-v1",
        "status": "pass" if not errors else "fail",
        "checkpoint": "DELTA_D2D_EXPERIMENTAL_STORAGE_CACHE_HARDENING",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head},
        "archived_failure_classification": {
            "result": "STALE_INCREMENTAL_TEST_EXECUTABLES",
            "finding": (
                "the archived crashes disappeared after rebuilding the two affected executables "
                "against the candidate-modified shared library and public llama_model_params layout"
            ),
            "source_runtime_defect_reproduced": False,
            "targeted_lifetime_check": targeted_ctest(args.targeted_ctest_log),
        },
        "focused_native_suite": focused_ctest(args.focused_ctest_log),
        "configuration_smoke": {
            "buffered_positional_normal": normal,
            "buffered_positional_fadv_random": random,
        },
        "direct_io_async_fill_regression_64_gib": {"cells": comparisons},
        "selection": {
            "default_changed_from_d2c": False,
            "runtime": "BUFFERED_POSITIONAL_DIRECT_PROMOTION",
            "worker_count_for_decision_fixture": 4,
            "io_access": "NORMAL",
            "async_cold_fill": False,
            "experimental_modes_retained_default_off": [
                "POSIX_FADV_RANDOM", "POSITIONAL_O_DIRECT", "ASYNC_COLD_FILL"
            ],
        },
        "raw_evidence": {
            "release": args.raw_release,
            "asset": args.raw_asset,
            "size": args.raw_size,
            "sha256": args.raw_sha256,
        },
        "errors": errors,
    }
    write_json(args.output, result)
    if errors:
        raise SystemExit("; ".join(errors))


if __name__ == "__main__":
    main()

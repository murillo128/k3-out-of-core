#!/usr/bin/env python3
"""Freeze the issue #58 unmodified baseline and evidence-driven first hypothesis."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def select(cases: list[dict[str, object]], **fields: object) -> dict[str, object]:
    matches = [case for case in cases if all(case[key] == value for key, value in fields.items())]
    if len(matches) != 1:
        raise ValueError(f"expected one cell for {fields}, found {len(matches)}")
    return matches[0]


def compact(case: dict[str, object]) -> dict[str, object]:
    return {
        key: case[key]
        for key in (
            "case", "layout", "order", "api", "cache_state", "requested_qd",
            "useful_gbps", "latency_ms", "operation_elapsed_ms", "checksum_sink_sha256",
            "buffer_bytes", "maximum_active_operations", "rusage", "block_devices",
            "page_cache_pre_read", "swap_used_bytes", "lifetime_resources", "plan_sha256",
        )
    }


def delta(candidate: dict[str, object], baseline: dict[str, object], metric: str) -> float:
    return float(candidate[metric]) / float(baseline[metric]) - 1.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--fio", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    matrix = json.loads(args.matrix.read_text())
    fio = json.loads(args.fio.read_text())
    if matrix["status"] != "PASS" or fio["status"] != "PASS":
        raise ValueError("baseline input did not pass")
    cases = matrix["cases"]
    cold = [case for case in cases if case["cache_state"] == "OS_COLD_VERIFIED"]
    warm = [case for case in cases if case["cache_state"] == "OS_WARM"]
    highest_cold = max(cold, key=lambda case: float(case["useful_gbps"]))
    highest_warm = max(warm, key=lambda case: float(case["useful_gbps"]))
    selected_cold = select(
        cases, layout="A", order="LOGICAL_SELECTED", api="direct-pread",
        requested_qd=32, cache_state="OS_COLD_VERIFIED",
    )
    selected_warm = select(
        cases, layout="A", order="LOGICAL_SELECTED", api="direct-pread",
        requested_qd=32, cache_state="OS_WARM",
    )
    low_qd_ring = select(
        cases, layout="A", order="LOGICAL_SELECTED", api="direct-io-uring",
        requested_qd=4, cache_state="OS_COLD_VERIFIED",
    )
    candidate_baseline = select(
        cases, layout="A", order="LOGICAL_SELECTED", api="buffered-io-uring",
        requested_qd=16, cache_state="OS_COLD_VERIFIED",
    )
    candidate = select(
        cases, layout="A", order="LOCALITY_WINDOW_8", api="buffered-io-uring",
        requested_qd=16, cache_state="OS_COLD_VERIFIED",
    )
    warm_baseline = select(
        cases, layout="A", order="LOGICAL_SELECTED", api="buffered-io-uring",
        requested_qd=16, cache_state="OS_WARM",
    )
    warm_candidate = select(
        cases, layout="A", order="LOCALITY_WINDOW_8", api="buffered-io-uring",
        requested_qd=16, cache_state="OS_WARM",
    )
    layout_deltas: list[float] = []
    for left in cases:
        if left["layout"] != "A":
            continue
        right = select(cases, **{
            "layout": "B", "order": left["order"], "api": left["api"],
            "requested_qd": left["requested_qd"], "cache_state": left["cache_state"],
        })
        layout_deltas.append(abs(delta(right, left, "useful_gbps")))
    fio_peak = max(int(case["bw_bytes"]) for case in fio["cases"])
    document = {
        "schema_version": "phase12-nvme-baseline-analysis-v1",
        "status": "PASS",
        "matrix": identity(args.matrix),
        "fio": identity(args.fio),
        "frozen_baseline": {
            "name": "SINGLE_NVME_LAYOUT_A_LOGICAL_DIRECT_PREAD_QD32",
            "reason": "highest-throughput runtime-eligible Layout A cold cell under default logical order; Layout B remains evidence-only",
            "cold": compact(selected_cold),
            "warm_direct_comparator": compact(selected_warm),
            "fraction_of_fio_peak": float(selected_cold["useful_gbps"]) * 1e9 / fio_peak,
        },
        "resource_efficient_comparator": {
            "reason": "direct io_uring reaches the device ceiling at QD4 with fewer buffers and within 5% of the frozen baseline",
            "cell": compact(low_qd_ring),
            "throughput_delta_from_frozen_baseline": delta(low_qd_ring, selected_cold, "useful_gbps"),
        },
        "observed_extrema": {"highest_cold": compact(highest_cold), "highest_warm": compact(highest_warm)},
        "layout_comparison": {
            "median_absolute_relative_throughput_delta": statistics.median(layout_deltas),
            "maximum_absolute_relative_throughput_delta": max(layout_deltas),
            "disposition": "no broad Layout B advantage; comparator remains evidence-only",
        },
        "first_optimization_hypothesis": {
            "candidate": "LOCALITY_WINDOW_8_FOR_BUFFERED_IO_URING_QD16",
            "causal_basis": "single-pass matrix showed a cold throughput increase without a warm regression; direct and pread rows act as falsifiers for a general storage-layout effect",
            "baseline_cell": compact(candidate_baseline),
            "candidate_cell": compact(candidate),
            "observed_cold_throughput_delta": delta(candidate, candidate_baseline, "useful_gbps"),
            "observed_cold_p95_delta": float(candidate["latency_ms"]["p95"]) / float(candidate_baseline["latency_ms"]["p95"]) - 1.0,
            "observed_warm_throughput_delta": delta(warm_candidate, warm_baseline, "useful_gbps"),
            "screen": "three interleaved fresh-process cold baseline/candidate pairs",
            "promising_gate": "mean paired throughput or p95 improvement >=5%, other metric regression <=2%, all correctness/resource gates clean",
            "falsifier": "paired improvement below 5%, >2% regression, or any correctness/resource failure",
        },
        "matrix_conclusion": {
            "direct_single_nvme_is_hardware_bound": True,
            "mmap_is_comparator_only": True,
            "no_runtime_or_default_change_selected": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "baseline": document["frozen_baseline"]["name"], "next": document["first_optimization_hypothesis"]["candidate"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Qualify full-scale API/order/QD/cache fairness before screening."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from plan import build_plan, encode_plan  # noqa: E402

APIS = ("buffered-io-uring", "direct-io-uring", "buffered-pread", "direct-pread")
QDS = (1, 2, 4, 8, 16, 32)
ORDERS = ("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_case(
    binary: Path,
    plan: Path,
    output: Path,
    *,
    api: str,
    qd: int,
    cache_state: str,
) -> dict[str, object]:
    command = [
        str(binary), "--plan", str(plan), "--api", api,
        "--cache-state", cache_state, "--qd", str(qd),
        "--iterations", "1", "--output", str(output),
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"qualification case failed: {command}: {completed.stderr.strip()}")
    result = json.loads(output.read_text())
    result["command"] = command
    result["plan_sha256"] = sha256_file(plan)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"qualified {output.stem}: {result['useful_gbps']:.3f} GB/s", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    corpus = args.corpus.resolve()
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, object]] = []
    plans: dict[tuple[str, str], Path] = {}
    for layout in ("A", "B"):
        for order in ORDERS:
            path = raw / "plans" / f"layout-{layout.lower()}-{order.lower()}.tsv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encode_plan(build_plan(corpus, layout, "COLD_SPREAD", 0, order)))
            plans[(layout, order)] = path

    for layout in ("A", "B"):
        logical = plans[(layout, "LOGICAL_SELECTED")]
        for api in APIS:
            for qd in QDS:
                for cache_state in ("OS_COLD_VERIFIED", "OS_WARM"):
                    name = f"layout-{layout.lower()}__{api}__qd-{qd}__{cache_state.lower()}"
                    result = run_case(binary, logical, raw / f"{name}.json", api=api, qd=qd, cache_state=cache_state)
                    result.update({"layout": layout, "order": "LOGICAL_SELECTED", "case": name})
                    cases.append(result)
        for cache_state in ("OS_COLD_VERIFIED", "OS_WARM"):
            name = f"layout-{layout.lower()}__mmap-buffered__qd-1__{cache_state.lower()}"
            result = run_case(binary, logical, raw / f"{name}.json", api="mmap-buffered", qd=1, cache_state=cache_state)
            result.update({"layout": layout, "order": "LOGICAL_SELECTED", "case": name})
            cases.append(result)
        for order in ("PHYSICAL_OFFSET", "LOCALITY_WINDOW_8"):
            for cache_state in ("OS_COLD_VERIFIED", "OS_WARM"):
                name = f"layout-{layout.lower()}__buffered-pread__qd-8__{cache_state.lower()}__{order.lower()}"
                result = run_case(binary, plans[(layout, order)], raw / f"{name}.json", api="buffered-pread", qd=8, cache_state=cache_state)
                result.update({"layout": layout, "order": order, "case": name})
                cases.append(result)

    failures: list[str] = []
    sinks = {str(case["checksum_sink_sha256"]) for case in cases}
    if len(sinks) != 1:
        failures.append("checksum sinks differ across layout/API/order/QD/cache cells")
    for case in cases:
        if case["status"] != "PASS" or int(case["short_reads"]) != 0:
            failures.append(f"{case['case']}: correctness failure")
        if int(case["completed_operations"]) != int(case["operation_count_per_iteration"]) * int(case["iterations"]):
            failures.append(f"{case['case']}: incomplete operation count")
        if int(case["useful_bytes"]) != 25_829_572_608:
            failures.append(f"{case['case']}: unexpected useful-byte count")
        if int(case["swap_used_bytes"]) != 0:
            failures.append(f"{case['case']}: nonzero swap use")
        if case["lifetime_resources"] != {"fd_delta": 0, "thread_delta": 0}:
            failures.append(f"{case['case']}: process resource leak")
        if case["api"] != "mmap-buffered" and int(case["requested_qd"]) > 1 and case["effective_qd_status"] != "SUPPORTED":
            failures.append(f"{case['case']}: unsupported effective QD")
        if int(case["maximum_active_operations"]) != int(case["requested_qd"]):
            failures.append(f"{case['case']}: observed in-flight depth differs from requested QD")
        qd = int(case["requested_qd"])
        bundle_bytes = 17_547_264
        if "pread" in str(case["api"]):
            if int(case["worker_count"]) != qd or int(case["buffer_count"]) != qd or int(case["buffer_bytes"]) != qd * bundle_bytes:
                failures.append(f"{case['case']}: pread worker/buffer bound mismatch")
        elif "io-uring" in str(case["api"]):
            ring = case["io_uring"]
            if int(case["checksum_worker_count"]) != qd or int(case["buffer_count"]) != 2 * qd or int(case["buffer_bytes"]) != 2 * qd * bundle_bytes:
                failures.append(f"{case['case']}: io_uring worker/buffer bound mismatch")
            if int(ring["sq_entries"]) < qd or int(ring["cq_entries"]) < qd:
                failures.append(f"{case['case']}: io_uring SQ/CQ depth is smaller than requested")
        elif int(case["worker_count"]) != 1 or int(case["buffer_count"]) != 0:
            failures.append(f"{case['case']}: mmap resource bound mismatch")
        direct = case["direct_io"]
        expected_direct = str(case["api"]).startswith("direct-")
        if bool(direct["requested"]) != expected_direct or bool(direct["opened_with_o_direct"]) != expected_direct or bool(direct["buffered_fallback_allowed"]):
            failures.append(f"{case['case']}: direct-I/O diagnostic mismatch")
        cache = case["page_cache_pre_read"]
        if case["cache_state"] == "OS_COLD_VERIFIED" and "direct" not in str(case["api"]):
            if not cache["sampled"] or int(cache["fadvise_failures"]) or float(cache["resident_fraction"]) > 0.01:
                failures.append(f"{case['case']}: cold page residency not verified")
        if case["cache_state"] == "OS_WARM" and "direct" not in str(case["api"]):
            if not cache["sampled"] or float(cache["resident_fraction"]) < 0.90:
                failures.append(f"{case['case']}: warm page residency not verified")
    summary = {
        "schema_version": "phase12-nvme-harness-qualification-v1",
        "status": "PASS" if not failures else "FAIL",
        "case_count": len(cases),
        "layouts": ["A", "B"],
        "apis": [*APIS, "mmap-buffered"],
        "queue_depths": list(QDS),
        "orders": list(ORDERS),
        "cache_states": ["OS_COLD_VERIFIED", "OS_WARM"],
        "checksum_sink_sha256": next(iter(sinks)) if len(sinks) == 1 else None,
        "harness_identity": {
            "binary": {"path": str(binary), "sha256": sha256_file(binary)},
            "sources": {
                str(path.relative_to(ROOT)): sha256_file(path)
                for path in (
                    ROOT / "scripts/phase12_nvme/phase12_nvme_bench.cpp",
                    ROOT / "scripts/phase12_nvme/plan.py",
                    ROOT / "scripts/phase12_nvme/qualify_harness.py",
                )
            },
        },
        "failures": failures,
        "cases": [{
            key: case[key]
            for key in (
                "case", "layout", "order", "api", "cache_state", "requested_qd",
                "effective_qd_status", "effective_qd_basis", "maximum_active_operations",
                "worker_count", "checksum_worker_count", "buffer_count", "buffer_bytes",
                "direct_io", "io_uring", "fraction_active_at_least_two", "short_reads",
                "maximum_inflight_bytes", "completed_operations", "useful_bytes",
                "useful_gbps", "operation_elapsed_ms", "checksum_sink_sha256", "page_cache_pre_read",
                "rusage", "process_memory", "swap_used_bytes", "lifetime_resources",
                "block_devices", "plan_sha256",
            )
        } for case in cases],
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": summary["status"], "case_count": len(cases), "failures": failures}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

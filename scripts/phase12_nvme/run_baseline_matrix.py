#!/usr/bin/env python3
"""Run the complete unmodified storage-only baseline matrix for issue #58."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from plan import build_plan, encode_plan  # noqa: E402
from qualify_harness import run_case, sha256_file  # noqa: E402

APIS = ("buffered-io-uring", "direct-io-uring", "buffered-pread", "direct-pread")
QDS = (1, 2, 4, 8, 16, 32)
ORDERS = ("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8")
CACHE_STATES = ("OS_COLD_VERIFIED", "OS_WARM")
BUNDLE_BYTES = 17_547_264
USEFUL_BYTES = 25_829_572_608


def run_or_resume(
    binary: Path,
    plan: Path,
    output: Path,
    *,
    api: str,
    qd: int,
    cache_state: str,
    resume: bool,
) -> dict[str, object]:
    command = [
        str(binary), "--plan", str(plan), "--api", api,
        "--cache-state", cache_state, "--qd", str(qd),
        "--iterations", "1", "--output", str(output),
    ]
    if resume and output.is_file():
        result = json.loads(output.read_text())
        if (
            result.get("status") != "PASS"
            or result.get("command") != command
            or result.get("plan_sha256") != sha256_file(plan)
            or result.get("api") != api
            or int(result.get("requested_qd", 0)) != qd
            or result.get("cache_state") != cache_state
        ):
            raise ValueError(f"existing baseline cell identity mismatch: {output}")
        print(f"resumed {output.stem}: {result['useful_gbps']:.3f} GB/s", flush=True)
        return result
    return run_case(binary, plan, output, api=api, qd=qd, cache_state=cache_state)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    binary = args.binary.resolve()
    corpus = args.corpus.resolve()
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    plans: dict[tuple[str, str], Path] = {}
    for layout in ("A", "B"):
        for order in ORDERS:
            path = raw / "plans" / f"layout-{layout.lower()}-{order.lower()}.tsv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encode_plan(build_plan(corpus, layout, "COLD_SPREAD", 0, order)))
            plans[(layout, order)] = path

    cases: list[dict[str, object]] = []
    for layout in ("A", "B"):
        for order in ORDERS:
            plan = plans[(layout, order)]
            for api in APIS:
                for qd in QDS:
                    for cache_state in CACHE_STATES:
                        name = f"layout-{layout.lower()}__{order.lower()}__{api}__qd-{qd}__{cache_state.lower()}"
                        result = run_or_resume(
                            binary, plan, raw / f"{name}.json", api=api, qd=qd,
                            cache_state=cache_state, resume=args.resume,
                        )
                        result.update({"case": name, "layout": layout, "order": order})
                        cases.append(result)
            for cache_state in CACHE_STATES:
                name = f"layout-{layout.lower()}__{order.lower()}__mmap-buffered__qd-1__{cache_state.lower()}"
                result = run_or_resume(
                    binary, plan, raw / f"{name}.json", api="mmap-buffered", qd=1,
                    cache_state=cache_state, resume=args.resume,
                )
                result.update({"case": name, "layout": layout, "order": order})
                cases.append(result)

    failures: list[str] = []
    sinks = {str(case["checksum_sink_sha256"]) for case in cases}
    if len(cases) != 300:
        failures.append(f"expected 300 cells, observed {len(cases)}")
    if len(sinks) != 1:
        failures.append("checksum sinks differ")
    for case in cases:
        name = str(case["case"])
        qd = int(case["requested_qd"])
        if case["status"] != "PASS" or int(case["short_reads"]) or int(case["useful_bytes"]) != USEFUL_BYTES:
            failures.append(f"{name}: correctness/byte-count failure")
        if case["effective_qd_status"] != "SUPPORTED" or int(case["maximum_active_operations"]) != qd:
            failures.append(f"{name}: effective QD failure")
        if int(case["swap_used_bytes"]) or case["lifetime_resources"] != {"fd_delta": 0, "thread_delta": 0}:
            failures.append(f"{name}: swap or lifetime-resource failure")
        if "pread" in str(case["api"]):
            if int(case["worker_count"]) != qd or int(case["buffer_bytes"]) != qd * BUNDLE_BYTES:
                failures.append(f"{name}: pread resource-bound failure")
        elif "io-uring" in str(case["api"]):
            if int(case["checksum_worker_count"]) != qd or int(case["buffer_bytes"]) != 2 * qd * BUNDLE_BYTES:
                failures.append(f"{name}: io_uring resource-bound failure")
        cache = case["page_cache_pre_read"]
        if case["cache_state"] == "OS_COLD_VERIFIED" and "direct" not in str(case["api"]):
            if not cache["sampled"] or int(cache["fadvise_failures"]) or float(cache["resident_fraction"]) > 0.01:
                failures.append(f"{name}: cold residency failure")
        if case["cache_state"] == "OS_WARM" and "direct" not in str(case["api"]):
            if not cache["sampled"] or float(cache["resident_fraction"]) < 0.90:
                failures.append(f"{name}: warm residency failure")

    source_paths = (
        ROOT / "scripts/phase12_nvme/phase12_nvme_bench.cpp",
        ROOT / "scripts/phase12_nvme/plan.py",
        ROOT / "scripts/phase12_nvme/qualify_harness.py",
        ROOT / "scripts/phase12_nvme/run_baseline_matrix.py",
    )
    document = {
        "schema_version": "phase12-nvme-baseline-matrix-v1",
        "status": "PASS" if not failures else "FAIL",
        "baseline_identity": {
            "name": "UNMODIFIED_CHECKPOINT_A_HARNESS",
            "binary": {"path": str(binary), "sha256": sha256_file(binary)},
            "sources": {str(path.relative_to(ROOT)): sha256_file(path) for path in source_paths},
            "corpus_generation_sha256": sha256_file(corpus / "generation.json"),
            "route_class": "COLD_SPREAD",
            "route_token": 0,
        },
        "case_count": len(cases),
        "layouts": ["A", "B"],
        "apis": [*APIS, "mmap-buffered"],
        "orders": list(ORDERS),
        "queue_depths": list(QDS),
        "cache_states": list(CACHE_STATES),
        "checksum_sink_sha256": next(iter(sinks)) if len(sinks) == 1 else None,
        "failures": failures,
        "cases": cases,
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "case_count": len(cases), "failures": failures}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

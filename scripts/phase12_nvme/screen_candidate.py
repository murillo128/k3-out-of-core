#!/usr/bin/env python3
"""Run the first issue #58 causal candidate as interleaved cold pairs."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from plan import build_plan, encode_plan  # noqa: E402
from qualify_harness import run_case, sha256_file  # noqa: E402

BUNDLE_BYTES = 17_547_264
USEFUL_BYTES = 25_829_572_608
EXPECTED_SINK = "205a762e95ada0c9d731c7d47ef41adda5a4ef9fbd8ea650eb91a74b9207956d"
PAIR_ORDERS = (("baseline", "candidate"), ("candidate", "baseline"), ("baseline", "candidate"))


def paired_summary(values: list[float]) -> dict[str, object]:
    mean = statistics.mean(values)
    if len(values) < 2:
        interval = [mean, mean]
    else:
        critical = {2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776}.get(len(values), 2.571)
        radius = critical * statistics.stdev(values) / math.sqrt(len(values))
        interval = [mean - radius, mean + radius]
    return {
        "values": values,
        "mean": mean,
        "median": statistics.median(values),
        "paired_95_percent_interval": interval,
    }


def validate_case(case: dict[str, object], label: str) -> list[str]:
    failures: list[str] = []
    if case["status"] != "PASS" or int(case["short_reads"]) != 0:
        failures.append(f"{label}: correctness failure")
    if int(case["useful_bytes"]) != USEFUL_BYTES or int(case["iterations"]) != 1:
        failures.append(f"{label}: byte/iteration mismatch")
    if case["effective_qd_status"] != "SUPPORTED" or int(case["maximum_active_operations"]) != 16:
        failures.append(f"{label}: effective QD failure")
    if int(case["checksum_worker_count"]) != 16 or int(case["buffer_bytes"]) != 32 * BUNDLE_BYTES:
        failures.append(f"{label}: bounded-resource failure")
    ring = case["io_uring"]
    if int(ring["sq_entries"]) < 16 or int(ring["cq_entries"]) < 16:
        failures.append(f"{label}: SQ/CQ depth failure")
    cache = case["page_cache_pre_read"]
    if not cache["sampled"] or int(cache["fadvise_failures"]) or float(cache["resident_fraction"]) > 0.01:
        failures.append(f"{label}: cold residency failure")
    if int(case["swap_used_bytes"]) or case["lifetime_resources"] != {"fd_delta": 0, "thread_delta": 0}:
        failures.append(f"{label}: swap/lifetime failure")
    if case["checksum_sink_sha256"] != EXPECTED_SINK:
        failures.append(f"{label}: checksum sink failure")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--baseline-analysis", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    corpus = args.corpus.resolve()
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    plans: dict[str, Path] = {}
    for label, order in (("baseline", "LOGICAL_SELECTED"), ("candidate", "LOCALITY_WINDOW_8")):
        path = raw / "plans" / f"{label}.tsv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encode_plan(build_plan(corpus, "A", "COLD_SPREAD", 0, order)))
        plans[label] = path

    pairs: list[dict[str, object]] = []
    failures: list[str] = []
    for pair_number, execution_order in enumerate(PAIR_ORDERS, 1):
        results: dict[str, dict[str, object]] = {}
        for sequence, label in enumerate(execution_order, 1):
            output = raw / f"pair-{pair_number}__sequence-{sequence}__{label}.json"
            result = run_case(
                binary, plans[label], output, api="buffered-io-uring", qd=16,
                cache_state="OS_COLD_VERIFIED",
            )
            result.update({
                "candidate_label": label,
                "layout": "A",
                "order": "LOGICAL_SELECTED" if label == "baseline" else "LOCALITY_WINDOW_8",
                "pair": pair_number,
                "sequence": sequence,
            })
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            failures.extend(validate_case(result, f"pair-{pair_number}/{label}"))
            results[label] = result
        baseline = results["baseline"]
        candidate = results["candidate"]
        pairs.append({
            "pair": pair_number,
            "execution_order": list(execution_order),
            "baseline": baseline,
            "candidate": candidate,
            "throughput_relative_improvement": float(candidate["useful_gbps"]) / float(baseline["useful_gbps"]) - 1.0,
            "p95_relative_improvement": 1.0 - float(candidate["latency_ms"]["p95"]) / float(baseline["latency_ms"]["p95"]),
        })

    sinks = {
        str(pair[label]["checksum_sink_sha256"])
        for pair in pairs for label in ("baseline", "candidate")
    }
    if sinks != {EXPECTED_SINK}:
        failures.append("cross-pair checksum sinks differ")
    throughput = paired_summary([float(pair["throughput_relative_improvement"]) for pair in pairs])
    p95 = paired_summary([float(pair["p95_relative_improvement"]) for pair in pairs])
    threshold = 0.05
    regression_limit = -0.02
    promising = not failures and (
        (float(throughput["mean"]) >= threshold and float(p95["mean"]) >= regression_limit)
        or (float(p95["mean"]) >= threshold and float(throughput["mean"]) >= regression_limit)
    )
    document = {
        "schema_version": "phase12-nvme-candidate-screen-v1",
        "status": "PASS" if not failures else "FAIL",
        "candidate": "LOCALITY_WINDOW_8_FOR_BUFFERED_IO_URING_QD16",
        "disposition": "promising" if promising else ("invalid" if failures else "rejected"),
        "causal_change": "submission order only: LOGICAL_SELECTED to LOCALITY_WINDOW_8",
        "fixed_configuration": {
            "layout": "A", "api": "buffered-io-uring", "requested_qd": 16,
            "cache_state": "OS_COLD_VERIFIED", "request_class": "COLD_SPREAD", "route_token": 0,
            "iterations_per_process": 1,
        },
        "baseline_analysis": {
            "path": str(args.baseline_analysis),
            "sha256": sha256_file(args.baseline_analysis),
        },
        "binary": {"path": str(binary), "sha256": sha256_file(binary)},
        "plans": {label: {"path": str(path), "sha256": sha256_file(path)} for label, path in plans.items()},
        "pair_count": len(pairs),
        "pairs": pairs,
        "paired_metrics": {
            "throughput_relative_improvement": throughput,
            "p95_relative_improvement": p95,
        },
        "gate": {
            "improvement_threshold": threshold,
            "other_metric_regression_limit": regression_limit,
            "correctness_and_resources_clean": not failures,
        },
        "failures": failures,
        "next_action": (
            "confirm with five interleaved cold pairs plus at least 100 post-warmup token-equivalent samples per cell"
            if promising else "do not confirm; select the next independent causal hypothesis or stop under the campaign rule"
        ),
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"], "disposition": document["disposition"],
        "throughput_mean": throughput["mean"], "p95_mean": p95["mean"], "failures": failures,
    }, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure same-host single versus layer-parity dual independent NVMe."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from qualify_harness import run_case, sha256_file  # noqa: E402

USEFUL_BYTES = 25_829_572_608
PER_DRIVE_BYTES = USEFUL_BYTES // 2
EXPECTED_SINK = "205a762e95ada0c9d731c7d47ef41adda5a4ef9fbd8ea650eb91a74b9207956d"
PAIR_ORDERS = (
    ("single", "dual"), ("dual", "single"), ("single", "dual"),
    ("dual", "single"), ("single", "dual"),
)


def paired_summary(values: list[float]) -> dict[str, object]:
    mean = statistics.mean(values)
    radius = 2.776 * statistics.stdev(values) / math.sqrt(len(values))
    return {
        "values": values,
        "mean": mean,
        "median": statistics.median(values),
        "paired_95_percent_interval": [mean - radius, mean + radius],
    }


def validate(case: dict[str, object], label: str) -> list[str]:
    failures: list[str] = []
    if case["status"] != "PASS" or int(case["short_reads"]) or int(case["useful_bytes"]) != USEFUL_BYTES:
        failures.append(f"{label}: correctness/byte-count failure")
    if case["checksum_sink_sha256"] != EXPECTED_SINK:
        failures.append(f"{label}: checksum failure")
    if case["effective_qd_status"] != "SUPPORTED" or int(case["maximum_active_operations"]) != 32:
        failures.append(f"{label}: effective QD failure")
    if case["direct_io"] != {"requested": True, "opened_with_o_direct": True, "buffered_fallback_allowed": False}:
        failures.append(f"{label}: direct-I/O diagnostic failure")
    if int(case["swap_used_bytes"]) or case["lifetime_resources"] != {"fd_delta": 0, "thread_delta": 0}:
        failures.append(f"{label}: swap/lifetime failure")
    devices = case["block_devices"]
    sources = case["per_source_activity"]
    expected_devices = 1 if label == "single" else 2
    if len(devices) != expected_devices or len(sources) != expected_devices:
        failures.append(f"{label}: source/device count failure")
    expected_bytes = [USEFUL_BYTES] if label == "single" else [PER_DRIVE_BYTES, PER_DRIVE_BYTES]
    if sorted(int(item["read_bytes"]) for item in devices) != expected_bytes:
        failures.append(f"{label}: per-drive byte failure")
    expected_operations = [1_472] if label == "single" else [736, 736]
    if sorted(int(item["operation_intervals"]) for item in sources) != expected_operations:
        failures.append(f"{label}: per-source operation-count failure")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--dual-corpus", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    dual_corpus = json.loads(args.dual_corpus.read_text())
    if dual_corpus["status"] != "PASS":
        raise ValueError("dual corpus did not pass")
    plans = {
        label: Path(dual_corpus["plans"][f"{label}_namespace"]["path"])
        for label in ("single", "dual")
    }
    for label, path in plans.items():
        if sha256_file(path) != dual_corpus["plans"][f"{label}_namespace"]["sha256"]:
            raise ValueError(f"{label} plan identity mismatch")
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    pairs: list[dict[str, object]] = []
    failures: list[str] = []
    for pair_number, execution_order in enumerate(PAIR_ORDERS, 1):
        results: dict[str, dict[str, object]] = {}
        for sequence, label in enumerate(execution_order, 1):
            output = raw / f"pair-{pair_number}__sequence-{sequence}__{label}.json"
            result = run_case(
                binary, plans[label], output, api="direct-pread", qd=32,
                cache_state="OS_COLD_VERIFIED",
            )
            result.update({"namespace_mode": label.upper() + "_NAMESPACE", "pair": pair_number, "sequence": sequence})
            output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
            failures.extend(validate(result, label))
            results[label] = result
        single = results["single"]
        dual = results["dual"]
        pairs.append({
            "pair": pair_number,
            "execution_order": list(execution_order),
            "single": single,
            "dual": dual,
            "throughput_relative_improvement": float(dual["useful_gbps"]) / float(single["useful_gbps"]) - 1.0,
            "p95_relative_improvement": 1.0 - float(dual["latency_ms"]["p95"]) / float(single["latency_ms"]["p95"]),
        })
    throughput = paired_summary([float(pair["throughput_relative_improvement"]) for pair in pairs])
    p95 = paired_summary([float(pair["p95_relative_improvement"]) for pair in pairs])
    dual_devices: dict[str, list[dict[str, object]]] = {}
    dual_sources: dict[str, list[dict[str, object]]] = {}
    for pair in pairs:
        for device in pair["dual"]["block_devices"]:
            dual_devices.setdefault(str(device["stat_path"]), []).append(device)
        for source in pair["dual"]["per_source_activity"]:
            dual_sources.setdefault(str(source["block_stat_path"]), []).append(source)
    per_drive = []
    for stat_path in sorted(dual_devices):
        devices = dual_devices[stat_path]
        sources = dual_sources[stat_path]
        per_drive.append({
            "block_stat_path": stat_path,
            "read_bytes_per_run": sorted({int(item["read_bytes"]) for item in devices}),
            "read_operations_median": statistics.median(int(item["read_operations"]) for item in devices),
            "mean_read_service_ms_median": statistics.median(float(item["mean_read_service_ms"]) for item in devices),
            "mean_device_queue_depth_median": statistics.median(float(item["mean_queue_depth_during_io"]) for item in devices),
            "application_maximum_active_median": statistics.median(int(item["maximum_active_operations"]) for item in sources),
            "application_average_active_median": statistics.median(float(item["average_active_operations"]) for item in sources),
            "operation_p95_ms_median": statistics.median(float(item["operation_elapsed_ms"]["p95"]) for item in sources),
        })
    document = {
        "schema_version": "phase12-nvme-dual-comparison-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "invalid",
        "comparison": "same-host SINGLE_NAMESPACE versus DUAL_NAMESPACE",
        "mapping": dual_corpus["mapping"],
        "fixed_configuration": {
            "layout": "A", "order": "LOGICAL_SELECTED", "api": "direct-pread",
            "requested_qd": 32, "cache_state": "OS_COLD_VERIFIED",
            "request_class": "COLD_SPREAD", "route_token": 0,
        },
        "binary": {"path": str(binary), "sha256": sha256_file(binary)},
        "dual_corpus": {"path": str(args.dual_corpus), "sha256": sha256_file(args.dual_corpus)},
        "plans": {label: {"path": str(path), "sha256": sha256_file(path)} for label, path in plans.items()},
        "pair_count": len(pairs),
        "pairs": pairs,
        "paired_metrics": {
            "throughput_relative_improvement": throughput,
            "p95_relative_improvement": p95,
        },
        "dual_per_drive_summary": per_drive,
        "failures": failures,
        "interpretation": (
            "dual independent namespaces preserve correctness and expose measured same-host scaling"
            if not failures else "comparison is not valid"
        ),
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"], "throughput_mean": throughput["mean"],
        "p95_mean": p95["mean"], "failures": failures,
    }, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

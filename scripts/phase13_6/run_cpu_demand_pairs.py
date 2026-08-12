#!/usr/bin/env python3
"""Run and verify the Phase 13.6P serial/batched full-K3 CPU pairs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import statistics
import subprocess
import sys
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-corpus", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--cold-cache-bytes", type=int, default=96 * 1024**3)
    parser.add_argument("--warmup-limit", type=int, default=128)
    parser.add_argument("--decode-forwards", type=int, default=64)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--n-ctx", type=int, default=256)
    parser.add_argument("--nvme-devices", default="nvme0n1,nvme1n1")
    parser.add_argument("--parent-source", default=str(pathlib.Path(__file__).resolve().parents[2]))
    parser.add_argument("--model-identity", required=True)
    args = parser.parse_args()
    if args.pairs < 3 or args.decode_forwards < 64:
        parser.error("final evidence requires at least three pairs and 64 forwards")
    return args


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head(path: pathlib.Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True
    ).strip()


def read_fields(path: pathlib.Path) -> list[int]:
    return [int(value) for value in path.read_text().split()]


def nvme_snapshot(devices: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for device in devices:
        fields = read_fields(pathlib.Path("/sys/class/block") / device / "stat")
        result[device] = {
            "read_operations": fields[0],
            "read_merges": fields[1],
            "read_sectors": fields[2],
            "read_bytes": fields[2] * 512,
            "read_time_ms": fields[3],
            "write_operations": fields[4],
            "write_sectors": fields[6],
            "write_bytes": fields[6] * 512,
        }
    return result


def scalar_snapshot(path: pathlib.Path) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2:
            try:
                result[fields[0].rstrip(":")] = int(fields[1])
            except ValueError:
                result[fields[0].rstrip(":")] = " ".join(fields[1:])
    return result


def host_snapshot(devices: list[str]) -> dict[str, Any]:
    cgroup_events = pathlib.Path("/sys/fs/cgroup/memory.events")
    pressure = pathlib.Path("/proc/pressure/memory")
    return {
        "utc": utc_now(),
        "nvme": nvme_snapshot(devices),
        "meminfo": scalar_snapshot(pathlib.Path("/proc/meminfo")),
        "vmstat": scalar_snapshot(pathlib.Path("/proc/vmstat")),
        "cgroup_memory_events": scalar_snapshot(cgroup_events) if cgroup_events.exists() else {},
        "memory_pressure": pressure.read_text().splitlines() if pressure.exists() else [],
    }


def numeric_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    return {
        key: int(after[key]) - int(value)
        for key, value in before.items()
        if key in after and isinstance(value, int) and isinstance(after[key], int)
    }


def host_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "nvme": {
            device: numeric_delta(values, after["nvme"][device])
            for device, values in before["nvme"].items()
        },
        "vmstat": numeric_delta(before["vmstat"], after["vmstat"]),
        "cgroup_memory_events": numeric_delta(
            before["cgroup_memory_events"], after["cgroup_memory_events"]
        ),
    }


def verify_host_envelope(envelope: dict[str, Any]) -> None:
    delta = envelope["delta"]
    vmstat = delta["vmstat"]
    cgroup = delta["cgroup_memory_events"]
    failures = {
        "vmstat.pswpin": vmstat.get("pswpin", 0),
        "vmstat.pswpout": vmstat.get("pswpout", 0),
        "vmstat.oom_kill": vmstat.get("oom_kill", 0),
        "cgroup.oom": cgroup.get("oom", 0),
        "cgroup.oom_kill": cgroup.get("oom_kill", 0),
    }
    nonzero = {key: value for key, value in failures.items() if value != 0}
    if nonzero:
        raise RuntimeError(f"host swap/OOM evidence is nonzero: {nonzero}")


def write_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def selected_work(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_ids": result["output"]["generated_ids"],
        "fill_tokens": result["fill"]["tokens_to_full"],
        "fill_cold": result["fill"]["cold"],
        "fill_cold_delta": result["fill"]["cold_delta"],
        "fill_storage_delta": result["fill"]["storage_delta"],
        "measured_cold_before": result["measured"]["cold_before"],
        "measured_cold_after": result["measured"]["cold_after"],
        "measured_cold_delta": result["measured"]["cold_delta"],
        "measured_storage_delta": result["measured"]["storage_delta"],
        "terminal_references": result["resources"]["terminal_references"],
        "terminal_scheduler_active_requests": result["resources"][
            "terminal_scheduler_active_requests"
        ],
        "terminal_scheduler_queued_requests": result["resources"][
            "terminal_scheduler_queued_requests"
        ],
    }


def verify_pair(serial: dict[str, Any], batched: dict[str, Any]) -> None:
    if selected_work(serial) != selected_work(batched):
        raise RuntimeError("serial/batched logical work, output, cache, or terminal state differs")
    serial_async = serial["measured"]["async_delta"]
    batched_async = batched["measured"]["async_delta"]
    exact_async_fields = (
        "read_requests_submitted",
        "read_requests_completed",
        "read_requests_cancelled",
        "read_operations_completed",
        "read_bytes_completed",
        "direct_read_operations",
        "direct_useful_bytes",
        "direct_aligned_bytes",
        "buffered_fallback_operations",
        "synchronous_fallback_operations",
    )
    if any(serial_async[key] != batched_async[key] for key in exact_async_fields):
        raise RuntimeError("serial/batched async work differs")
    if serial_async["peak_active_read_requests_lifetime"] != 1:
        raise RuntimeError("serial control did not retain one-request issue width")
    if batched_async["peak_active_read_requests_lifetime"] < 2:
        raise RuntimeError("batched path did not prove concurrent expert requests")
    if serial_async["peak_active_operations_lifetime"] > 3:
        raise RuntimeError("serial control exceeded one K3 bundle operation width")
    if batched_async["peak_active_operations_lifetime"] < 6:
        raise RuntimeError("batched path did not prove concurrent read operations")


def log_ci(values: list[float]) -> dict[str, float]:
    logs = [math.log(value) for value in values]
    mean = statistics.fmean(logs)
    if len(logs) == 1:
        radius = 0.0
    else:
        t_critical = 4.303 if len(logs) == 3 else 2.776 if len(logs) == 5 else 1.96
        radius = t_critical * statistics.stdev(logs) / math.sqrt(len(logs))
    return {
        "geometric_mean": math.exp(mean),
        "ci95_low": math.exp(mean - radius),
        "ci95_high": math.exp(mean + radius),
    }


def build_summary(results: list[dict[str, Any]], preregistration: dict[str, Any]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    tps_ratios: list[float] = []
    p95_ratios: list[float] = []
    p99_ratios: list[float] = []
    peak_rss_ratios: list[float] = []
    for pair_index in range(preregistration["pair_count"]):
        pair_runs = [item for item in results if item["pair"] == pair_index + 1]
        serial = next(item["result"] for item in pair_runs if item["mode"] == "SERIAL")
        batched = next(item["result"] for item in pair_runs if item["mode"] == "BATCHED")
        verify_pair(serial, batched)
        tps_ratio = batched["measured"]["decode_tok_s"] / serial["measured"]["decode_tok_s"]
        p95_ratio = batched["measured"]["p95_forward_s"] / serial["measured"]["p95_forward_s"]
        p99_ratio = batched["measured"]["p99_forward_s"] / serial["measured"]["p99_forward_s"]
        peak_rss_ratio = batched["resources"]["peak_rss_kib"] / serial["resources"]["peak_rss_kib"]
        tps_ratios.append(tps_ratio)
        p95_ratios.append(p95_ratio)
        p99_ratios.append(p99_ratio)
        peak_rss_ratios.append(peak_rss_ratio)
        pairs.append({
            "pair": pair_index + 1,
            "tps_ratio_batched_over_serial": tps_ratio,
            "p95_latency_ratio_batched_over_serial": p95_ratio,
            "p99_latency_ratio_batched_over_serial": p99_ratio,
            "peak_rss_ratio_batched_over_serial": peak_rss_ratio,
        })
    tps = log_ci(tps_ratios)
    p95 = log_ci(p95_ratios)
    p99 = log_ci(p99_ratios)
    peak_rss = log_ci(peak_rss_ratios)
    floors = preregistration["materiality_floors"]
    materially_positive = tps["ci95_low"] > 1.0 + floors["median_decode_tps"]
    materially_negative = (
        tps["ci95_high"] < 1.0 - floors["median_decode_tps"]
        or p95["ci95_low"] > 1.0 + floors["p95_routed_forward_latency"]
        or peak_rss["ci95_low"] > 1.0 + floors["owned_or_pinned_memory"]
    )
    within_point_floors = (
        tps["geometric_mean"] >= 1.0 - floors["median_decode_tps"]
        and p95["geometric_mean"] <= 1.0 + floors["p95_routed_forward_latency"]
        and p99["geometric_mean"] <= 1.0 + floors["p95_routed_forward_latency"]
        and peak_rss["geometric_mean"] <= 1.0 + floors["owned_or_pinned_memory"]
    )
    parity_in_intervals = (
        tps["ci95_low"] <= 1.0 <= tps["ci95_high"]
        and p95["ci95_low"] <= 1.0 <= p95["ci95_high"]
        and peak_rss["ci95_low"] <= 1.0 <= peak_rss["ci95_high"]
    )
    if materially_positive:
        classification = "CPU_PERF_POSITIVE"
    elif materially_negative or not within_point_floors:
        classification = "CPU_PERF_NEGATIVE"
    elif parity_in_intervals:
        classification = "CPU_PERF_EQUIVALENT_CLEANER"
    else:
        classification = "CPU_PERF_AMBIGUOUS"
    return {
        "schema_version": "phase13-6p-cpu-paired-summary-v1",
        "status": "pass" if classification != "CPU_PERF_AMBIGUOUS" else "ambiguous",
        "classification": classification,
        "pairs": pairs,
        "tps_ratio": tps,
        "p95_latency_ratio": p95,
        "p99_latency_ratio": p99,
        "peak_rss_ratio": peak_rss,
        "parity_in_intervals": parity_in_intervals,
        "within_point_floors": within_point_floors,
        "all_work_and_outputs_equal": True,
        "preregistration": preregistration,
    }


def main() -> int:
    args = parse_args()
    binary = pathlib.Path(args.binary).resolve()
    model = pathlib.Path(args.model).resolve()
    prompt = pathlib.Path(args.prompt_corpus).resolve()
    output_root = pathlib.Path(args.output_root).resolve()
    parent = pathlib.Path(args.parent_source).resolve()
    nested = parent / "llama.cpp"
    devices = [item for item in args.nvme_devices.split(",") if item]
    output_root.mkdir(parents=True, exist_ok=True)
    pair_orders = [
        ["SERIAL", "BATCHED"] if index % 2 == 0 else ["BATCHED", "SERIAL"]
        for index in range(args.pairs)
    ]
    preregistration = {
        "schema_version": "phase13-6p-cpu-preregistration-v1",
        "created_utc": utc_now(),
        "pair_count": args.pairs,
        "pair_orders": pair_orders,
        "fresh_process_per_run": True,
        "decode_forwards": args.decode_forwards,
        "routing": "EXACT",
        "primary_endpoints": ["decode_tok_s", "p95_forward_s", "p99_forward_s"],
        "noise_model": {
            "method": "paired log-ratio Student-t 95% interval",
            "initial_between_process_envelope": 0.05,
            "basis": "conservative host envelope plus the retained issue-83 serial diagnostic",
            "retained_serial_decode_tok_s": 0.0343536078741,
        },
        "materiality_floors": {
            "median_decode_tps": 0.03,
            "p95_routed_forward_latency": 0.05,
            "owned_or_pinned_memory": 0.05,
        },
        "correctness_and_lifetime_tolerance": 0,
        "all_hit_rule": "no material regression in focused all-hit evidence",
        "identities": {
            "parent": git_head(parent),
            "nested": git_head(nested),
            "binary_sha256": sha256(binary),
            "model_identity": args.model_identity,
        },
    }
    preregistration_path = output_root / "preregistration.json"
    if preregistration_path.exists():
        existing = load_json(preregistration_path)
        preregistration["created_utc"] = existing.get("created_utc")
        if existing != preregistration:
            raise RuntimeError("existing preregistration differs; use a new output root")
        preregistration = existing
    else:
        write_json(preregistration_path, preregistration)

    completed: list[dict[str, Any]] = []
    run_number = 0
    for pair_index, order in enumerate(pair_orders, start=1):
        for mode in order:
            run_number += 1
            stem = f"run-{run_number:02d}-pair-{pair_index}-{mode.lower()}"
            result_path = output_root / f"{stem}.json"
            envelope_path = output_root / f"{stem}-envelope.json"
            if result_path.exists() and envelope_path.exists():
                print(f"resume {stem}", flush=True)
                envelope = load_json(envelope_path)
                verify_host_envelope(envelope)
                result = load_json(result_path)
                if result.get("status") != "pass" or result["execution"]["current_layer_issue_mode"] != mode:
                    raise RuntimeError(f"{stem} resumed result identity/status mismatch")
                completed.append({"pair": pair_index, "mode": mode, "result": result})
                continue
            command = [
                str(binary), "--model", str(model), "--prompt-corpus", str(prompt),
                "--output", str(result_path), "--point", "EXACT", "--issue-mode", mode,
                "--cold-cache-bytes", str(args.cold_cache_bytes),
                "--warmup-limit", str(args.warmup_limit),
                "--decode-forwards", str(args.decode_forwards),
                "--threads", str(args.threads), "--n-ctx", str(args.n_ctx),
            ]
            before = host_snapshot(devices)
            print(f"start {stem} {before['utc']}", flush=True)
            with (output_root / f"{stem}.stdout.log").open("w") as stdout, \
                    (output_root / f"{stem}.stderr.log").open("w") as stderr:
                process = subprocess.run(command, stdout=stdout, stderr=stderr, check=False)
            after = host_snapshot(devices)
            envelope = {
                "schema_version": "phase13-6p-cpu-run-envelope-v1",
                "run": run_number,
                "pair": pair_index,
                "mode": mode,
                "command": command,
                "exit_status": process.returncode,
                "before": before,
                "after": after,
                "delta": host_delta(before, after),
            }
            write_json(envelope_path, envelope)
            verify_host_envelope(envelope)
            if process.returncode != 0 or not result_path.exists():
                raise RuntimeError(f"{stem} failed with exit status {process.returncode}")
            result = load_json(result_path)
            if result.get("status") != "pass" or result["execution"]["current_layer_issue_mode"] != mode:
                raise RuntimeError(f"{stem} result identity/status mismatch")
            completed.append({"pair": pair_index, "mode": mode, "result": result})
            print(
                f"complete {stem} {after['utc']} tps={result['measured']['decode_tok_s']:.9f}",
                flush=True,
            )
        pair_results = [item for item in completed if item["pair"] == pair_index]
        verify_pair(
            next(item["result"] for item in pair_results if item["mode"] == "SERIAL"),
            next(item["result"] for item in pair_results if item["mode"] == "BATCHED"),
        )
        print(f"verified pair {pair_index}", flush=True)

    summary = build_summary(completed, preregistration)
    write_json(output_root / "paired-summary.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if summary["status"] == "pass" else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"run_cpu_demand_pairs: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

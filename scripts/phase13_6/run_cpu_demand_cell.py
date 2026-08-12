#!/usr/bin/env python3
"""Run one unprofiled Phase 13.6P CPU demand screening cell."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

import run_cpu_demand_pairs as pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt-corpus", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--mode", choices=("SERIAL", "BATCHED"), required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--nested-sha", required=True)
    parser.add_argument("--nested-base", required=True)
    parser.add_argument("--model-identity", required=True)
    parser.add_argument("--build-fingerprint", required=True)
    parser.add_argument("--cold-cache-bytes", type=int, default=96 * 1024**3)
    parser.add_argument("--warmup-limit", type=int, default=128)
    parser.add_argument("--decode-forwards", type=int, default=8)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--n-ctx", type=int, default=256)
    parser.add_argument("--nvme-devices", default="nvme0n1,nvme1n1")
    args = parser.parse_args()
    if args.decode_forwards < 1 or args.threads < 1:
        parser.error("decode-forwards and threads must be positive")
    return args


def process_status(pid: int) -> dict[str, int | str]:
    path = pathlib.Path("/proc") / str(pid) / "status"
    return pairs.scalar_snapshot(path) if path.exists() else {}


def scalar_value(path: pathlib.Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def main() -> int:
    args = parse_args()
    binary = pathlib.Path(args.binary).resolve()
    model = pathlib.Path(args.model).resolve()
    prompt = pathlib.Path(args.prompt_corpus).resolve()
    fingerprint = pathlib.Path(args.build_fingerprint).resolve()
    output_root = pathlib.Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    result_path = output_root / f"{args.name}.json"
    envelope_path = output_root / f"{args.name}-envelope.json"
    stdout_path = output_root / f"{args.name}.stdout.log"
    stderr_path = output_root / f"{args.name}.stderr.log"
    occupied = [path for path in (result_path, envelope_path, stdout_path, stderr_path) if path.exists()]
    if occupied:
        raise RuntimeError(f"refusing to overwrite existing cell artifacts: {occupied}")

    devices = [item for item in args.nvme_devices.split(",") if item]
    command = [
        str(binary),
        "--model", str(model),
        "--prompt-corpus", str(prompt),
        "--output", str(result_path),
        "--point", "EXACT",
        "--issue-mode", args.mode,
        "--cold-cache-bytes", str(args.cold_cache_bytes),
        "--warmup-limit", str(args.warmup_limit),
        "--decode-forwards", str(args.decode_forwards),
        "--threads", str(args.threads),
        "--n-ctx", str(args.n_ctx),
    ]
    before = pairs.host_snapshot(devices)
    cgroup_events = pairs.current_cgroup_memory_events()
    cgroup_current = cgroup_events.parent / "memory.current" if cgroup_events else None
    samples = {
        "count": 0,
        "minimum_mem_available_kib": before["meminfo"].get("MemAvailable"),
        "peak_cgroup_memory_current_bytes": scalar_value(cgroup_current) if cgroup_current else None,
        "peak_sampled_process_rss_kib": 0,
        "peak_sampled_process_hwm_kib": 0,
        "peak_sampled_process_swap_kib": 0,
        "cpu_affinity_allowed_list": None,
    }
    print(f"start {args.name} {before['utc']}", flush=True)
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        while True:
            meminfo = pairs.scalar_snapshot(pathlib.Path("/proc/meminfo"))
            status = process_status(process.pid)
            samples["count"] += 1
            available = meminfo.get("MemAvailable")
            if isinstance(available, int):
                previous = samples["minimum_mem_available_kib"]
                samples["minimum_mem_available_kib"] = available if previous is None else min(previous, available)
            current = scalar_value(cgroup_current) if cgroup_current else None
            if current is not None:
                previous = samples["peak_cgroup_memory_current_bytes"]
                samples["peak_cgroup_memory_current_bytes"] = current if previous is None else max(previous, current)
            for source, target in (
                ("VmRSS", "peak_sampled_process_rss_kib"),
                ("VmHWM", "peak_sampled_process_hwm_kib"),
                ("VmSwap", "peak_sampled_process_swap_kib"),
            ):
                value = status.get(source)
                if isinstance(value, int):
                    samples[target] = max(samples[target], value)
            affinity = status.get("Cpus_allowed_list")
            if affinity is not None:
                samples["cpu_affinity_allowed_list"] = affinity
            returncode = process.poll()
            if returncode is not None:
                break
            time.sleep(1)
    after = pairs.host_snapshot(devices)
    envelope: dict[str, Any] = {
        "schema_version": "phase13-6p-cpu-screen-envelope-v1",
        "name": args.name,
        "mode": args.mode,
        "command": command,
        "exit_status": returncode,
        "identities": {
            "project": args.project_sha,
            "nested": args.nested_sha,
            "nested_base": args.nested_base,
            "binary_sha256": pairs.sha256(binary),
            "model_identity_manifest_sha256": args.model_identity,
            "build_fingerprint_path": str(fingerprint),
            "build_fingerprint_sha256": pairs.sha256(fingerprint),
        },
        "resource_samples": samples,
        "before": before,
        "after": after,
        "delta": pairs.host_delta(before, after),
    }
    pairs.write_json(envelope_path, envelope)
    pairs.verify_host_envelope(envelope)
    if returncode != 0 or not result_path.exists():
        raise RuntimeError(f"cell failed with exit status {returncode}")
    result = pairs.load_json(result_path)
    if result.get("status") != "pass" or result["execution"]["current_layer_issue_mode"] != args.mode:
        raise RuntimeError("result identity/status mismatch")
    print(
        json.dumps(
            {
                "status": "pass",
                "name": args.name,
                "decode_tok_s": result["measured"]["decode_tok_s"],
                "time_to_full_s": result["fill"]["time_to_full_s"],
                "peak_rss_kib": result["resources"]["peak_rss_kib"],
                "minimum_mem_available_kib": samples["minimum_mem_available_kib"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"run_cpu_demand_cell: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

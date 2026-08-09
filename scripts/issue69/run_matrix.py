#!/usr/bin/env python3
"""Run bounded unprofiled issue 69 fixture cells in fresh processes."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from common import DEFAULT_COLD_BYTES, cmake_build_identity, probe_command, validate_workload, write_json


def meminfo() -> dict[str, int]:
    wanted = {"MemAvailable", "MemFree", "Cached", "Mlocked", "SwapFree", "SwapTotal"}
    result: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in wanted:
            result[key] = int(value.strip().split()[0])
    return result


def process_status(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM", "VmPin", "Threads"}:
                result[key] = int(value.strip().split()[0])
    except FileNotFoundError:
        pass
    return result


def gpu_sample() -> list[dict[str, object]]:
    command = [
        "nvidia-smi", "--query-gpu=index,uuid,utilization.gpu,memory.used,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.check_output(command, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    result: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 5:
            continue
        result.append({
            "cuda_ordinal": int(fields[0]), "uuid": fields[1],
            "gpu_utilization_percent": float(fields[2]), "memory_used_mib": float(fields[3]),
            "power_watts": None if fields[4] in {"N/A", "[N/A]"} else float(fields[4]),
        })
    return result


def compress(path: Path) -> Path:
    target = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=9) as destination:
        shutil.copyfileobj(source, destination)
    path.unlink()
    return target


def run_one(args: argparse.Namespace, cell: str, pair: int) -> None:
    stem = f"{cell}-{pair:02d}"
    raw = args.output_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    final = raw / f"{stem}.json.gz"
    if final.exists() and args.resume:
        print(f"skip {stem}: output exists", flush=True)
        return
    if final.exists():
        raise FileExistsError(final)
    workload = raw / f"{stem}.json"
    log = raw / f"{stem}.log"
    command = probe_command(
        args.probe, args.model, workload, cell, args.cold_bytes, args.runtime_mode,
        args.prewarm_cold_all, args.io_workers,
    )
    environment = os.environ.copy()
    environment.update({"GGML_CUDA_GRAPH_OPT": "0", "GGML_CUDA_DISABLE_GRAPHS": "1"})
    started_wall = time.time()
    started = time.monotonic()
    samples: list[dict[str, object]] = []
    print(f"start {stem}", flush=True)
    with log.open("wb") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
        while process.poll() is None:
            samples.append({
                "elapsed_seconds": time.monotonic() - started,
                "process": process_status(process.pid), "host": meminfo(), "gpus": gpu_sample(),
            })
            time.sleep(args.sample_period)
        returncode = process.wait()
    elapsed = time.monotonic() - started
    write_json(raw / f"{stem}-resources.json", {
        "schema_version": "issue69-run-resources-v1", "cell": cell, "pair": pair,
        "returncode": returncode, "started_unix_seconds": started_wall,
        "elapsed_seconds": elapsed, "pid": process.pid, "command": command,
        "environment": {key: environment[key] for key in ("GGML_CUDA_GRAPH_OPT", "GGML_CUDA_DISABLE_GRAPHS")},
        "build": cmake_build_identity(args.probe),
        "cold_bytes": args.cold_bytes, "prewarm_cold_all": args.prewarm_cold_all,
        "io_workers": args.io_workers, "samples": samples,
    })
    compress(log)
    if returncode != 0 or not workload.is_file():
        raise RuntimeError(f"{stem} failed with exit code {returncode}")
    validate_workload(workload)
    compress(workload)
    print(f"complete {stem}: {elapsed:.1f}s", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", required=True)
    parser.add_argument("--pairs", type=int, default=1)
    parser.add_argument("--start-pair", type=int, default=1)
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--runtime-mode", choices=("COMPLIANCE", "PRODUCTION_PERFORMANCE"),
        default="PRODUCTION_PERFORMANCE")
    parser.add_argument("--prewarm-cold-all", action="store_true")
    parser.add_argument("--io-workers", type=int)
    parser.add_argument("--sample-period", type=float, default=0.5)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.pairs < 1 or args.start_pair < 1 or args.cold_bytes <= 0 or args.sample_period <= 0:
        raise SystemExit("invalid run bounds")
    if not args.probe.is_file() or not args.model.is_file():
        raise SystemExit("probe or model is missing")
    for pair in range(args.start_pair, args.start_pair + args.pairs):
        for cell in [item.strip() for item in args.cells.split(",") if item.strip()]:
            run_one(args, cell, pair)
    write_json(args.output_dir / "matrix.json", {
        "schema_version": "issue69-run-matrix-v1", "status": "complete",
        "cells": [item.strip() for item in args.cells.split(",") if item.strip()],
        "pairs": args.pairs, "start_pair": args.start_pair,
        "cold_bytes": args.cold_bytes, "runtime_mode": args.runtime_mode,
        "prewarm_cold_all": args.prewarm_cold_all, "io_workers": args.io_workers,
        "build": cmake_build_identity(args.probe),
    })


if __name__ == "__main__":
    main()

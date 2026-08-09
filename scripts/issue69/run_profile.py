#!/usr/bin/env python3
"""Capture bounded process-wide perf profiles for issue 69 cells."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import time

from common import (DEFAULT_COLD_BYTES, cmake_build_identity, file_identity,
                    probe_command, validate_workload, write_json)
from run_matrix import gpu_sample, meminfo


PERF_EVENTS = (
    "task-clock,cycles,instructions,cache-references,cache-misses,"
    "context-switches,cpu-migrations,page-faults"
)


def matching_probe_pids(probe: Path) -> list[int]:
    expected = probe.resolve()
    result: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "exe").resolve() == expected:
                result.append(int(entry.name))
        except (FileNotFoundError, PermissionError):
            pass
    return result


def thread_snapshot(pid: int) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    try:
        for task in Path(f"/proc/{pid}/task").iterdir():
            result.append({"tid": int(task.name), "comm": (task / "comm").read_text().strip()})
    except FileNotFoundError:
        pass
    return sorted(result, key=lambda item: int(item["tid"]))


def run_one(args: argparse.Namespace, cell: str) -> None:
    directory = args.output_dir / cell
    if directory.exists():
        raise FileExistsError(directory)
    directory.mkdir(parents=True)
    workload = directory / "workload.json"
    perf_data = directory / "perf.data"
    perf_stat = directory / "perf-stat.jsonl"
    stdout = directory / "stdout.log"
    stderr = directory / "stderr.log"
    probe = probe_command(
        args.probe, args.model, workload, cell, args.cold_bytes,
        "PRODUCTION_PERFORMANCE", False, args.io_workers,
    )
    stat = [
        str(args.perf), "stat", "--delay", str(args.delay_ms), "--json-output",
        "--output", str(perf_stat), "--event", PERF_EVENTS, "--",
    ] + probe
    command = [
        str(args.perf), "record", "--delay", str(args.delay_ms),
        "--freq", str(args.frequency), "--call-graph", f"dwarf,{args.stack_bytes}",
        "--output", str(perf_data), "--max-size", f"{args.max_data_bytes}B",
        "--timestamp-boundary", "--",
    ] + stat
    environment = os.environ.copy()
    environment.update({"GGML_CUDA_GRAPH_OPT": "0", "GGML_CUDA_DISABLE_GRAPHS": "1"})
    samples: list[dict[str, object]] = []
    observed_probe_pids: set[int] = set()
    started = time.monotonic()
    print(f"start profile {cell}", flush=True)
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process = subprocess.Popen(command, stdout=out, stderr=err, env=environment)
        while process.poll() is None:
            pids = matching_probe_pids(args.probe)
            observed_probe_pids.update(pids)
            samples.append({
                "elapsed_seconds": time.monotonic() - started,
                "probe_processes": [
                    {"pid": pid, "threads": thread_snapshot(pid)} for pid in pids
                ],
                "host": meminfo(), "gpus": gpu_sample(),
            })
            time.sleep(args.sample_period)
        returncode = process.wait()
    elapsed = time.monotonic() - started
    if returncode != 0:
        raise RuntimeError(f"perf profile {cell} failed with exit code {returncode}")
    if len(observed_probe_pids) != 1:
        raise RuntimeError(f"expected one observed probe PID for {cell}, got {sorted(observed_probe_pids)}")
    evidence = validate_workload(workload)
    if not perf_data.is_file() or not perf_stat.is_file():
        raise RuntimeError(f"perf profile {cell} did not produce required output")
    write_json(directory / "capture.json", {
        "schema_version": "issue69-perf-capture-v1", "status": "complete", "cell": cell,
        "probe_pid": next(iter(observed_probe_pids)), "elapsed_seconds": elapsed,
        "delay_ms": args.delay_ms, "frequency_hz": args.frequency,
        "stack_bytes": args.stack_bytes, "max_data_bytes": args.max_data_bytes,
        "command": command, "probe_command": probe,
        "environment": {key: environment[key] for key in ("GGML_CUDA_GRAPH_OPT", "GGML_CUDA_DISABLE_GRAPHS")},
        "perf_version": subprocess.check_output([str(args.perf), "--version"], text=True).strip(),
        "kernel": subprocess.check_output(["uname", "-srvmo"], text=True).strip(),
        "build": cmake_build_identity(args.probe),
        "perf_data": file_identity(perf_data), "perf_stat": file_identity(perf_stat),
        "workload": file_identity(workload),
        "worker_count": evidence["async_io"]["diagnostics"]["worker_count"],
        "resource_samples": samples,
    })
    print(f"complete profile {cell}: {elapsed:.1f}s, {perf_data.stat().st_size} bytes", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", default="S0,S1,A1")
    parser.add_argument("--perf", type=Path, default=Path("/usr/bin/perf"))
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--io-workers", type=int)
    parser.add_argument("--delay-ms", type=int, default=25_000)
    parser.add_argument("--frequency", type=int, default=99)
    parser.add_argument("--stack-bytes", type=int, default=8192)
    parser.add_argument("--max-data-bytes", type=int, default=512 * 1024**2)
    parser.add_argument("--sample-period", type=float, default=0.5)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    if not args.probe.is_file() or not args.model.is_file() or not args.perf.is_file():
        raise SystemExit("probe, model, or perf is missing")
    if args.delay_ms < 0 or args.frequency < 1 or args.stack_bytes < 1024 or args.sample_period <= 0:
        raise SystemExit("invalid profile bounds")
    args.output_dir.mkdir(parents=True)
    for cell in [item.strip() for item in args.cells.split(",") if item.strip()]:
        run_one(args, cell)


if __name__ == "__main__":
    main()

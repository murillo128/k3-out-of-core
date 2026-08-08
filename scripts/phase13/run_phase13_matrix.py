#!/usr/bin/env python3
"""Run the fixed Phase 13 fresh-process DeepSeek matrix and capture resources."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


PROMPT = (
    "<｜begin▁of▁sentence｜><｜User｜>Explain why a careful measurement should distinguish "
    "observed facts from assumptions.<｜Assistant｜><think>"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--start-pair", type=int, default=1)
    parser.add_argument("--cells", default="A,B")
    parser.add_argument("--b-hot-slots", type=int, default=268)
    parser.add_argument("--sample-period", type=float, default=0.5)
    parser.add_argument(
        "--expert-runtime-mode", choices=("COMPLIANCE", "PRODUCTION_PERFORMANCE"),
        required=True)
    return parser.parse_args()


def read_proc_status(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM", "VmPin", "Threads"}:
                result[key] = int(value.strip().split()[0])
    except (FileNotFoundError, ProcessLookupError):
        pass
    return result


def read_meminfo() -> dict[str, int]:
    wanted = {"MemAvailable", "MemFree", "Cached", "Mlocked", "SwapFree"}
    result: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in wanted:
            result[key] = int(value.strip().split()[0])
    return result


def gpu_sample() -> list[dict[str, object]]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,uuid,utilization.gpu,utilization.memory,memory.used,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.check_output(command, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    result: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 6:
            continue
        result.append({
            "cuda_ordinal": int(fields[0]),
            "uuid": fields[1],
            "gpu_utilization_percent": float(fields[2]),
            "memory_utilization_percent": float(fields[3]),
            "memory_used_mib": float(fields[4]),
            "power_watts": None if fields[5] in {"[N/A]", "N/A"} else float(fields[5]),
        })
    return result


def command_for(args: argparse.Namespace, cell: str, output: Path) -> list[str]:
    if cell == "A":
        devices, hot_slots, staging = 1, 268, 0
    elif cell == "B":
        devices, hot_slots, staging = 2, args.b_hot_slots, 67108864
    elif cell == "Bprime":
        devices, hot_slots, staging = 2, 134, 67108864
    else:
        raise ValueError(f"unknown cell {cell}")
    command = [
        str(args.probe), "--model", str(args.model), "--output", str(output),
        "--mode", "cold", "--expert-runtime-mode", args.expert_runtime_mode, "--prompt", PROMPT,
        "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL",
        "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--hot-slots", str(hot_slots), "--cold-bytes", "17179869184",
        "--ring-bytes", "67173120", "--expert-devices", str(devices),
        "--peer-transport", "HOST_STAGED", "--peer-staging-bytes", str(staging),
        "--queue-depth", "256", "--trace-capacity",
        "65536" if args.expert_runtime_mode == "COMPLIANCE" else "0",
        "--n-ctx", "4096", "--n-batch", "128", "--n-ubatch", "128",
        "--max-generate", "24", "--background", "0", "--observe-routes",
        "1" if args.expert_runtime_mode == "COMPLIANCE" else "0",
        "--transport", "POSITIONAL", "--config-source", "EXPLICIT", "--integrity", "NONE",
    ]
    return command


def compress(path: Path) -> Path:
    target = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=9) as destination:
        shutil.copyfileobj(source, destination)
    path.unlink()
    return target


def run_one(args: argparse.Namespace, cell: str, pair: int) -> None:
    stem = f"{cell}-{pair:02d}"
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    final_output = raw_dir / f"{stem}.json.gz"
    if final_output.exists():
        print(f"skip {stem}: {final_output} exists", flush=True)
        return
    output = raw_dir / f"{stem}.json"
    log = raw_dir / f"{stem}.log"
    metadata = raw_dir / f"{stem}-resources.json"
    command = command_for(args, cell, output)
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
                "process": read_proc_status(process.pid),
                "host": read_meminfo(),
                "gpus": gpu_sample(),
            })
            time.sleep(args.sample_period)
        returncode = process.wait()
    ended = time.monotonic()
    record = {
        "schema_version": "phase13-run-resources-v1",
        "cell": cell,
        "pair": pair,
        "pid": process.pid,
        "returncode": returncode,
        "started_unix_seconds": started_wall,
        "elapsed_seconds": ended - started,
        "fresh_process_cache_state": "PROVIDER_COLD_OS_TMPFS_RESIDENT",
        "fixture_transport": "POSITIONAL",
        "expert_runtime_mode": args.expert_runtime_mode,
        "model_backing_path": str(args.model),
        "command": command,
        "environment": {key: environment[key] for key in ("GGML_CUDA_GRAPH_OPT", "GGML_CUDA_DISABLE_GRAPHS")},
        "samples": samples,
    }
    metadata.write_text(json.dumps(record, indent=2) + "\n")
    compress(log)
    if returncode != 0 or not output.exists():
        raise RuntimeError(f"{stem} failed with exit code {returncode}")
    with output.open() as stream:
        evidence = json.load(stream)
    if evidence.get("status") != "pass" or len(evidence.get("generated_ids", [])) != 24:
        raise RuntimeError(f"{stem} produced incomplete evidence")
    compress(output)
    print(f"complete {stem}: {ended - started:.1f}s", flush=True)


def main() -> None:
    args = parse_args()
    if args.pairs < 1 or args.start_pair < 1 or args.sample_period <= 0:
        raise SystemExit("invalid run bounds")
    if not args.probe.is_file() or not args.model.is_file():
        raise SystemExit("probe or model is missing")
    cells = [cell.strip() for cell in args.cells.split(",") if cell.strip()]
    for pair in range(args.start_pair, args.start_pair + args.pairs):
        for cell in cells:
            run_one(args, cell, pair)


if __name__ == "__main__":
    main()

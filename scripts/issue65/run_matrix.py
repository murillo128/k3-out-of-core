#!/usr/bin/env python3
"""Run issue 65 topology cells in fresh interleaved processes."""

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
    parser.add_argument("--cells", required=True)
    parser.add_argument("--pairs", type=int, default=1)
    parser.add_argument("--start-pair", type=int, default=1)
    parser.add_argument("--sample-period", type=float, default=0.5)
    parser.add_argument("--d2-slots", type=int)
    parser.add_argument("--a1-slots0", type=int)
    parser.add_argument("--a1-slots1", type=int)
    parser.add_argument(
        "--runtime-mode", choices=("COMPLIANCE", "PRODUCTION_PERFORMANCE"), required=True)
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
        "--query-gpu=index,pci.bus_id,uuid,utilization.gpu,utilization.memory,memory.total,memory.used,memory.free,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.check_output(command, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    result: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 9:
            continue
        result.append({
            "cuda_ordinal": int(fields[0]), "pci_bus_id": fields[1], "uuid": fields[2],
            "gpu_utilization_percent": float(fields[3]),
            "memory_utilization_percent": float(fields[4]),
            "memory_total_mib": float(fields[5]), "memory_used_mib": float(fields[6]),
            "memory_free_mib": float(fields[7]),
            "power_watts": None if fields[8] in {"[N/A]", "N/A"} else float(fields[8]),
        })
    return result


def cell_arguments(args: argparse.Namespace, cell: str) -> list[str]:
    if cell == "S0_LEGACY":
        return ["--hot-slots", "268", "--expert-devices", "1", "--role-config", "LEGACY",
                "--peer-staging-bytes", "0"]
    if cell == "S0_EXPLICIT":
        return ["--hot-slots", "268", "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", "0:268", "--peer-staging-bytes", "0"]
    if cell == "S1_LEGACY":
        return ["--hot-slots", "268", "--expert-devices", "2", "--role-config", "LEGACY",
                "--peer-staging-bytes", "67108864"]
    if cell == "S1_EXPLICIT":
        return ["--hot-slots", "268", "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", "0:268,1:268", "--peer-staging-bytes", "67108864"]
    if cell == "D1":
        return ["--hot-slots", "536", "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", "1:536", "--peer-staging-bytes", "67108864"]
    if cell == "D1_DELAYED":
        return ["--hot-slots", "536", "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", "1:536", "--peer-staging-bytes", "67108864",
                "--delay-device", "0", "--device-delay-us", "1000"]
    if cell == "D2" and args.d2_slots:
        return ["--hot-slots", str(args.d2_slots), "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", f"1:{args.d2_slots}", "--peer-staging-bytes", "67108864"]
    if cell == "A1" and args.a1_slots0 and args.a1_slots1:
        return ["--hot-slots", str(args.a1_slots0), "--role-config", "EXPLICIT", "--resident-device", "0",
                "--expert-role-devices", f"0:{args.a1_slots0},1:{args.a1_slots1}",
                "--peer-staging-bytes", "67108864"]
    raise ValueError(f"unknown or incompletely configured cell {cell}")


def command_for(args: argparse.Namespace, cell: str, output: Path) -> list[str]:
    compliance = args.runtime_mode == "COMPLIANCE"
    return [
        str(args.probe), "--model", str(args.model), "--output", str(output),
        "--mode", "cold", "--expert-runtime-mode", args.runtime_mode, "--prompt", PROMPT,
        "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL",
        "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--cold-bytes", "17179869184", "--ring-bytes", "67173120",
        "--peer-transport", "HOST_STAGED", "--queue-depth", "256",
        "--trace-capacity", "65536" if compliance else "0",
        "--n-ctx", "4096", "--n-batch", "128", "--n-ubatch", "128",
        "--max-generate", "24", "--background", "0",
        "--observe-routes", "1" if compliance else "0", "--transport", "POSITIONAL",
        "--config-source", "EXPLICIT", "--integrity", "NONE",
    ] + cell_arguments(args, cell)


def compress(path: Path) -> None:
    target = path.with_suffix(path.suffix + ".gz")
    with path.open("rb") as source, gzip.open(target, "wb", compresslevel=9) as destination:
        shutil.copyfileobj(source, destination)
    path.unlink()


def run_one(args: argparse.Namespace, cell: str, pair: int) -> None:
    stem = f"{cell}-{pair:02d}"
    raw_dir = args.output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    final_output = raw_dir / f"{stem}.json.gz"
    if final_output.exists():
        print(f"skip {stem}: output exists", flush=True)
        return
    output = raw_dir / f"{stem}.json"
    log = raw_dir / f"{stem}.log"
    resources = raw_dir / f"{stem}-resources.json"
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
                "process": read_proc_status(process.pid), "host": read_meminfo(), "gpus": gpu_sample(),
            })
            time.sleep(args.sample_period)
        returncode = process.wait()
    elapsed = time.monotonic() - started
    resources.write_text(json.dumps({
        "schema_version": "issue65-matrix-resources-v1", "cell": cell, "pair": pair,
        "returncode": returncode, "started_unix_seconds": started_wall, "elapsed_seconds": elapsed,
        "fresh_process_cache_state": "PROVIDER_COLD_OS_TMPFS_RESIDENT",
        "model_backing_path": str(args.model), "command": command,
        "environment": {key: environment[key] for key in ("GGML_CUDA_GRAPH_OPT", "GGML_CUDA_DISABLE_GRAPHS")},
        "samples": samples,
    }, indent=2) + "\n")
    compress(log)
    if returncode != 0 or not output.is_file():
        raise RuntimeError(f"{stem} failed with exit code {returncode}")
    evidence = json.loads(output.read_text())
    if evidence.get("status") != "pass" or len(evidence.get("generated_ids", [])) != 24:
        raise RuntimeError(f"{stem} produced incomplete evidence")
    compress(output)
    print(f"complete {stem}: {elapsed:.1f}s", flush=True)


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

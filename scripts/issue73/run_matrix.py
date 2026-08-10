#!/usr/bin/env python3
"""Run bounded full-K3 cases in fresh processes with host/device resource evidence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from common import (
    DEFAULT_COLD_BYTES,
    DEFAULT_PEER_STAGING_BYTES,
    DEFAULT_RING_BYTES,
    ROOT,
    decode_tps,
    output_identity,
    probe_command,
    validate_workload,
    write_json,
)


CGROUP_ROOT = Path("/sys/fs/cgroup")


def revision_state() -> dict[str, str]:
    nested = ROOT / "llama.cpp"
    result = {
        "project": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "nested": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=nested, text=True).strip(),
    }
    project_status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True)
    nested_status = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=nested, text=True)
    if project_status or nested_status:
        raise RuntimeError("full-K3 evidence requires clean project and nested worktrees")
    return result


def read_int_or_text(path: Path) -> int | str:
    value = path.read_text().strip()
    return int(value) if value.isdigit() else value


def cgroup_status(pid: int) -> dict[str, object]:
    try:
        entry = next(line for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
        directory = CGROUP_ROOT / entry.removeprefix("0::").lstrip("/")
        memory_stat = {
            key: int(value)
            for key, value in (line.split() for line in (directory / "memory.stat").read_text().splitlines())
            if key in {"anon", "file", "shmem", "file_mapped", "file_dirty", "swapcached"}
        }
        memory_events = {
            key: int(value)
            for key, value in (line.split() for line in (directory / "memory.events").read_text().splitlines())
        }
        return {
            "path": str(directory),
            "memory_current": read_int_or_text(directory / "memory.current"),
            "memory_peak": read_int_or_text(directory / "memory.peak"),
            "memory_swap_current": read_int_or_text(directory / "memory.swap.current"),
            "memory_stat": memory_stat,
            "memory_events": memory_events,
        }
    except (FileNotFoundError, StopIteration, ValueError):
        return {}


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
            if key in {
                    "VmRSS", "VmHWM", "VmPin", "Threads",
                    "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"}:
                result[key] = int(value.strip().split()[0])
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat[stat.rfind(")") + 2:].split()
        result.update({
            "minor_faults": int(fields[7]), "major_faults": int(fields[9]),
            "user_ticks": int(fields[11]), "system_ticks": int(fields[12]),
        })
        for line in Path(f"/proc/{pid}/io").read_text().splitlines():
            key, value = line.split(":", 1)
            result[f"io_{key}"] = int(value)
    except (FileNotFoundError, IndexError, ValueError):
        pass
    return result


def gpu_sample() -> list[dict[str, object]]:
    command = [
        "nvidia-smi", "--query-gpu=index,uuid,pci.bus_id,utilization.gpu,memory.used,memory.free,power.draw",
        "--format=csv,noheader,nounits",
    ]
    try:
        output = subprocess.check_output(command, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[dict[str, object]] = []
    for line in output.splitlines():
        fields = [item.strip() for item in line.split(",")]
        if len(fields) != 7:
            continue
        rows.append({
            "cuda_ordinal": int(fields[0]), "uuid": fields[1], "pci_bdf": fields[2],
            "gpu_utilization_percent": float(fields[3]), "memory_used_mib": float(fields[4]),
            "memory_free_mib": float(fields[5]),
            "power_watts": None if fields[6] in {"N/A", "[N/A]"} else float(fields[6]),
        })
    return rows


def block_status(path: Path) -> dict[str, int]:
    values = [int(value) for value in path.read_text().split()]
    return {
        "read_operations": values[0], "read_sectors": values[2], "read_ticks_ms": values[3],
        "in_flight": values[8], "io_ticks_ms": values[9], "weighted_ticks_ms": values[10],
    }


def block_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    result = {key: after[key] - before[key] for key in before if key != "in_flight"}
    result["read_bytes"] = result["read_sectors"] * 512
    return result


def swap_status() -> str:
    return subprocess.check_output(["swapon", "--show", "--bytes", "--noheadings"], text=True)


def drop_page_cache() -> None:
    subprocess.run(["sync"], check=True)
    Path("/proc/sys/vm/drop_caches").write_text("3\n")


def run_one(args: argparse.Namespace, ordinal: int) -> dict[str, object]:
    if revision_state() != args.revisions:
        raise RuntimeError("project or nested revision changed during the matrix")
    raw = args.output_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    stem = f"{args.case}-{ordinal:02d}"
    workload_path = raw / f"{stem}.json"
    log_path = raw / f"{stem}.log"
    resources_path = raw / f"{stem}-resources.json"
    if any(path.exists() for path in (workload_path, log_path, resources_path)):
        raise FileExistsError(f"case output already exists: {stem}")

    command = probe_command(
        args.probe, args.model, workload_path,
        role_devices=args.roles, n_gpu_layers=args.n_gpu_layers,
        n_ubatch=args.n_ubatch, max_generate=args.max_generate, cold_bytes=args.cold_bytes,
        ring_bytes=args.ring_bytes, peer_staging_bytes=args.peer_staging_bytes,
        io_workers=args.io_workers, queue_depth=args.queue_depth,
        async_cold_fill=args.async_cold_fill, transport=args.transport,
        runtime_mode=args.runtime_mode, miss_policy=args.miss_policy,
        observe_routes=args.observe_routes,
        trace_capacity=args.trace_capacity,
    )
    environment = os.environ.copy()
    environment.update({"GGML_CUDA_GRAPH_OPT": "0", "GGML_CUDA_DISABLE_GRAPHS": "1"})
    if args.drop_page_cache:
        drop_page_cache()
    block_before = block_status(args.block_stat)
    swap_before = swap_status()
    started_wall = time.time()
    started = time.monotonic()
    samples: list[dict[str, object]] = []
    with log_path.open("xb") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
        while process.poll() is None:
            samples.append({
                "elapsed_seconds": time.monotonic() - started,
                "process": process_status(process.pid), "host": meminfo(),
                "gpus": gpu_sample(), "cgroup": cgroup_status(process.pid),
            })
            time.sleep(args.sample_period)
        returncode = process.wait()
    elapsed = time.monotonic() - started
    block_after = block_status(args.block_stat)
    swap_after = swap_status()
    resources = {
        "schema_version": "issue73-run-resources-v1", "case": args.case, "run": ordinal,
        "measurement_tier": args.measurement_tier,
        "revisions": args.revisions,
        "returncode": returncode, "started_unix_seconds": started_wall, "elapsed_seconds": elapsed,
        "pid": process.pid, "command": command,
        "environment": {key: environment[key] for key in ("GGML_CUDA_GRAPH_OPT", "GGML_CUDA_DISABLE_GRAPHS")},
        "cache_state": "OS_COLD_REQUESTED_AND_DROPPED" if args.drop_page_cache else "UNCHANGED",
        "block_device": {
            "path": str(args.block_stat), "before": block_before, "after": block_after,
            "delta": block_delta(block_before, block_after),
        },
        "swap": {"before": swap_before, "after": swap_after}, "samples": samples,
    }
    write_json(resources_path, resources)
    if returncode != 0 or not workload_path.is_file():
        raise RuntimeError(f"{stem} failed with exit code {returncode}; see {log_path}")
    workload = validate_workload(workload_path, args.max_generate)
    result = {
        "case": args.case, "run": ordinal, "elapsed_seconds": elapsed,
        "decode_tps": decode_tps(workload), "output_identity": output_identity(workload),
        "workload": str(workload_path), "resources": str(resources_path), "log": str(log_path),
    }
    print(f"complete {stem}: decode_tps={result['decode_tps']:.6f} elapsed={elapsed:.1f}s", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--roles", required=True)
    parser.add_argument("--n-gpu-layers", type=int, required=True)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--start-run", type=int, default=1)
    parser.add_argument("--n-ubatch", type=int, default=4)
    parser.add_argument("--max-generate", type=int, default=24)
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--ring-bytes", type=int, default=DEFAULT_RING_BYTES)
    parser.add_argument("--peer-staging-bytes", type=int, default=DEFAULT_PEER_STAGING_BYTES)
    parser.add_argument("--io-workers", type=int, default=4)
    parser.add_argument("--queue-depth", type=int, default=64)
    parser.add_argument("--transport", choices=("POSITIONAL", "BUFFERED", "DIRECT_IO", "DIRECT_IO_POSITIONAL"), default="POSITIONAL")
    parser.add_argument("--runtime-mode", choices=("COMPLIANCE", "PRODUCTION_PERFORMANCE"), default="PRODUCTION_PERFORMANCE")
    parser.add_argument("--miss-policy", choices=("PROMOTE_AND_GPU", "CPU_FALLBACK"), default="PROMOTE_AND_GPU")
    parser.add_argument("--measurement-tier", choices=("P0", "P1", "P-TRACE"), default="P0")
    parser.add_argument("--async-cold-fill", action="store_true")
    parser.add_argument("--observe-routes", action="store_true")
    parser.add_argument("--trace-capacity", type=int, default=0)
    parser.add_argument("--sample-period", type=float, default=0.5)
    parser.add_argument("--drop-page-cache", action="store_true")
    parser.add_argument("--block-stat", type=Path, default=Path("/sys/block/sda/stat"))
    args = parser.parse_args()
    if (args.runs < 1 or args.start_run < 1 or args.n_gpu_layers < 0 or args.n_ubatch < 1 or
            args.max_generate < 1 or args.sample_period <= 0):
        raise SystemExit("invalid full-K3 run bounds")
    if not args.probe.is_file() or not args.model.is_file() or not args.block_stat.is_file():
        raise SystemExit("probe, model, or block-stat path is missing")
    args.revisions = revision_state()
    results = [run_one(args, run) for run in range(args.start_run, args.start_run + args.runs)]
    write_json(args.output_dir / "matrix.json", {
        "schema_version": "issue73-run-matrix-v1", "status": "complete",
        "case": args.case, "roles": args.roles, "n_gpu_layers": args.n_gpu_layers,
        "n_ubatch": args.n_ubatch, "miss_policy": args.miss_policy,
        "measurement_tier": args.measurement_tier,
        "revisions": args.revisions,
        "runs": results,
    })


if __name__ == "__main__":
    main()

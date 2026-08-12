#!/usr/bin/env python3
"""Capture synchronized host/process residency evidence around one Mode-P cell."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

import run_cpu_demand_pairs as pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary")
    parser.add_argument("--model")
    parser.add_argument("--prompt-corpus")
    parser.add_argument("--output-root")
    parser.add_argument("--name")
    parser.add_argument("--cold-cache-bytes", type=int)
    parser.add_argument("--decode-forwards", type=int, default=3)
    parser.add_argument("--threads", type=int, default=32)
    parser.add_argument("--n-ctx", type=int, default=256)
    parser.add_argument("--warmup-limit", type=int, default=128)
    parser.add_argument(
        "--snapshots",
        help="comma-separated lifecycle=elapsed_seconds entries",
    )
    parser.add_argument("--sample-interval", type=float, default=0.5)
    parser.add_argument("--perf-start", type=float)
    parser.add_argument("--perf-duration", type=float, default=25.0)
    parser.add_argument("--nvme-devices", default="nvme0n1,nvme1n1")
    parser.add_argument("--recover-root")
    args = parser.parse_args()
    required = (
        "binary", "model", "prompt_corpus", "output_root", "name",
        "cold_cache_bytes", "snapshots",
    )
    if args.recover_root is None and any(getattr(args, key) is None for key in required):
        parser.error("ordinary capture requires binary/model/prompt/output/name/cache/snapshots")
    if args.decode_forwards < 1 or args.threads < 1 or args.sample_interval <= 0:
        parser.error("decode-forwards, threads, and sample-interval must be positive")
    return args


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_snapshot_plan(value: str) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in value.split(","):
        label, separator, elapsed = item.partition("=")
        if not separator or not label or label in seen:
            raise ValueError(f"invalid or duplicate snapshot entry: {item!r}")
        seconds = float(elapsed)
        if seconds < 0:
            raise ValueError(f"snapshot time must be nonnegative: {item!r}")
        seen.add(label)
        result.append((label, seconds))
    return sorted(result, key=lambda item: item[1])


def scalar_value(path: pathlib.Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
        return None


def read_optional(path: pathlib.Path, binary: bool = False) -> bytes | str | None:
    try:
        return path.read_bytes() if binary else path.read_text()
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None


def write_json(path: pathlib.Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def cgroup_directory() -> pathlib.Path | None:
    for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            directory = pathlib.Path("/sys/fs/cgroup") / fields[2].lstrip("/")
            return directory if directory.is_dir() else None
    return None


def snapshot_summary(pid: int | None, devices: list[str], cgroup: pathlib.Path | None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "utc": utc_now(),
        "monotonic_s": time.monotonic(),
        "meminfo": pairs.scalar_snapshot(pathlib.Path("/proc/meminfo")),
        "vmstat": pairs.scalar_snapshot(pathlib.Path("/proc/vmstat")),
        "nvme": pairs.nvme_snapshot(devices),
    }
    pressure = read_optional(pathlib.Path("/proc/pressure/memory"))
    summary["host_memory_pressure"] = pressure.splitlines() if isinstance(pressure, str) else None
    if cgroup is not None:
        summary["cgroup"] = {
            "path": str(cgroup),
            "memory_current": scalar_value(cgroup / "memory.current"),
            "memory_stat": pairs.scalar_snapshot(cgroup / "memory.stat"),
            "memory_events": pairs.scalar_snapshot(cgroup / "memory.events"),
        }
        cgroup_pressure = read_optional(cgroup / "memory.pressure")
        summary["cgroup"]["memory_pressure"] = (
            cgroup_pressure.splitlines() if isinstance(cgroup_pressure, str) else None
        )
    if pid is not None:
        process = pathlib.Path("/proc") / str(pid)
        if process.exists():
            values: dict[str, Any] = {"pid": pid}
            for name in ("status", "io", "smaps_rollup"):
                try:
                    values[name] = pairs.scalar_snapshot(process / name)
                except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                    values[name] = None
            summary["process"] = values
    return summary


def recover_existing(root: pathlib.Path, devices: list[str]) -> int:
    metadata_path = root / "metadata.json"
    result_path = root / "result.json"
    samples_path = root / "samples.jsonl"
    metadata = json.loads(metadata_path.read_text())
    result = json.loads(result_path.read_text())
    if result.get("status") != "pass":
        raise RuntimeError("cannot recover a non-passing probe result")
    with samples_path.open() as source:
        last_line = None
        for line in source:
            if line.strip():
                last_line = line
    if last_line is None:
        raise RuntimeError("cannot recover without a live-process sample")
    last_sample = json.loads(last_line)
    cgroup = cgroup_directory()
    after_path = root / "snapshots" / "host-after-recovery" / "snapshot.json"
    after = (
        json.loads(after_path.read_text())
        if after_path.exists()
        else capture_full_snapshot(
            root, "host-after-recovery", None, time.monotonic(), devices, cgroup
        )
    )
    captured = [
        directory.name
        for directory in sorted((root / "snapshots").iterdir())
        if (directory / "snapshot.json").exists()
    ]
    metadata.update(
        {
            "exit_status": result.get("exit_status", 0),
            "ended_utc": utc_now(),
            "elapsed_s": last_sample["elapsed_s"],
            "captured_snapshots": captured,
            "terminal_process_snapshot_captured": (
                root / "snapshots" / "terminal-process" / "snapshot.json"
            ).exists(),
            "last_process_sample": last_sample,
            "host_after": after,
            "recovery": {
                "classification": "probe passed; wrapper lost /proc race during terminal snapshot",
                "terminal_evidence": "last live sample captured post-run deallocation state",
            },
        }
    )
    write_json(metadata_path, metadata)
    return 0


def capture_full_snapshot(
    root: pathlib.Path,
    label: str,
    pid: int | None,
    started: float,
    devices: list[str],
    cgroup: pathlib.Path | None,
) -> dict[str, Any]:
    directory = root / "snapshots" / label
    directory.mkdir(parents=True, exist_ok=False)
    summary = snapshot_summary(pid, devices, cgroup)
    summary["label"] = label
    summary["elapsed_s"] = time.monotonic() - started
    summary["process_capture"] = {}
    if pid is not None:
        process = pathlib.Path("/proc") / str(pid)
        for name in ("status", "io", "smaps_rollup", "smaps", "numa_maps", "maps", "cmdline"):
            content = read_optional(process / name, binary=name == "cmdline")
            available = content is not None
            summary["process_capture"][name] = available
            if content is not None:
                target = directory / name
                if isinstance(content, bytes):
                    target.write_bytes(content)
                else:
                    target.write_text(content)
    if cgroup is not None:
        for name in ("memory.current", "memory.stat", "memory.events", "memory.numa_stat", "memory.pressure"):
            content = read_optional(cgroup / name)
            if isinstance(content, str):
                (directory / f"cgroup-{name}").write_text(content)
    for source, target in (
        (pathlib.Path("/proc/meminfo"), "meminfo"),
        (pathlib.Path("/proc/vmstat"), "vmstat"),
        (pathlib.Path("/proc/pressure/memory"), "host-memory-pressure"),
    ):
        content = read_optional(source)
        if isinstance(content, str):
            (directory / target).write_text(content)
    write_json(directory / "snapshot.json", summary)
    return summary


def launch_perf(pid: int, root: pathlib.Path, duration: float) -> list[subprocess.Popen[Any]]:
    stat_command = [
        "perf", "stat", "-p", str(pid),
        "-e", "task-clock,cycles,instructions,page-faults,minor-faults,major-faults,context-switches,cpu-migrations",
        "-o", str(root / "perf-stat.txt"),
        "--", "sleep", str(duration),
    ]
    record_command = [
        "perf", "record", "-p", str(pid),
        "-e", "major-faults", "-c", "100",
        "--call-graph", "dwarf,8192",
        "-o", str(root / "perf-major-faults.data"),
        "--", "sleep", str(duration),
    ]
    (root / "perf-commands.json").write_text(
        json.dumps({"stat": stat_command, "record": record_command}, indent=2) + "\n"
    )
    return [subprocess.Popen(stat_command), subprocess.Popen(record_command)]


def main() -> int:
    args = parse_args()
    devices = [item for item in args.nvme_devices.split(",") if item]
    if args.recover_root is not None:
        return recover_existing(pathlib.Path(args.recover_root).resolve(), devices)
    binary = pathlib.Path(args.binary).resolve()
    model = pathlib.Path(args.model).resolve()
    prompt = pathlib.Path(args.prompt_corpus).resolve()
    output_root = pathlib.Path(args.output_root).resolve()
    root = output_root / args.name
    root.mkdir(parents=True, exist_ok=False)
    result_path = root / "result.json"
    stdout_path = root / "stdout.log"
    stderr_path = root / "stderr.log"
    plan = parse_snapshot_plan(args.snapshots)
    cgroup = cgroup_directory()
    command = [
        str(binary),
        "--model", str(model),
        "--prompt-corpus", str(prompt),
        "--output", str(result_path),
        "--point", "EXACT",
        "--issue-mode", "BATCHED",
        "--cold-cache-bytes", str(args.cold_cache_bytes),
        "--warmup-limit", str(args.warmup_limit),
        "--decode-forwards", str(args.decode_forwards),
        "--threads", str(args.threads),
        "--n-ctx", str(args.n_ctx),
    ]
    started = time.monotonic()
    before = capture_full_snapshot(root, "host-before", None, started, devices, cgroup)
    metadata: dict[str, Any] = {
        "schema_version": "phase13-6p-residency-cell-v1",
        "name": args.name,
        "classification": "profiled residency-attribution diagnostic; not endpoint TPS evidence",
        "command": command,
        "binary_sha256": pairs.sha256(binary),
        "snapshot_plan": [{"label": label, "elapsed_s": elapsed} for label, elapsed in plan],
        "sample_interval_s": args.sample_interval,
        "perf_start_s": args.perf_start,
        "perf_duration_s": args.perf_duration if args.perf_start is not None else None,
        "started_utc": utc_now(),
        "cgroup": str(cgroup) if cgroup is not None else None,
        "host_before": before,
        "limitations": {
            "host_psi": not pathlib.Path("/proc/pressure/memory").exists(),
            "cgroup_psi": cgroup is None or not (cgroup / "memory.pressure").exists(),
            "tracefs_readable": os.access("/sys/kernel/tracing/events", os.R_OK),
        },
    }
    write_json(root / "metadata.json", metadata)
    captured: list[dict[str, Any]] = []
    series_path = root / "samples.jsonl"
    perf_processes: list[subprocess.Popen[Any]] = []
    perf_started = False
    terminal_captured = False
    last_process_sample: dict[str, Any] | None = None
    print(f"start {args.name} {metadata['started_utc']}", flush=True)
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr, series_path.open("w") as series:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        metadata["pid"] = process.pid
        write_json(root / "metadata.json", metadata)
        pending = list(plan)
        while True:
            elapsed = time.monotonic() - started
            sample = snapshot_summary(process.pid, devices, cgroup)
            sample["elapsed_s"] = elapsed
            if "process" in sample:
                last_process_sample = sample
            series.write(json.dumps(sample, sort_keys=True) + "\n")
            series.flush()
            while pending and elapsed >= pending[0][1]:
                label, _ = pending.pop(0)
                captured.append(capture_full_snapshot(root, label, process.pid, started, devices, cgroup))
            if args.perf_start is not None and not perf_started and elapsed >= args.perf_start:
                perf_processes = launch_perf(process.pid, root, args.perf_duration)
                perf_started = True
            if result_path.exists() and not terminal_captured:
                captured.append(
                    capture_full_snapshot(root, "terminal-process", process.pid, started, devices, cgroup)
                )
                terminal_captured = True
            returncode = process.poll()
            if returncode is not None:
                break
            time.sleep(args.sample_interval)
    for perf_process in perf_processes:
        perf_process.wait()
    after = capture_full_snapshot(root, "host-after", None, started, devices, cgroup)
    metadata.update(
        {
            "exit_status": returncode,
            "ended_utc": utc_now(),
            "elapsed_s": time.monotonic() - started,
            "captured_snapshots": [item["label"] for item in captured],
            "terminal_process_snapshot_captured": terminal_captured,
            "last_process_sample": last_process_sample,
            "host_after": after,
        }
    )
    write_json(root / "metadata.json", metadata)
    if returncode != 0 or not result_path.exists():
        raise RuntimeError(f"cell failed with exit status {returncode}")
    result = json.loads(result_path.read_text())
    if result.get("status") != "pass":
        raise RuntimeError("probe result did not pass")
    print(
        json.dumps(
            {
                "status": "pass",
                "name": args.name,
                "elapsed_s": metadata["elapsed_s"],
                "diagnostic_decode_tok_s": result["measured"]["decode_tok_s"],
                "major_faults": result["measured"]["major_faults"],
                "peak_rss_kib": result["resources"]["peak_rss_kib"],
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
        print(f"capture_residency_cell: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

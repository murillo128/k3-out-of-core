#!/usr/bin/env python3
"""Fail-closed external-system Perfetto capture for Phase 12.5."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import select
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import file_identity, sha256, trace_processor_version, write_json

MAX_CONFIGURED_BUFFER = 1024 * 1024 * 1024
MAX_TRACE_BYTES = 2 * 1024 * 1024 * 1024
REQUIRED_SOURCES = ("track_event", "linux.ftrace", "linux.process_stats", "linux.sys_stats", "linux.system_info")


def ftrace_event_availability(tracefs: Path, event: str) -> dict[str, Any]:
    enable = tracefs / "events" / event / "enable"
    event_format = tracefs / "events" / event / "format"
    trace_marker = tracefs / "trace_marker"
    # ftrace/print is the trace_marker pseudo-event.  Kernels expose its
    # format and trace_marker endpoint but intentionally provide no
    # per-event enable file; Perfetto enables it by requesting the event.
    pseudo_print = event == "ftrace/print" and event_format.is_file() and trace_marker.exists()
    return {
        "event": event,
        "enable_path": str(enable),
        "format_path": str(event_format),
        "pseudo_event": event == "ftrace/print",
        "available": enable.is_file() or pseudo_print,
    }


def read_spec(path: Path) -> tuple[list[str], dict[str, str]]:
    value = json.loads(path.read_text())
    if isinstance(value, list):
        command, extra_env = value, {}
    elif isinstance(value, dict):
        command, extra_env = value.get("command"), value.get("environment", {})
    else:
        raise ValueError("command JSON must be an array or object")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command must be a non-empty string array")
    if not isinstance(extra_env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()):
        raise ValueError("environment must be a string map")
    forbidden = [key for key in extra_env if any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "KEY"))]
    if forbidden:
        raise ValueError("secret-like environment names are forbidden: " + ",".join(sorted(forbidden)))
    return command, extra_env


def config_preflight(path: Path) -> dict[str, Any]:
    text = path.read_text()
    sizes = [int(value) * 1024 for value in re.findall(r"\bsize_kb:\s*(\d+)", text)]
    if not sizes or sum(sizes) > MAX_CONFIGURED_BUFFER:
        raise ValueError("configured Perfetto buffers must total 1 GiB or less")
    fill_policies = re.findall(r"\bfill_policy:\s*(\w+)", text)
    if len(fill_policies) != len(sizes) or any(value != "DISCARD" for value in fill_policies):
        raise ValueError("every qualifying buffer must use fail-closed DISCARD policy")
    if not re.search(r"\bwrite_into_file:\s*true\b", text):
        raise ValueError("qualifying config must periodically drain buffers into the bounded output file")
    file_limits = [int(value) for value in re.findall(r"\bmax_file_size_bytes:\s*(\d+)", text)]
    if file_limits != [MAX_TRACE_BYTES]:
        raise ValueError("qualifying config must set the exact 2 GiB output-file maximum")
    write_periods = [int(value) for value in re.findall(r"\bfile_write_period_ms:\s*(\d+)", text)]
    if len(write_periods) != 1 or not 100 <= write_periods[0] <= 5000:
        raise ValueError("qualifying config must use a 100-5000 ms periodic file drain")
    flush_periods = [int(value) for value in re.findall(r"\bflush_period_ms:\s*(\d+)", text)]
    if len(flush_periods) != 1 or not 100 <= flush_periods[0] <= 30000:
        raise ValueError("qualifying config must periodically flush producers")
    sources = re.findall(r'\bname:\s*"([^"]+)"', text)
    missing_sources = [source for source in REQUIRED_SOURCES if source not in sources]
    if missing_sources:
        raise ValueError("missing required data sources: " + ",".join(missing_sources))
    events = sorted(set(re.findall(r'ftrace_events:\s*"([^"]+)"', text)))
    tracefs = Path("/sys/kernel/tracing")
    if not tracefs.is_dir():
        raise RuntimeError("BLOCKED_OS_TRACE: /sys/kernel/tracing is unavailable")
    availability = [ftrace_event_availability(tracefs, event) for event in events]
    missing_events = [item["event"] for item in availability if not item["available"]]
    if missing_events:
        raise RuntimeError("BLOCKED_OS_TRACE: unavailable ftrace events: " + ",".join(missing_events))
    return {"buffer_bytes": sum(sizes), "data_sources": sorted(set(sources)),
        "ftrace_events": availability, "tracefs": str(tracefs.resolve())}


def process_snapshot(names: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for entry in sorted(Path("/proc").iterdir(), key=lambda item: item.name):
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if comm in names:
            rows.append({"pid": int(entry.name), "comm": comm, "cmdline": cmdline})
    return rows


def cap_eff() -> str:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("CapEff:"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def fsync_file(path: Path) -> None:
    with path.open("rb") as source:
        os.fsync(source.fileno())


def exclusive_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("x", encoding="utf-8")


def terminate_owned(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--perfetto", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--command-json", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--perfetto-log", type=Path, required=True)
    parser.add_argument("--case-metadata-json", type=Path)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    if platform.system() != "Linux":
        raise RuntimeError("BLOCKED_OS_TRACE: capture host is not Linux")
    for path in (args.perfetto, args.trace_processor, args.config, args.command_json):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in (args.trace, args.metadata, args.stdout, args.stderr, args.perfetto_log):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)

    config = config_preflight(args.config)
    usage = shutil.disk_usage(args.trace.parent)
    required_free = min(MAX_TRACE_BYTES, config["buffer_bytes"] + 1024 * 1024 * 1024)
    if usage.free < required_free:
        raise RuntimeError("trace output filesystem lacks bounded free space")
    command, extra_env = read_spec(args.command_json)
    executable = Path(command[0])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError(executable)
    daemons = process_snapshot(("traced", "traced_probes"))
    if {item["comm"] for item in daemons} != {"traced", "traced_probes"}:
        raise RuntimeError("BLOCKED_OS_TRACE: traced and traced_probes must already be running")

    case_metadata = json.loads(args.case_metadata_json.read_text()) if args.case_metadata_json else {}
    started = datetime.now(timezone.utc)
    perfetto_command = [str(args.perfetto), "--txt", "--config", str(args.config), "--out", str(args.trace)]
    env = os.environ.copy()
    env.update(extra_env)
    env["LLAMA_PERFETTO_CAPTURE"] = "1"
    stop_read_fd, stop_write_fd = os.pipe()
    env["LLAMA_PERFETTO_STOP_FD"] = str(stop_write_fd)
    trace_process: subprocess.Popen[Any] | None = None
    workload_process: subprocess.Popen[Any] | None = None
    workload_returncode = -1
    trace_returncode = -1
    try:
        with exclusive_log(args.perfetto_log) as trace_log:
            trace_process = subprocess.Popen(perfetto_command, stdout=trace_log, stderr=subprocess.STDOUT,
                cwd=Path.cwd(), text=True)
            deadline = time.monotonic() + 0.25
            while time.monotonic() < deadline and trace_process.poll() is None and not args.trace.exists():
                time.sleep(0.05)
            if trace_process.poll() is not None:
                raise RuntimeError("Perfetto CLI exited before the workload started")
            with exclusive_log(args.stdout) as stdout, exclusive_log(args.stderr) as stderr:
                workload_process = subprocess.Popen(command, stdout=stdout, stderr=stderr, cwd=Path.cwd(), env=env,
                    text=True, pass_fds=(stop_write_fd,))
                os.close(stop_write_fd)
                stop_write_fd = -1
                deadline = time.monotonic() + args.timeout_seconds
                while True:
                    if workload_process.poll() is not None:
                        raise RuntimeError("workload exited before requesting a trace stop")
                    if trace_process.poll() is not None:
                        raise RuntimeError("Perfetto CLI exited before the workload requested a trace stop")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, args.timeout_seconds)
                    readable, _, _ = select.select([stop_read_fd], [], [], min(remaining, 0.25))
                    if readable:
                        marker = os.read(stop_read_fd, 1)
                        if marker != b"\x01":
                            raise RuntimeError("workload trace-stop handshake was incomplete")
                        break
                trace_process.send_signal(signal.SIGTERM)
                trace_returncode = trace_process.wait(timeout=60)
                workload_returncode = workload_process.wait(timeout=60)
                stdout.flush(); os.fsync(stdout.fileno())
                stderr.flush(); os.fsync(stderr.fileno())
            if workload_returncode != 0:
                raise RuntimeError(f"workload exited with {workload_returncode}")
            trace_log.flush(); os.fsync(trace_log.fileno())
            if trace_returncode != 0:
                raise RuntimeError(f"Perfetto CLI exited with {trace_returncode}")
    except BaseException:
        if workload_process is not None and workload_process.poll() is None:
            terminate_owned(workload_process)
        if trace_process is not None:
            terminate_owned(trace_process)
        raise
    finally:
        if stop_write_fd >= 0:
            os.close(stop_write_fd)
        os.close(stop_read_fd)

    if not args.trace.is_file() or args.trace.stat().st_size == 0:
        raise RuntimeError("TRACE_INVALID: Perfetto did not produce a trace")
    if args.trace.stat().st_size > MAX_TRACE_BYTES:
        raise RuntimeError("TRACE_INVALID: trace exceeds the 2 GiB hard maximum")
    fsync_file(args.trace)
    completed = datetime.now(timezone.utc)
    metadata = {
        "schema_version": "phase12-5-capture-v1",
        "status": "complete",
        "case": case_metadata,
        "host": {"hostname": platform.node(), "kernel": platform.release(), "platform": platform.platform(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(), "euid": os.geteuid(),
            "cap_eff": cap_eff(), "daemons": daemons},
        "tools": {"perfetto": file_identity(args.perfetto),
            "trace_processor": {**file_identity(args.trace_processor),
                "version": trace_processor_version(args.trace_processor)}},
        "config": {**file_identity(args.config), **config},
        "capture": {"started_utc": started.isoformat(), "completed_utc": completed.isoformat(),
            "perfetto_command": perfetto_command, "workload_command": command,
            "workload_environment_names": sorted(extra_env) + ["LLAMA_PERFETTO_CAPTURE", "LLAMA_PERFETTO_STOP_FD"],
            "workload_pid": workload_process.pid if workload_process else None,
            "workload_returncode": workload_returncode, "perfetto_pid": trace_process.pid if trace_process else None,
            "perfetto_returncode": trace_returncode, "output_free_bytes_before": usage.free},
        "files": {"trace": file_identity(args.trace), "stdout": file_identity(args.stdout),
            "stderr": file_identity(args.stderr), "perfetto_log": file_identity(args.perfetto_log),
            "command_json": file_identity(args.command_json)},
    }
    write_json(args.metadata, metadata)
    print(json.dumps({"status": "complete", "trace": str(args.trace), "size": args.trace.stat().st_size,
        "sha256": sha256(args.trace)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

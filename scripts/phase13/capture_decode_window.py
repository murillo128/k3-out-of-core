#!/usr/bin/env python3
"""Capture one bounded Phase 13 seeded decode window with Perfetto."""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.phase12_5.common import file_identity, sha256, trace_processor_version, write_json

MAX_TRACE_BYTES = 256 * 1024 * 1024
MAX_BUFFER_KIB = 128 * 1024


def read_command(path: Path) -> tuple[list[str], dict[str, str]]:
    value = json.loads(path.read_text())
    command = value.get("command")
    environment = value.get("environment", {})
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise ValueError("command JSON requires a non-empty string-array command")
    if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in environment.items()):
        raise ValueError("command JSON environment must be a string map")
    required = {
        "GGML_CUDA_GRAPH_OPT": "0",
        "GGML_CUDA_DISABLE_GRAPHS": "1",
        "LLAMA_PERFETTO_WINDOW_REQUEST": None,
        "LLAMA_PERFETTO_WINDOW_LAYER": None,
        "LLAMA_PERFETTO_WINDOW_MS": None,
        "LLAMA_PERFETTO_WINDOW_SEED": None,
    }
    for key, expected in required.items():
        if key not in environment or (expected is not None and environment[key] != expected):
            raise ValueError(f"missing or invalid required environment {key}")
    if environment["LLAMA_PERFETTO_WINDOW_MS"] not in {"1000", "500", "250"}:
        raise ValueError("decode window must be 1000, 500, or 250 ms")
    return command, environment


def terminate(process: subprocess.Popen[Any] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
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
    parser.add_argument("--command-json", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--workload-output", type=Path, required=True)
    parser.add_argument("--stdout", type=Path, required=True)
    parser.add_argument("--stderr", type=Path, required=True)
    parser.add_argument("--perfetto-log", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--case", choices=("A", "B"), required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    for path in (args.perfetto, args.trace_processor, args.config, args.command_json):
        if not path.is_file():
            raise FileNotFoundError(path)
    outputs = (args.trace, args.workload_output, args.stdout, args.stderr, args.perfetto_log, args.metadata)
    for path in outputs:
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
    config_text = args.config.read_text()
    if config_text.count('name: "track_event"') != 1 or "linux.ftrace" in config_text:
        raise ValueError("Phase 13 trace config must contain only the track_event data source")
    sizes = [int(line.split(":", 1)[1]) for line in config_text.splitlines() if "size_kb:" in line]
    if sizes != [MAX_BUFFER_KIB] or "fill_policy: DISCARD" not in config_text:
        raise ValueError("Phase 13 trace config must use one bounded 128 MiB DISCARD buffer")
    command, extra_environment = read_command(args.command_json)
    if Path(command[0]).resolve() == args.perfetto.resolve() or not Path(command[0]).is_file():
        raise ValueError("invalid workload executable")
    if "--output" not in command:
        raise ValueError("workload command must declare --output")
    output_index = command.index("--output") + 1
    if output_index >= len(command) or Path(command[output_index]) != args.workload_output:
        raise ValueError("workload output identity differs from command JSON")

    environment = os.environ.copy()
    environment.update(extra_environment)
    environment["LLAMA_PERFETTO_CAPTURE"] = "1"
    stop_read_fd, stop_write_fd = os.pipe()
    environment["LLAMA_PERFETTO_STOP_FD"] = str(stop_write_fd)
    trace_process: subprocess.Popen[Any] | None = None
    workload_process: subprocess.Popen[Any] | None = None
    started = datetime.now(timezone.utc)
    perfetto_command = [str(args.perfetto), "--txt", "--config", str(args.config), "--out", str(args.trace)]
    try:
        with args.perfetto_log.open("x") as perfetto_log:
            trace_process = subprocess.Popen(perfetto_command, stdout=perfetto_log, stderr=subprocess.STDOUT, text=True)
            time.sleep(0.25)
            if trace_process.poll() is not None:
                raise RuntimeError("Perfetto exited before workload launch")
            with args.stdout.open("x") as stdout, args.stderr.open("x") as stderr:
                workload_process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                    env=environment, pass_fds=(stop_write_fd,), text=True)
                os.close(stop_write_fd)
                stop_write_fd = -1
                deadline = time.monotonic() + args.timeout_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, args.timeout_seconds)
                    readable, _, _ = select.select([stop_read_fd], [], [], min(remaining, 0.25))
                    if readable:
                        if os.read(stop_read_fd, 1) != b"\x01":
                            raise RuntimeError("workload trace-stop handshake was incomplete")
                        break
                    if workload_process.poll() is not None:
                        raise RuntimeError(f"workload exited before trace stop ({workload_process.returncode})")
                    if trace_process.poll() is not None:
                        raise RuntimeError("Perfetto exited before trace stop")
                trace_process.send_signal(signal.SIGTERM)
                trace_returncode = trace_process.wait(timeout=60)
                workload_returncode = workload_process.wait(timeout=120)
                stdout.flush()
                os.fsync(stdout.fileno())
                stderr.flush()
                os.fsync(stderr.fileno())
            perfetto_log.flush()
            os.fsync(perfetto_log.fileno())
        if trace_returncode != 0 or workload_returncode != 0:
            raise RuntimeError(f"trace/workload exit codes: {trace_returncode}/{workload_returncode}")
    except BaseException:
        terminate(workload_process)
        terminate(trace_process)
        raise
    finally:
        if stop_write_fd >= 0:
            os.close(stop_write_fd)
        os.close(stop_read_fd)

    if not args.trace.is_file() or args.trace.stat().st_size == 0:
        raise RuntimeError("Perfetto produced no trace")
    if args.trace.stat().st_size > MAX_TRACE_BYTES:
        raise RuntimeError("trace exceeds the Phase 13 256 MiB bound")
    workload = json.loads(args.workload_output.read_text())
    trace_diagnostics = workload.get("perfetto", {})
    if workload.get("status") != "pass" or not trace_diagnostics.get("decode_window_complete"):
        raise RuntimeError("workload or decode-window closeout is incomplete")
    completed = datetime.now(timezone.utc)
    result = {
        "schema_version": "phase13-decode-window-capture-v1",
        "status": "complete",
        "case": args.case,
        "started_utc": started.isoformat(),
        "completed_utc": completed.isoformat(),
        "command": command,
        "environment": {**extra_environment, "LLAMA_PERFETTO_CAPTURE": "1"},
        "perfetto_command": perfetto_command,
        "trace": file_identity(args.trace),
        "workload": file_identity(args.workload_output),
        "command_spec": file_identity(args.command_json),
        "config": file_identity(args.config),
        "perfetto": file_identity(args.perfetto),
        "trace_processor": {**file_identity(args.trace_processor),
            "version": trace_processor_version(args.trace_processor)},
        "trace_diagnostics": trace_diagnostics,
    }
    write_json(args.metadata, result)
    print(json.dumps({"status": "complete", "case": args.case,
        "trace_bytes": args.trace.stat().st_size, "trace_sha256": sha256(args.trace)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

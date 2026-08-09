#!/usr/bin/env python3
"""Capture bounded Perfetto/CUPTI decode windows for issue 69 cells."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random
import subprocess
import sys

from common import (DEFAULT_COLD_BYTES, ROOT, cmake_build_identity, file_identity,
                    probe_command, validate_workload, write_json)


def run(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", default="S0,S1,A1")
    parser.add_argument("--perfetto", type=Path, default=Path("/usr/local/bin/perfetto"))
    parser.add_argument("--trace-processor", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--io-workers", type=int)
    parser.add_argument("--seed", type=int, default=69)
    parser.add_argument("--window-ms", type=int, choices=(1000, 500, 250), default=1000)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    for path in (args.probe, args.model, args.perfetto, args.trace_processor):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True)
    selection = random.Random(args.seed)
    request_ordinal = selection.randrange(8, 17)
    routed_layer = selection.randrange(43)
    cells: dict[str, object] = {}
    for cell in [item.strip() for item in args.cells.split(",") if item.strip()]:
        directory = args.output_dir / cell
        directory.mkdir()
        workload = directory / "workload.json"
        command = probe_command(
            args.probe, args.model, workload, cell, args.cold_bytes,
            "PRODUCTION_PERFORMANCE", False, args.io_workers,
        )
        environment = {
            "GGML_CUDA_GRAPH_OPT": "0", "GGML_CUDA_DISABLE_GRAPHS": "1",
            "LLAMA_PERFETTO_WINDOW_REQUEST": str(request_ordinal),
            "LLAMA_PERFETTO_WINDOW_LAYER": str(routed_layer),
            "LLAMA_PERFETTO_WINDOW_MS": str(args.window_ms),
            "LLAMA_PERFETTO_WINDOW_SEED": str(args.seed),
        }
        command_spec = directory / "command.json"
        write_json(command_spec, {"command": command, "environment": environment})
        trace = directory / "trace.pftrace"
        capture = directory / "capture.json"
        run([
            sys.executable, str(ROOT / "scripts/phase13/capture_decode_window.py"),
            "--perfetto", str(args.perfetto), "--trace-processor", str(args.trace_processor),
            "--config", str(ROOT / "scripts/phase13/configs/decode-window-128m.pbtxt"),
            "--command-json", str(command_spec), "--trace", str(trace),
            "--workload-output", str(workload), "--stdout", str(directory / "stdout.log"),
            "--stderr", str(directory / "stderr.log"), "--perfetto-log", str(directory / "perfetto.log"),
            "--metadata", str(capture), "--case", "A",
        ])
        verification = directory / "verification.json"
        run([
            sys.executable, str(ROOT / "scripts/phase13/verify_decode_window.py"),
            "--trace-processor", str(args.trace_processor), "--trace", str(trace),
            "--workload", str(workload), "--capture", str(capture),
            "--case", "A", "--output", str(verification),
        ])
        evidence = validate_workload(workload)
        cells[cell] = {
            "worker_count": evidence["async_io"]["diagnostics"]["worker_count"],
            "trace": file_identity(trace), "workload": file_identity(workload),
            "capture": file_identity(capture), "verification": file_identity(verification),
        }
    write_json(args.output_dir / "trace-matrix.json", {
        "schema_version": "issue69-trace-matrix-v1", "status": "valid",
        "build": cmake_build_identity(args.probe),
        "selection": {"seed": args.seed, "request_ordinal": request_ordinal,
            "routed_layer": routed_layer, "window_ms": args.window_ms},
        "cells": cells,
    })


if __name__ == "__main__":
    main()

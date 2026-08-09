#!/usr/bin/env python3
"""Capture bounded Perfetto scheduler evidence for issue 69 selected cells."""

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
from run_matrix import block_delta, block_status, cgroup_status, drop_page_cache


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", default="S0,A1")
    parser.add_argument("--perfetto", type=Path, default=Path("/usr/local/bin/perfetto"))
    parser.add_argument("--trace-processor", type=Path,
                        default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--config", type=Path, default=(
        ROOT / "scripts/issue69/configs/decode-window-scheduler-128m.pbtxt"
    ))
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--io-workers", type=int)
    parser.add_argument("--async-cold-fill", action="store_true")
    parser.add_argument("--transport", choices=(
        "POSITIONAL", "BUFFERED", "DIRECT_IO", "DIRECT_IO_POSITIONAL"),
        default="POSITIONAL")
    parser.add_argument("--io-access", choices=("NORMAL", "RANDOM"), default="NORMAL")
    parser.add_argument("--seed", type=int, default=69)
    parser.add_argument("--window-ms", type=int, choices=(1000, 500, 250), default=1000)
    parser.add_argument("--drop-page-cache", action="store_true")
    parser.add_argument("--block-stat", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    for path in (args.probe, args.model, args.perfetto, args.trace_processor, args.config):
        if not path.is_file():
            raise FileNotFoundError(path)
    for path in args.block_stat:
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True)
    selection = random.Random(args.seed)
    request_ordinal = selection.randrange(8, 17)
    routed_layer = selection.randrange(43)
    cells: dict[str, object] = {}
    capture_script = ROOT / "scripts/phase12_5/capture_perfetto.py"
    for cell in [item.strip() for item in args.cells.split(",") if item.strip()]:
        directory = args.output_dir / cell
        directory.mkdir()
        workload = directory / "workload.json"
        command = probe_command(
            args.probe, args.model, workload, cell, args.cold_bytes,
            "PRODUCTION_PERFORMANCE", False, args.io_workers, args.async_cold_fill,
            args.transport, args.io_access,
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
        if args.drop_page_cache:
            drop_page_cache()
        block_before = {str(path): block_status(path) for path in args.block_stat}
        trace = directory / "scheduler.pftrace"
        metadata = directory / "capture.json"
        completed = subprocess.run([
            sys.executable, str(capture_script),
            "--perfetto", str(args.perfetto),
            "--trace-processor", str(args.trace_processor),
            "--config", str(args.config),
            "--trace", str(trace),
            "--command-json", str(command_spec),
            "--metadata", str(metadata),
            "--stdout", str(directory / "stdout.log"),
            "--stderr", str(directory / "stderr.log"),
            "--perfetto-log", str(directory / "perfetto.log"),
        ], cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"scheduler trace {cell} failed with {completed.returncode}")
        block_after = {str(path): block_status(path) for path in args.block_stat}
        evidence = validate_workload(workload)
        cells[cell] = {
            "worker_count": evidence["async_io"]["diagnostics"]["worker_count"],
            "cache_state": (
                "OS_COLD_REQUESTED_AND_DROPPED" if args.drop_page_cache else "UNCHANGED"
            ),
            "block_devices": {
                "before": block_before, "after": block_after,
                "delta": {
                    path: block_delta(block_before[path], block_after[path])
                    for path in block_before
                },
            },
            "cgroup": cgroup_status(os.getpid()),
            "trace": file_identity(trace), "workload": file_identity(workload),
            "capture": file_identity(metadata),
        }
    write_json(args.output_dir / "scheduler-trace-matrix.json", {
        "schema_version": "issue69-scheduler-trace-matrix-v1", "status": "valid",
        "build": cmake_build_identity(args.probe),
        "selection": {
            "seed": args.seed, "request_ordinal": request_ordinal,
            "routed_layer": routed_layer, "window_ms": args.window_ms,
        },
        "config": file_identity(args.config),
        "drop_page_cache": args.drop_page_cache,
        "async_cold_fill": args.async_cold_fill,
        "transport": args.transport, "io_access": args.io_access,
        "block_stat": [str(path) for path in args.block_stat],
        "cells": cells,
    })


if __name__ == "__main__":
    main()

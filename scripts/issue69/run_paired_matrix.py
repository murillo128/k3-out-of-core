#!/usr/bin/env python3
"""Run fresh, alternating issue 69 baseline/final throughput pairs."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from common import DEFAULT_COLD_BYTES, cmake_build_identity, write_json
from run_matrix import run_one


def run_args(args: argparse.Namespace, candidate: str) -> SimpleNamespace:
    return SimpleNamespace(
        probe=args.baseline_probe if candidate == "baseline" else args.final_probe,
        model=args.model,
        output_dir=args.output_dir / candidate,
        cold_bytes=args.cold_bytes,
        runtime_mode="PRODUCTION_PERFORMANCE",
        prewarm_cold_all=False,
        io_workers=args.io_workers,
        sample_period=args.sample_period,
        resume=False,
    )


def manifest(args: argparse.Namespace, status: str, completed: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "issue69-paired-matrix-v1",
        "status": status,
        "cells": args.cells,
        "pairs": args.pairs,
        "cold_bytes": args.cold_bytes,
        "io_workers": args.io_workers,
        "runtime_mode": "PRODUCTION_PERFORMANCE",
        "order_rule": "odd pairs baseline-final; even pairs final-baseline; order applied per cell",
        "revisions": {
            "baseline": args.baseline_revision,
            "final": args.final_revision,
        },
        "builds": {
            "baseline": cmake_build_identity(args.baseline_probe),
            "final": cmake_build_identity(args.final_probe),
        },
        "completed_runs": completed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-probe", type=Path, required=True)
    parser.add_argument("--final-probe", type=Path, required=True)
    parser.add_argument("--baseline-revision", required=True)
    parser.add_argument("--final-revision", required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", default="S0,S1,A1")
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--cold-bytes", type=int, default=DEFAULT_COLD_BYTES)
    parser.add_argument("--io-workers", type=int, default=2)
    parser.add_argument("--sample-period", type=float, default=0.5)
    args = parser.parse_args()
    args.cells = [item.strip() for item in args.cells.split(",") if item.strip()]
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    if (args.pairs < 1 or args.cold_bytes <= 0 or args.io_workers < 1 or
            args.sample_period <= 0):
        raise SystemExit("invalid paired-run bounds")
    for path in (args.baseline_probe, args.final_probe, args.model):
        if not path.is_file():
            raise FileNotFoundError(path)

    args.output_dir.mkdir(parents=True)
    completed: list[dict[str, object]] = []
    write_json(args.output_dir / "paired-matrix.json", manifest(args, "in_progress", completed))
    for pair in range(1, args.pairs + 1):
        candidates = ("baseline", "final") if pair % 2 else ("final", "baseline")
        for cell in args.cells:
            for candidate in candidates:
                run_one(run_args(args, candidate), cell, pair)
                completed.append({"pair": pair, "cell": cell, "candidate": candidate})
                write_json(
                    args.output_dir / "paired-matrix.json",
                    manifest(args, "in_progress", completed),
                )
    write_json(args.output_dir / "paired-matrix.json", manifest(args, "complete", completed))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run the bounded, whole-cgroup Delta D2c confirmation matrix."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from common import file_identity, write_json


POLICIES = {
    "buffered": {
        "cells": ("S0", "S1", "A1"),
        "workers": 4,
        "transport": "POSITIONAL",
        "async_fill": False,
    },
    "direct": {
        "cells": ("S0", "A1"),
        "workers": 8,
        "transport": "DIRECT_IO_POSITIONAL",
        "async_fill": False,
    },
    "direct-fill": {
        "cells": ("S0", "S1", "A1"),
        "workers": 8,
        "transport": "DIRECT_IO_POSITIONAL",
        "async_fill": True,
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--memory-gib", type=int, default=64)
    parser.add_argument("--cold-bytes", type=int, default=16 * 1024**3)
    parser.add_argument("--block-stat", type=Path, default=Path("/sys/block/vda/stat"))
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.pairs < 1 or args.memory_gib < 1 or args.cold_bytes < 1:
        raise SystemExit("invalid confirmation bounds")
    for path in (args.probe, args.model, args.block_stat):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.output_dir.exists() and not args.resume:
        raise FileExistsError(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    run_matrix = Path(__file__).with_name("run_matrix.py").resolve()
    records: list[dict[str, object]] = []
    for pair in range(1, args.pairs + 1):
        for cell in ("S0", "S1", "A1"):
            for policy_name, policy in POLICIES.items():
                if cell not in policy["cells"]:
                    continue
                result = args.output_dir / policy_name / "raw" / f"{cell}-{pair:02d}.json.gz"
                if result.is_file() and args.resume:
                    records.append({
                        "pair": pair, "cell": cell, "policy": policy_name,
                        "status": "reused", "result": file_identity(result),
                    })
                    continue
                unit = f"issue69-d2c-confirm-{args.memory_gib}g-p{pair}-{cell.lower()}-{policy_name}"
                command = [
                    "systemd-run", f"--unit={unit}", "--wait", "--collect", "--pipe",
                    "--service-type=exec", "-p", f"MemoryMax={args.memory_gib}G",
                    "-p", "MemorySwapMax=0", "-p", "OOMPolicy=stop",
                    "-p", "TimeoutStartSec=15min", "/usr/bin/python3", str(run_matrix),
                    "--probe", str(args.probe.resolve()), "--model", str(args.model.resolve()),
                    "--output-dir", str((args.output_dir / policy_name).resolve()),
                    "--cells", cell, "--pairs", "1", "--start-pair", str(pair),
                    "--cold-bytes", str(args.cold_bytes), "--io-workers", str(policy["workers"]),
                    "--transport", str(policy["transport"]), "--io-access", "NORMAL",
                    "--drop-page-cache", "--block-stat", str(args.block_stat),
                ]
                if policy["async_fill"]:
                    command.append("--async-cold-fill")
                print(f"start pair={pair} cell={cell} policy={policy_name}", flush=True)
                completed = subprocess.run(command, check=False)
                record = {
                    "pair": pair, "cell": cell, "policy": policy_name,
                    "command": command, "returncode": completed.returncode,
                }
                if result.is_file():
                    record["result"] = file_identity(result)
                records.append(record)
                write_json(args.output_dir / "confirmation.json", {
                    "schema_version": "issue69-delta-d2c-confirmation-v1",
                    "status": "running" if completed.returncode == 0 else "failed",
                    "memory_max_bytes": args.memory_gib * 1024**3,
                    "memory_swap_max_bytes": 0,
                    "cold_bytes": args.cold_bytes,
                    "pairs": args.pairs,
                    "policies": POLICIES,
                    "records": records,
                })
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"confirmation failed: pair={pair} cell={cell} policy={policy_name}"
                    )
    value = json.loads((args.output_dir / "confirmation.json").read_text())
    value["status"] = "complete"
    write_json(args.output_dir / "confirmation.json", value)


if __name__ == "__main__":
    main()

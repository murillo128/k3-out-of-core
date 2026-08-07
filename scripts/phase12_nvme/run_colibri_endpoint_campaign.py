#!/usr/bin/env python3
"""Run or safely resume the predeclared untraced Colibrì endpoint campaign."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


CASES = (
    ("pair-01-8g", "8GiB", 8.0, 5),
    ("pair-01-max-safe", "MAX_SAFE", 112.761, 75),
    ("anchor-96g", "96GiB", 96.0, 63),
    ("pair-02-8g", "8GiB", 8.0, 5),
    ("pair-02-max-safe", "MAX_SAFE", 112.761, 75),
    ("pair-03-8g", "8GiB", 8.0, 5),
    ("pair-03-max-safe", "MAX_SAFE", 112.761, 75),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--snapshot-verification", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--block-stat", type=Path, action="append", required=True)
    parser.add_argument("--runner", type=Path, default=Path(__file__).with_name("run_colibri_endpoint.py"))
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    completed = []
    for run_id, label, requested_gib, slots in CASES:
        output = args.output_root / run_id
        summary = output / "run.json"
        if summary.is_file():
            existing = json.loads(summary.read_text())
            if existing.get("status") != "PASS" or existing.get("run_id") != run_id:
                raise RuntimeError(f"refusing non-PASS or mismatched existing run {run_id}")
            print(json.dumps({"run_id": run_id, "status": "RESUME_EXISTING_PASS"}), flush=True)
            completed.append(run_id)
            continue
        command = [
            sys.executable, str(args.runner), "--binary", str(args.binary), "--model-dir", str(args.model_dir),
            "--build-manifest", str(args.build_manifest), "--snapshot-verification", str(args.snapshot_verification),
            "--output-dir", str(output), "--run-id", run_id, "--capacity-label", label,
            "--requested-capacity-gib", str(requested_gib), "--slots-per-layer", str(slots),
            "--ngen", "256", "--minimum-complete-forwards", "256", "--timeout-seconds", "7200", "--drop-caches",
        ]
        for block in args.block_stat:
            command.extend(("--block-stat", str(block)))
        print(json.dumps({"run_id": run_id, "status": "START", "command": command}), flush=True)
        result = subprocess.run(command)
        if result.returncode != 0:
            raise RuntimeError(f"campaign run {run_id} failed with {result.returncode}")
        completed.append(run_id)
        print(json.dumps({"run_id": run_id, "status": "PASS"}), flush=True)
    print(json.dumps({"status": "PASS", "completed": completed}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

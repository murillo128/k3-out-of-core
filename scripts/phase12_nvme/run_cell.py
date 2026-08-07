#!/usr/bin/env python3
"""Run one fresh-process Phase 12-NVMe project storage cell."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--api", choices=("buffered-pread", "direct-pread", "buffered-io-uring", "direct-io-uring", "mmap-buffered"), required=True)
    parser.add_argument("--cache-state", choices=("OS_COLD_VERIFIED", "OS_WARM"), required=True)
    parser.add_argument("--qd", type=int, choices=(1, 2, 4, 8, 16, 32), required=True)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--layout", choices=("A", "B"), required=True)
    parser.add_argument("--request", choices=("COLD_SPREAD", "LOGICAL_SHUFFLE", "HALF_HOT"), required=True)
    parser.add_argument("--token", type=int, choices=range(32), required=True)
    parser.add_argument("--order", choices=("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    native_output = args.output.with_suffix(args.output.suffix + ".native.tmp")
    command = [
        str(args.binary.resolve()), "--plan", str(args.plan.resolve()), "--api", args.api,
        "--cache-state", args.cache_state, "--qd", str(args.qd),
        "--iterations", str(args.iterations), "--output", str(native_output),
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"native cell failed ({completed.returncode}): {completed.stderr.strip()}")
    result = json.loads(native_output.read_text())
    native_output.unlink()
    result.update({
        "layout": args.layout,
        "request_class": args.request,
        "route_token": args.token,
        "submission_order": args.order,
        "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
        "command": command[:-1] + [str(args.output)],
    })
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "api", "layout", "request_class", "submission_order", "requested_qd", "useful_gbps")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

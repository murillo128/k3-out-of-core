#!/usr/bin/env python3
"""Run one reversible host read-ahead comparator for issue 69 Delta D2c."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import time

from common import file_identity, write_json


def read_value(path: Path) -> int:
    return int(path.read_text().strip())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--read-ahead-kb", type=Path,
                        default=Path("/sys/block/vda/queue/read_ahead_kb"))
    parser.add_argument("--block-stat", type=Path, default=Path("/sys/block/vda/stat"))
    parser.add_argument("--memory-gib", type=int, default=64)
    parser.add_argument("--io-workers", type=int, default=4)
    parser.add_argument("--value", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args()
    for path in (args.probe, args.model, args.read_ahead_kb, args.block_stat):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    if args.memory_gib < 1 or args.io_workers < 1 or args.value < 0 or args.timeout_seconds < 1:
        raise ValueError("invalid bounded read-ahead screen arguments")
    before = read_value(args.read_ahead_kb)
    during: int | None = None
    after: int | None = None
    returncode = -1
    timed_out = False
    run_matrix = Path(__file__).with_name("run_matrix.py").resolve()
    command = [
        "systemd-run", "--unit=issue69-d2c-read-ahead-screen", "--wait", "--collect",
        "--pipe", "--service-type=exec", "-p", f"MemoryMax={args.memory_gib}G",
        "-p", "MemorySwapMax=0", "-p", "OOMPolicy=stop", "-p", "TimeoutStartSec=15min",
        "/usr/bin/python3", str(run_matrix), "--probe", str(args.probe.resolve()),
        "--model", str(args.model.resolve()), "--output-dir", str(args.output_dir.resolve()),
        "--cells", "S0", "--pairs", "1", "--cold-bytes", str(16 * 1024**3),
        "--io-workers", str(args.io_workers), "--transport", "POSITIONAL",
        "--io-access", "NORMAL", "--drop-page-cache", "--block-stat", str(args.block_stat),
    ]
    started = time.monotonic()
    try:
        args.read_ahead_kb.write_text(f"{args.value}\n")
        during = read_value(args.read_ahead_kb)
        if during != args.value:
            raise RuntimeError("host read-ahead value did not change exactly")
        try:
            returncode = subprocess.run(
                command, check=False, timeout=args.timeout_seconds
            ).returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run(
                ["systemctl", "stop", "issue69-d2c-read-ahead-screen.service"],
                check=False,
            )
    finally:
        args.read_ahead_kb.write_text(f"{before}\n")
        after = read_value(args.read_ahead_kb)
        if after != before:
            raise RuntimeError("host read-ahead value was not restored")
    result = args.output_dir / "raw/S0-01.json.gz"
    log = args.output_dir / "raw/S0-01.log"
    status = "pass" if returncode == 0 and result.is_file() else "rejected_early"
    write_json(args.output_dir / "read-ahead-screen.json", {
        "schema_version": "issue69-read-ahead-screen-v1", "status": status,
        "sysfs": str(args.read_ahead_kb), "before_kib": before,
        "requested_kib": args.value, "observed_during_kib": during,
        "restored_kib": after, "command": command, "returncode": returncode,
        "elapsed_seconds": time.monotonic() - started, "timed_out": timed_out,
        "completed_workload": result.is_file(),
        "result": file_identity(result) if result.is_file() else None,
        "log": file_identity(log) if log.is_file() else None,
    })


if __name__ == "__main__":
    main()

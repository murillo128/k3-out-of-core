#!/usr/bin/env python3
"""Preserve a stopped qualification attempt as negative technical evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--finding",
        default="io_uring completions were checksummed serially on the ring thread while pread cells checksummed in parallel workers, so QD>1 API throughput was not a fair transport comparison.",
    )
    parser.add_argument(
        "--next",
        default="parallelize completion checksumming behind bounded slot ownership, preserving canonical reduction order, then restart qualification from an empty raw-attempt directory.",
    )
    args = parser.parse_args()
    files = sorted(args.raw.glob("*.json"))
    cases = [json.loads(path.read_text()) | {"file": path.name, "sha256": sha256_file(path)} for path in files]
    rings = [case for case in cases if case.get("api") == "buffered-io-uring"]
    document = {
        "schema_version": "phase12-nvme-harness-qualification-failure-v1",
        "status": "REJECTED_HARNESS_ATTEMPT",
        "completed_cases": len(cases),
        "correctness": {
            "all_passed": all(case.get("status") == "PASS" for case in cases),
            "all_short_reads_zero": all(case.get("short_reads") == 0 for case in cases),
            "checksum_sinks": sorted({case.get("checksum_sink_sha256") for case in cases}),
        },
        "finding": args.finding,
        "observed_buffered_io_uring": [{
            key: case[key]
            for key in ("api", "cache_state", "requested_qd", "useful_gbps", "effective_qd_status", "short_reads", "checksum_sink_sha256")
        } for case in rings],
        "disposition": "rejected",
        "next": args.next,
        "raw_files": [{"path": case["file"], "sha256": case["sha256"]} for case in cases],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "completed_cases": len(cases), "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

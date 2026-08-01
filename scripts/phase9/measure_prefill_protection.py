#!/usr/bin/env python3
"""Capture the fixed decode-warmup/prefill/decode-resume protection sequence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity  # noqa: E402


def run(probe: Path, directory: Path, policy: str, repetition: int) -> dict[str, Any]:
    output = directory / f"{policy.lower()}-{repetition}.json"
    command = [str(probe), "--output", str(output), "--budget-bytes", "1572864",
               "--projection-bytes", "65536", "--layers", "4", "--experts-per-layer", "8",
               "--experts-used", "2", "--touch-slots", "16", "--classification-samples", "0",
               "--prefill-protection", "1", "--policy", policy]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{policy} prefill protection failed: {completed.stderr[-4000:]}")
    value = json.loads(output.read_text())
    return {"policy": policy, "repetition": repetition, "command": command,
            "artifact": file_identity(output), "result": value["prefill_protection"],
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    policies = ("LRU", "LFRU", "SLRU")
    runs = [run(args.probe, args.output_dir, policy, repetition)
            for policy in policies for repetition in range(args.repetitions)]
    by_policy = {policy: [entry for entry in runs if entry["policy"] == policy] for policy in policies}
    for policy, entries in by_policy.items():
        identities = [{key: entry["result"][key] for key in (
            "protected_survivors_after_prefill", "protected_forced_victims", "resume_misses",
            "resume_evictions", "first_eight_disk_bytes", "first_eight_h2d_bytes")}
            for entry in entries]
        if any(value != identities[0] for value in identities[1:]):
            raise RuntimeError(f"{policy} protection counters are not repeatable")
    output = {
        "schema_version": "phase9-prefill-protection-v1", "status": "pass",
        "input": {"probe": file_identity(args.probe)},
        "sequence": ["decode warmup", "large prefill burst", "repeat identical decode routes for eight tokens"],
        "runs": runs,
        "comparison": {
            "lru": by_policy["LRU"][0]["result"], "lfru": by_policy["LFRU"][0]["result"],
            "slru": by_policy["SLRU"][0]["result"],
            "disposition": "SLRU preserves the four-key protected decode set; LRU is retained as compatibility control",
        },
        "scope": "controlled exact-layout cold-cache mechanism sequence; no model-quality claim",
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "summary": str(args.summary)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

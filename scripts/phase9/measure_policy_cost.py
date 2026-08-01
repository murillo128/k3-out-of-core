#!/usr/bin/env python3
"""Capture native policy CPU cost with an exact executable identity."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    native = args.output.with_suffix(".native.json")
    command = [str(args.replay), "--benchmark-output", str(native)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"policy benchmark failed: {completed.stderr[-4000:]}")
    result = json.loads(native.read_text())
    if result.get("status") != "pass": raise RuntimeError("native policy benchmark did not pass")
    result.update({"inputs": {"replay": file_identity(args.replay), "native_output": file_identity(native)},
                   "command": command, "exit_code": completed.returncode})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result))
    print(canonical_json({"status": "pass", "output": str(args.output)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

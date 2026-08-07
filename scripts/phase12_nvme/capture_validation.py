#!/usr/bin/env python3
"""Run and capture the focused Checkpoint A validation commands."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(arguments: list[str]) -> dict[str, object]:
    completed = subprocess.run(arguments, cwd=ROOT, text=True, capture_output=True)
    return {
        "command": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--build", type=Path, default=ROOT / "build/phase12-nvme")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build = args.build.resolve()
    commands = [
        ["cmake", "-S", "scripts/phase12_nvme", "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"],
        ["cmake", "--build", str(build), "--parallel", "16"],
        ["ctest", "--test-dir", str(build), "--output-on-failure"],
        ["python3", "-m", "unittest", "discover", "-s", "tests/phase12p", "-p", "test_*.py"],
        ["python3", "-m", "unittest", "discover", "-s", "tests/phase12_nvme", "-p", "test_*.py"],
    ]
    results = [run(command) for command in commands]
    binary = build / "phase12_nvme_bench"
    document = {
        "schema_version": "phase12-nvme-validation-v1",
        "status": "PASS" if all(result["returncode"] == 0 for result in results) else "FAIL",
        "commands": results,
        "binary": {
            "path": str(binary),
            "sha256": sha256_file(binary) if binary.is_file() else None,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

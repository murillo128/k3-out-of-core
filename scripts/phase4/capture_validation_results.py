#!/usr/bin/env python3
"""Capture exact issue #17 closeout commands and reproducible result identities."""

from __future__ import annotations

import argparse
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from common import git, json_write, run


COMMANDS = [
    ("build-cpu", ["cmake", "--build", "llama.cpp/build-cpu", "--target", "test-expert-weight-provider", "test-hot-expert-cache", "phase4-hot-cache-probe", "-j4"]),
    ("build-cuda", ["cmake", "--build", "llama.cpp/build-cuda", "--target", "llama", "test-expert-weight-provider", "test-hot-expert-cache", "phase4-hot-cache-probe", "-j4"]),
    ("ctest-cpu", ["ctest", "--test-dir", "llama.cpp/build-cpu", "--output-on-failure", "-R", "expert-weight-provider|hot-expert-cache"]),
    ("ctest-cuda", ["ctest", "--test-dir", "llama.cpp/build-cuda", "--output-on-failure", "-R", "expert-weight-provider|hot-expert-cache"]),
    ("ctest-asan-ubsan", ["ctest", "--test-dir", "llama.cpp/build-asan", "--output-on-failure", "-R", "expert-weight-provider|hot-expert-cache"]),
    ("unittest-phase3", ["python3", "-m", "unittest", "discover", "-s", "tests/phase3", "-p", "test_*.py", "-v"]),
    ("unittest-phase4", ["python3", "-m", "unittest", "discover", "-s", "tests/phase4", "-p", "test_*.py", "-v"]),
    ("diff-check-project", ["git", "diff", "--check", "0da90c6711e00613820183c1811dcaf1baffb409..HEAD"]),
    ("diff-check-nested", ["git", "-C", "llama.cpp", "diff", "--check", "a120de8e2d0b552c51eacd7d701ef1dd994bc3db..HEAD"]),
    ("status-project", ["git", "status", "--short"]),
    ("status-nested", ["git", "-C", "llama.cpp", "status", "--short"]),
]


def counts(name: str, output: str) -> tuple[int | None, int | None]:
    if name.startswith("ctest-"):
        match = re.search(r"(\d+) tests passed, (\d+) tests failed", output)
        if not match: return None, None
        passed, failed = map(int, match.groups())
        return passed, passed + failed
    if name.startswith("unittest-"):
        match = re.search(r"Ran (\d+) tests?", output)
        return (int(match.group(1)), int(match.group(1))) if match else (None, None)
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    records = []
    for name, command in COMMANDS:
        completed = run(command, root, check=False)
        passed, total = counts(name, completed.stdout + completed.stderr)
        record = {
            "name": name, "command": command, "cwd": ".", "exit_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "stdout_bytes": len(completed.stdout.encode()), "stderr_bytes": len(completed.stderr.encode()),
            "passed": passed, "total": total,
        }
        records.append(record)
        if completed.returncode != 0 or (total is not None and passed != total):
            raise RuntimeError(f"validation failed: {name}\n{completed.stdout}\n{completed.stderr}")
        if name.startswith("status-") and completed.stdout:
            raise RuntimeError(f"validation requires a clean tree: {name}\n{completed.stdout}")
    report = {
        "schema_version": "phase4-validation-results-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "project_head": git(root, "rev-parse", "HEAD"),
        "llama_cpp_head": git(root / "llama.cpp", "rev-parse", "HEAD"),
        "commands": records,
    }
    json_write(args.output.resolve(), report)
    print(f"PASS: wrote {args.output} with {len(records)} exact command results")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

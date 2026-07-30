#!/usr/bin/env python3
"""Capture exact issue #20 closeout commands and output identities."""

from __future__ import annotations
import argparse, hashlib, re
from datetime import datetime, timezone
from pathlib import Path
from common import git, json_write, run

COMMANDS = [
    ("build-cpu", ["cmake", "--build", "llama.cpp/build-cpu", "--target", "test-expert-weight-provider", "test-hot-expert-cache", "test-cold-expert-cache", "test-expert-transfer-ring", "-j4"]),
    ("build-cuda", ["cmake", "--build", "llama.cpp/build-cuda", "--target", "llama", "test-expert-weight-provider", "test-hot-expert-cache", "test-cold-expert-cache", "test-expert-transfer-ring", "phase5-cold-cache-probe", "-j4"]),
    ("ctest-cpu", ["ctest", "--test-dir", "llama.cpp/build-cpu", "--output-on-failure", "-R", "expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring"]),
    ("ctest-cuda", ["ctest", "--test-dir", "llama.cpp/build-cuda", "--output-on-failure", "-R", "expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring"]),
    ("unittest-phase4", ["python3", "-m", "unittest", "discover", "-s", "tests/phase4", "-p", "test_*.py", "-v"]),
    ("unittest-phase5", ["python3", "-m", "unittest", "discover", "-s", "tests/phase5", "-p", "test_*.py", "-v"]),
    ("diff-check-nested", ["git", "-C", "llama.cpp", "diff", "--check", "57fe1eabbe3d0ced59096a0744efc91e286fb1c7..HEAD"]),
    ("diff-check-project", ["git", "diff", "--check", "114f0de6f5d1cbd5f9ef6255f9100f3f4d52380a..HEAD"]),
    ("status-project", ["git", "status", "--short"]),
    ("status-nested", ["git", "-C", "llama.cpp", "status", "--short"]),
]

def counts(name, output):
    if name.startswith("ctest-"):
        match = re.search(r"\d+% tests passed, (\d+) tests failed out of (\d+)", output)
        if not match: return None, None
        failed, total = map(int, match.groups()); return total - failed, total
    if name.startswith("unittest-"):
        match = re.search(r"Ran (\d+) tests?", output)
        return (int(match.group(1)), int(match.group(1))) if match else (None, None)
    return None, None

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); root = args.project_root.resolve(); records = []
    for name, command in COMMANDS:
        result = run(command, root, check=False); passed, total = counts(name, result.stdout + result.stderr)
        records.append({"name": name, "command": command, "cwd": ".", "exit_code": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(), "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
            "stdout_bytes": len(result.stdout.encode()), "stderr_bytes": len(result.stderr.encode()), "passed": passed, "total": total})
        if result.returncode != 0 or (total is not None and passed != total): raise RuntimeError(f"validation failed: {name}\n{result.stdout}\n{result.stderr}")
        if name.startswith("status-") and result.stdout: raise RuntimeError(f"validation requires clean tree: {name}\n{result.stdout}")
    json_write(args.output.resolve(), {"schema_version": "phase5-validation-results-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(), "project_head": git(root, "rev-parse", "HEAD"),
        "llama_cpp_head": git(root / "llama.cpp", "rev-parse", "HEAD"), "commands": records})
    print(f"PASS: wrote {args.output} with {len(records)} commands"); return 0

if __name__ == "__main__": raise SystemExit(main())

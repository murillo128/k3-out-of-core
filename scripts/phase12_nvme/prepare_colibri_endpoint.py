#!/usr/bin/env python3
"""Build the pinned Colibrì Kimi-K3 engine with default-off evidence telemetry."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any


COLIBRI_COMMIT = "b085b48888a88d9a1c00b151a9979774b72cdbfd"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256_file(path)}


def run(command: list[str], cwd: Path) -> str:
    return subprocess.check_output(command, cwd=cwd, text=True, stderr=subprocess.STDOUT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.source.resolve()
    worktree = args.worktree.resolve()
    patch = args.patch.resolve()
    if run(["git", "rev-parse", "HEAD"], source).strip() != COLIBRI_COMMIT:
        raise ValueError("Colibrì source revision mismatch")
    if run(["git", "status", "--porcelain", "--untracked-files=all"], source).strip():
        raise ValueError("accepted Colibrì source worktree is dirty")
    if not worktree.exists():
        worktree.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "worktree", "add", "--detach", str(worktree), COLIBRI_COMMIT], source)
        run(["git", "apply", "--unidiff-zero", "--check", str(patch)], worktree)
        run(["git", "apply", "--unidiff-zero", str(patch)], worktree)
    if run(["git", "rev-parse", "HEAD"], worktree).strip() != COLIBRI_COMMIT:
        raise ValueError("telemetry worktree revision mismatch")
    run(["git", "diff", "--check"], worktree)
    run(["git", "apply", "--unidiff-zero", "--check", "--reverse", str(patch)], worktree)
    actual_diff = subprocess.check_output(["git", "diff", "--unified=0", "--", "c/kimi_k3.c"], cwd=worktree)
    if hashlib.sha256(actual_diff).hexdigest() != sha256_file(patch):
        raise ValueError("telemetry worktree diff does not equal the committed patch")

    build = subprocess.run(
        ["make", "-C", str(worktree / "c"), "-B", "kimi_k3", "ARCH=native"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    binary = worktree / "c/kimi_k3"
    original = source / "c/kimi_k3"
    document = {
        "schema_version": "phase12-nvme-colibri-endpoint-telemetry-build-v1",
        "status": "PASS",
        "base_commit": COLIBRI_COMMIT,
        "accepted_source_clean": True,
        "instrumentation": {
            "scope": "default-off evidence-only endpoint counters, token identities, timing scopes, and optional trace_marker ranges",
            "behavior_boundary": "no routing, cache admission, arithmetic, model, I/O policy, or production default change",
            "patch": identity(patch),
        },
        "commands": {
            "build": ["make", "-C", str(worktree / "c"), "-B", "kimi_k3", "ARCH=native"],
        },
        "build_output": build.stdout.splitlines(),
        "original_binary": identity(original),
        "instrumented_binary": identity(binary),
        "worktree": str(worktree),
        "compiler": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
        "environment": {"PATH": os.environ.get("PATH", "")},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "binary_sha256": document["instrumented_binary"]["sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

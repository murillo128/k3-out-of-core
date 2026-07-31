#!/usr/bin/env python3
"""Shared, bounded Phase 8 evidence helpers."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

PROJECT_BASE = "5fe0bda6965da7d2b0f85dd14b97427a7b60f161"
PHASE8_START = "30013880641fd2f10a1952b5b9619e6d872e233b"
LLAMA_BASE = "b71e40f91b1a0dab578d56ac733211453704d674"
LLAMA_CHECKPOINT_B = "a885ff7750a4e73901b7f378e7dc45880a7d1536"
CHECKPOINT_A_COMMENT = 5141694340
CHECKPOINT_B_COMMENT = 5144721775
PHASE7_FINAL_REVIEW_COMMENT = 5140690363
PHASE7_MANIFEST = "results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json"
RESULTS = "results/2026-07-31/skynet/phase8-miss-execution"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(root: Path, path: Path, *, external: bool = False) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved if external else resolved.relative_to(root.resolve())),
        "size": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_command(
    command: list[str], cwd: Path, environment: dict[str, str] | None = None,
    *, timeout: int | None = None,
) -> tuple[dict[str, Any], str, str]:
    started = time.monotonic_ns()
    env = os.environ.copy()
    if environment:
        env.update(environment)
    completed = subprocess.run(
        command, cwd=cwd, text=True, capture_output=True, env=env, timeout=timeout)
    record: dict[str, Any] = {
        "command": command,
        "cwd": str(cwd.resolve()),
        "duration_ms": (time.monotonic_ns() - started) // 1_000_000,
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout.encode()),
        "stderr_sha256": sha256_bytes(completed.stderr.encode()),
        "stdout_tail": completed.stdout.splitlines()[-16:],
        "stderr_tail": completed.stderr.splitlines()[-16:],
    }
    if environment:
        record["environment"] = environment
    return record, completed.stdout, completed.stderr


def percentile(samples: list[int], percentile_value: int) -> int:
    if not samples:
        raise ValueError("percentile requires samples")
    ordered = sorted(samples)
    index = ((len(ordered) - 1) * percentile_value + 99) // 100
    return ordered[index]


def environment(root: Path, results: Path) -> dict[str, Any]:
    def output(command: list[str]) -> str:
        return subprocess.run(command, cwd=root, text=True, capture_output=True).stdout.strip()

    mount = output(["findmnt", "-no", "SOURCE,FSTYPE,TARGET", "--target", str(results)])
    return {
        "host": platform.node(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpu": output(["bash", "-lc", "lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1"]),
        "memory": output(["bash", "-lc", "free -b | sed -n '2p'"]),
        "filesystem": mount,
        "nvme": "nvme" in mount.lower(),
        "gpu": output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader"]),
        "cuda": output(["nvcc", "--version"]),
        "compiler": output(["c++", "--version"]).splitlines()[0],
        "cmake": output(["cmake", "--version"]).splitlines()[0],
    }

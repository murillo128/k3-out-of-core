#!/usr/bin/env python3
"""Shared, dependency-free helpers for Phase 12.5 trace evidence."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def write_json(path: Path, value: Any, *, replace: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    descriptor = os.open(path, flags, 0o644)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as destination:
            destination.write(canonical_bytes(value))
            destination.flush()
            os.fsync(destination.fileno())
    finally:
        os.close(descriptor)


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None,
        timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True,
        timeout=timeout, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def trace_processor_version(binary: Path) -> str:
    return run([str(binary), "--version"]).stdout.strip()


def query_trace(binary: Path, trace: Path, sql: Path, *, timeout: int = 900) -> list[dict[str, str]]:
    completed = run([str(binary), str(trace), "--query-file", str(sql)], timeout=timeout)
    output = completed.stdout.strip()
    if not output:
        return []
    return list(csv.DictReader(io.StringIO(output)))


def scalar(row: dict[str, str], key: str) -> int:
    value = row.get(key)
    if value is None or value == "[NULL]":
        raise ValueError(f"missing scalar {key}")
    return int(value)


def file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}

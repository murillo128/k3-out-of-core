#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

PROJECT_BASE = "96b0b483c6bc0bfc2679669e5bb049081c7660ae"
CHECKPOINT_B_PROJECT = "a39eeafa4fee6af6a44fd03d630cf1cac79500d3"
LLAMA_BASE = "7a606dd4e11a108929f799253809a904f55feae4"
LLAMA_CANDIDATE = "b71e40f91b1a0dab578d56ac733211453704d674"
MAIN_POLICY = "6ba55dcadf78cbfef4fba09bf4495c225651710b"
CHECKPOINT_A_COMMENT = 5135836934
CHECKPOINT_A_PROJECT = "be8672b9ba991a108ca6d0ffb43fae0e960519d4"
CHECKPOINT_A_LLAMA = "990a416b62e896e2a15f0b160236cb9e3575e4e2"
CHECKPOINT_B_COMMENT = 5140081178
PHASE6_MANIFEST = "results/2026-07-30/skynet/phase6-gguf-storage/phase6-manifest.json"
RESULTS = "results/2026-07-31/skynet/phase7-async-runtime"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(root: Path, path: Path) -> dict:
    resolved = path.resolve()
    return {
        "path": str(resolved.relative_to(root.resolve())),
        "size": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_command(command: list[str], cwd: Path, environment: dict[str, str] | None = None) -> tuple[dict, str, str]:
    started = time.monotonic_ns()
    env = os.environ.copy()
    if environment:
        env.update(environment)
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, env=env)
    duration_ms = (time.monotonic_ns() - started) // 1_000_000
    record = {
        "command": command,
        "cwd": str(cwd.resolve()),
        "duration_ms": duration_ms,
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout.encode()),
        "stderr_sha256": sha256_bytes(completed.stderr.encode()),
        "stdout_tail": completed.stdout.splitlines()[-12:],
        "stderr_tail": completed.stderr.splitlines()[-12:],
    }
    if environment:
        record["environment"] = environment
    return record, completed.stdout, completed.stderr


def diagnostics(output: str, prefix: str) -> dict:
    line = next((line for line in output.splitlines() if line.startswith(prefix)), None)
    if line is None:
        raise RuntimeError(f"missing {prefix} record")
    result: dict[str, object] = {}
    for field in line.split("\t")[1:]:
        key, value = field.split("=", 1)
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def tab_records(output: str) -> dict[str, list[dict]]:
    records: dict[str, list[dict]] = {}
    for line in output.splitlines():
        if "\t" not in line or "=" not in line:
            continue
        prefix = line.split("\t", 1)[0]
        try:
            record = diagnostics(line, prefix)
        except (RuntimeError, ValueError):
            continue
        records.setdefault(prefix, []).append(record)
    return records


def filesystem(path: Path) -> dict:
    stat = os.statvfs(path)
    source = subprocess.run(
        ["findmnt", "-no", "SOURCE,FSTYPE,TARGET", "--target", str(path)],
        text=True,
        capture_output=True,
    )
    return {
        "findmnt": source.stdout.strip(),
        "block_size": stat.f_bsize,
        "physically_on_nvme": "nvme" in source.stdout.lower(),
    }


def environment(root: Path, results_root: Path) -> dict:
    def output(command: list[str]) -> str:
        return subprocess.run(command, cwd=root, text=True, capture_output=True).stdout.strip()

    return {
        "host": platform.node(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpu": output(["bash", "-lc", "lscpu | sed -n 's/^Model name:[[:space:]]*//p' | head -n 1"]),
        "memory": output(["bash", "-lc", "free -b | sed -n '2p'"]),
        "filesystem": filesystem(results_root),
        "gpu": output(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap", "--format=csv,noheader"]),
        "compiler": output(["c++", "--version"]).splitlines()[0],
        "cmake": output(["cmake", "--version"]).splitlines()[0],
    }

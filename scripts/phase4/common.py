#!/usr/bin/env python3
"""Shared helpers and immutable identities for issue #17 Phase 4 evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


PROJECT_BASE = "0da90c6711e00613820183c1811dcaf1baffb409"
LLAMA_BASE = "a120de8e2d0b552c51eacd7d701ef1dd994bc3db"
CHECKPOINT_A_PROJECT = "b839fa407758714684c0d1769c265332ac8e713f"
CHECKPOINT_A_LLAMA = "8ededcb548b0d9dc6248d6ba490aecedca576bec"
CHECKPOINT_A_COMMENT = 5131012078
MODELS = {
    "f16": {
        "name": "Kimi-K3-0.40B-F16.gguf", "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
    },
    "mxfp4": {
        "name": "Kimi-K3-0.40B-MXFP4.gguf", "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, env=dict(os.environ), text=True,
                               capture_output=True, check=False)
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n"
                           f"{completed.stdout}\n{completed.stderr}")
    return completed


def git(root: Path, *args: str) -> str:
    return run(["git", *args], root).stdout.strip()


def parse_fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise RuntimeError(f"expected one {prefix} line, found {len(lines)}")
    result: dict[str, Any] = {}
    for field in lines[0].split("\t")[1:]:
        key, value = field.split("=", 1)
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def validate_models(models: dict[str, Path]) -> None:
    for name, path in models.items():
        expected = MODELS[name]
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"immutable model identity mismatch: {path}")


def cmake_configuration(build: Path) -> dict[str, str]:
    result = {}
    for line in (build / "CMakeCache.txt").read_text().splitlines():
        if line.startswith(("#", "//")) or "=" not in line or ":" not in line.split("=", 1)[0]:
            continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        if key.startswith("GGML_") or key in {"BUILD_SHARED_LIBS", "CMAKE_BUILD_TYPE",
                "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_CUDA_COMPILER",
                "CMAKE_CUDA_ARCHITECTURES"}:
            result[key] = value
    return dict(sorted(result.items()))


def gpu_identity(root: Path) -> dict[str, Any]:
    completed = run(["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total,compute_cap",
                     "--format=csv,noheader,nounits"], root, check=False)
    return {"query": completed.stdout.strip(), "available": completed.returncode == 0}


def run_monitored(command: list[str], cwd: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    process = subprocess.Popen(command, cwd=cwd, env=dict(os.environ), text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    peak_mib = 0.0
    while process.poll() is None:
        sample = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                     cwd, check=False)
        if sample.returncode == 0:
            for line in sample.stdout.splitlines():
                try:
                    peak_mib = max(peak_mib, float(line.strip()))
                except ValueError:
                    pass
        time.sleep(0.02)
    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), peak_mib


def json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

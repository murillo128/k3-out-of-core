#!/usr/bin/env python3
"""Shared helpers and immutable identities for issue #20 Phase 5 evidence."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_BASE = "114f0de6f5d1cbd5f9ef6255f9100f3f4d52380a"
LLAMA_BASE = "57fe1eabbe3d0ced59096a0744efc91e286fb1c7"
CHECKPOINT_A_PROJECT = "6404770597f979a8290d1de3f6bc503ab7d74d8b"
CHECKPOINT_A_LLAMA = "5ffed360965a1de7e2d788b8637a470183d27165"
CHECKPOINT_A_COMMENT = 5132379446
PHASE4_MANIFEST = "results/2026-07-30/skynet/phase4-hot-cache/phase4-manifest.json"
MODELS = {
    "f16": {"name": "Kimi-K3-0.40B-F16.gguf", "size": 784318432,
            "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
            "source_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"},
    "mxfp4": {"name": "Kimi-K3-0.40B-MXFP4.gguf", "size": 751976576,
              "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
              "source_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"},
}

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()

def run(command: list[str], cwd: Path, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    actual_env = dict(os.environ)
    if env: actual_env.update(env)
    result = subprocess.run(command, cwd=cwd, env=actual_env, text=True,
                            capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}\n"
                           f"{result.stdout}\n{result.stderr}")
    return result

def git(root: Path, *args: str) -> str:
    return run(["git", *args], root).stdout.strip()

def parse_fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1: raise RuntimeError(f"expected one {prefix} line, found {len(lines)}")
    result: dict[str, Any] = {}
    for field in lines[0].split("\t")[1:]:
        key, value = field.split("=", 1)
        try: result[key] = int(value)
        except ValueError:
            try: result[key] = float(value)
            except ValueError: result[key] = value
    return result

def validate_models(models: dict[str, Path]) -> None:
    for name, path in models.items():
        expected = MODELS[name]
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"immutable model identity mismatch: {path}")

def cmake_configuration(build: Path) -> dict[str, str]:
    result = {}
    for line in (build / "CMakeCache.txt").read_text().splitlines():
        if line.startswith(("#", "//")) or "=" not in line or ":" not in line.split("=", 1)[0]: continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        if key.startswith("GGML_") or key in {"BUILD_SHARED_LIBS", "CMAKE_BUILD_TYPE",
                "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "CMAKE_CUDA_COMPILER",
                "CMAKE_CUDA_ARCHITECTURES"}: result[key] = value
    return dict(sorted(result.items()))

def gpu_identity(root: Path) -> dict[str, Any]:
    result = run(["nvidia-smi", "--query-gpu=name,uuid,driver_version,memory.total,compute_cap",
                  "--format=csv,noheader,nounits"], root, check=False)
    return {"available": result.returncode == 0, "query": result.stdout.strip()}

def run_monitored(command: list[str], cwd: Path, env: dict[str, str] | None = None):
    actual_env = dict(os.environ)
    if env: actual_env.update(env)
    process = subprocess.Popen(command, cwd=cwd, env=actual_env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    captured: list[tuple[str, str]] = []
    drain = threading.Thread(target=lambda: captured.append(process.communicate()), daemon=True)
    drain.start()
    peak_gpu_mib = 0.0
    peak_rss_kib = 0
    while drain.is_alive():
        gpu = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], cwd, check=False)
        for line in gpu.stdout.splitlines():
            try: peak_gpu_mib = max(peak_gpu_mib, float(line.strip()))
            except ValueError: pass
        try:
            for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"): peak_rss_kib = max(peak_rss_kib, int(line.split()[1]))
        except (FileNotFoundError, ProcessLookupError): pass
        time.sleep(0.01)
    drain.join()
    stdout, stderr = captured[0]
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), peak_gpu_mib, peak_rss_kib

def json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

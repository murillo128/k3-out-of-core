#!/usr/bin/env python3
"""Capture the immutable inputs and host environment for K3 Phase 1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


EXPECTED = {
    "project_commit": "fe6512a8fbfb6a7824672d4a86f7b7bf35141e5d",
    "submodule_commit": "84245db4c790af22135f34992689edcc11877003",
    "published_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca",
    "f16_sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
    "mxfp4_sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
}


def run(argv: list[str], *, required: bool = True, cwd: Path | None = None) -> dict[str, Any]:
    executable = shutil.which(argv[0])
    if executable is None:
        if required:
            raise RuntimeError(f"required command is unavailable: {argv[0]}")
        return {"status": "unavailable", "value": None, "reason": f"{argv[0]} not found"}
    completed = subprocess.run(
        argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
    )
    value = completed.stdout.strip()
    if completed.returncode != 0:
        if required:
            raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(argv)}\n{value}")
        return {
            "status": "unavailable",
            "value": None,
            "reason": f"exit {completed.returncode}: {value}",
        }
    return {"status": "observed", "value": value, "reason": None}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_equal(name: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{name} mismatch: expected {expected}, observed {actual}")


def cmake_cache(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"mandatory CMake cache missing: {path}")
    prefixes = ("CMAKE_BUILD_TYPE", "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "GGML_CPU", "GGML_CUDA")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("//") or line.startswith("#") or "=" not in line or ":" not in line:
            continue
        left, value = line.split("=", 1)
        key = left.split(":", 1)[0]
        if key.startswith(prefixes):
            values[key] = value
    if not {"CMAKE_BUILD_TYPE", "CMAKE_C_COMPILER", "CMAKE_CXX_COMPILER", "GGML_CPU", "GGML_CUDA"} <= values.keys():
        raise RuntimeError(f"mandatory CMake settings missing from {path}")
    return dict(sorted(values.items()))


def package_versions() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in ("huggingface-hub", "safetensors", "tiktoken", "torch", "transformers"):
        try:
            result[name] = {"status": "observed", "value": importlib.metadata.version(name), "reason": None}
        except importlib.metadata.PackageNotFoundError:
            result[name] = {"status": "unavailable", "value": None, "reason": "package not installed"}
    return result


def parse_meminfo() -> dict[str, int]:
    text = Path("/proc/meminfo").read_text(encoding="utf-8")
    match = re.search(r"^MemTotal:\s+(\d+) kB$", text, re.MULTILINE)
    if not match:
        raise RuntimeError("MemTotal missing from /proc/meminfo")
    return {"total_bytes": int(match.group(1)) * 1024}


def artifact(path: Path, expected_hash: str) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(f"mandatory artifact missing: {path}")
    observed_hash = sha256(path)
    require_equal(str(path), observed_hash, expected_hash)
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": observed_hash}


def capture(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    project_sha = run(["git", "rev-parse", "HEAD"], cwd=root)["value"]
    submodule_sha = run(["git", "-C", "llama.cpp", "rev-parse", "HEAD"], cwd=root)["value"]
    run(["git", "merge-base", "--is-ancestor", EXPECTED["project_commit"], project_sha], cwd=root)
    require_equal("llama.cpp commit", submodule_sha, EXPECTED["submodule_commit"])
    if run(["git", "-C", "llama.cpp", "status", "--porcelain"], cwd=root)["value"]:
        raise RuntimeError("llama.cpp worktree is not clean")

    gpu_query = run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]
    )
    gpu_fields = [field.strip() for field in gpu_query["value"].splitlines()[0].split(",")]
    if len(gpu_fields) != 3:
        raise RuntimeError("unexpected nvidia-smi GPU query output")

    os_release = run(["lsb_release", "-ds"], required=False)
    nvcc = run(["nvcc", "--version"], required=False)
    storage = run(["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,MODEL,FSTYPE,MOUNTPOINTS"])
    filesystem = run(["findmnt", "-J", "-T", str(root), "-o", "SOURCE,FSTYPE,TARGET,OPTIONS"])
    cpu_model = run(["lscpu", "-J"])

    environment = {
        "schema_version": 1,
        "capture": {"hostname": socket.gethostname(), "cwd": str(root)},
        "os": {
            "description": os_release,
            "system": platform.system(),
            "kernel": platform.release(),
            "machine": platform.machine(),
        },
        "cpu": {"lscpu": json.loads(cpu_model["value"])},
        "memory": parse_meminfo(),
        "gpu": {
            "name": gpu_fields[0],
            "vram_mib": int(gpu_fields[1]),
            "driver_version": gpu_fields[2],
        },
        "toolchain": {
            "cuda_toolkit": nvcc,
            "cc": run(["cc", "--version"])["value"].splitlines()[0],
            "cxx": run(["c++", "--version"])["value"].splitlines()[0],
            "cmake": run(["cmake", "--version"])["value"].splitlines()[0],
            "python": {"executable": sys.executable, "version": platform.python_version()},
            "hf": {"executable": shutil.which("hf"), "version": run(["hf", "version"])["value"]},
            "packages": package_versions(),
        },
        "builds": {
            "cpu": cmake_cache(root / "llama.cpp/build-cpu/CMakeCache.txt"),
            "cuda": cmake_cache(root / "llama.cpp/build-cuda/CMakeCache.txt"),
        },
        "storage": {
            "block_devices": json.loads(storage["value"]),
            "project_filesystem": json.loads(filesystem["value"]),
        },
    }

    revisions_file = root / "models/hf/REVISIONS.txt"
    revisions_text = revisions_file.read_text(encoding="utf-8") if revisions_file.is_file() else ""
    if EXPECTED["published_revision"] not in (root / "manifests/kimi-k3-0.40b-phase1.json").read_text():
        raise RuntimeError("published GGUF revision is absent from the committed manifest")
    input_manifest = {
        "schema_version": 1,
        "repositories": {
            "project": {"approved_base_commit": EXPECTED["project_commit"], "capture_commit": project_sha},
            "llama_cpp": {"commit": submodule_sha, "worktree_clean": True},
        },
        "models": {
            "source_revisions_record": {"path": str(revisions_file.relative_to(root)), "contents": revisions_text.strip()},
            "published_gguf_revision": EXPECTED["published_revision"],
        },
        "artifacts": {
            "f16": artifact(root / "models/gguf/Kimi-K3-0.40B-F16.gguf", EXPECTED["f16_sha256"]),
            "mxfp4": artifact(root / "models/gguf/Kimi-K3-0.40B-MXFP4.gguf", EXPECTED["mxfp4_sha256"]),
        },
    }
    return environment, input_manifest


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-manifest", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    environment, inputs = capture(root)
    write_json(args.output, environment)
    write_json(args.input_manifest, inputs)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"capture_environment: {error}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Shared immutable inputs and process helpers for issue #13 evidence."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any


PROJECT_BASE = "81df862da6e4ff9db005f6265470070bb5456f4c"
LLAMA_BASE = "4daaaa1a4dd26d6465f84891b854b5f7ddc03020"
PUBLISHED_GGUF = "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"
PUBLISHED_CORPUS = "2d838d6b4d0aca4e9af1e7d899e57ad29330c72e"
MODELS = {
    "f16": {
        "name": "Kimi-K3-0.40B-F16.gguf",
        "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
    },
    "mxfp4": {
        "name": "Kimi-K3-0.40B-MXFP4.gguf",
        "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    completed = subprocess.run(
        command, cwd=cwd, env=environment, text=True, capture_output=True, check=False
    )
    if check and completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}"
        )
    return completed


def git(root: Path, *arguments: str) -> str:
    return run(["git", *arguments], root).stdout.strip()


def validate_models(paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        expected = MODELS[name]
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise RuntimeError(f"immutable model identity mismatch: {path}")


def parse_fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise RuntimeError(f"expected exactly one {prefix} line")
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


def ensure_baseline(root: Path) -> dict[str, Path]:
    base_root = Path("/tmp") / f"k3-phase3-baseline-{LLAMA_BASE[:12]}"
    source = base_root / "llama.cpp"
    if not source.exists():
        base_root.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--quiet", "--shared", str(root / "llama.cpp"), str(source)], root)
        run(["git", "checkout", "--quiet", "--detach", LLAMA_BASE], source)
    if git(source, "rev-parse", "HEAD") != LLAMA_BASE or git(source, "status", "--porcelain"):
        raise RuntimeError("baseline checkout identity or cleanliness mismatch")

    builds: dict[str, Path] = {}
    for backend, cuda in (("cpu", "OFF"), ("cuda", "ON")):
        build = base_root / f"build-{backend}"
        configure = [
            "cmake", "-S", str(source), "-B", str(build),
            "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_SHARED_LIBS=ON",
            "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_CURL=OFF", f"-DGGML_CUDA={cuda}",
        ]
        run(configure, root)
        run(["cmake", "--build", str(build), "--target", "llama", "-j4"], root)
        builds[backend] = build
    return builds


def compile_cpp(
    root: Path,
    build: Path,
    output: Path,
    sources: list[Path],
    include_source: Path,
    extra_options: list[str] | None = None,
) -> list[str]:
    command = [
        "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
        *(extra_options or []),
        f"-I{include_source / 'include'}", f"-I{include_source / 'src'}",
        f"-I{include_source / 'ggml/include'}", f"-I{root / 'scripts/phase2'}",
        *[str(source) for source in sources],
        f"-L{build / 'bin'}", f"-Wl,-rpath,{build / 'bin'}",
        "-lllama", "-lggml", "-lggml-base", "-o", str(output),
    ]
    run(command, root)
    return command


def cmake_configuration(build: Path) -> dict[str, str]:
    result = {}
    for line in (build / "CMakeCache.txt").read_text().splitlines():
        if line.startswith(("#", "//")) or "=" not in line or ":" not in line.split("=", 1)[0]:
            continue
        key_type, value = line.split("=", 1)
        key = key_type.split(":", 1)[0]
        if key.startswith("GGML_") or key in {
            "BUILD_SHARED_LIBS", "CMAKE_BUILD_TYPE", "CMAKE_C_COMPILER",
            "CMAKE_CXX_COMPILER", "CMAKE_CUDA_COMPILER", "CMAKE_CUDA_ARCHITECTURES",
        }:
            result[key] = value
    return dict(sorted(result.items()))

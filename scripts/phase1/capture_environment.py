#!/usr/bin/env python3
"""Capture the Phase 1 execution environment and immutable local inputs."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
EXECUTION_BASE = "511e87fc98cca8069fc57526fbb04b10789967eb"
EXECUTION_BRANCH = "codex/phase1-closeout-clean"
LLAMA_CPP_COMMIT = "84245db4c790af22135f34992689edcc11877003"
HF_TOOL_PATH = "/usr/local/src/k3-out-of-core/.venv-k3/bin/hf"
PYTHON_PATH = "/usr/local/src/k3-out-of-core/.venv-k3/bin/python"

SOURCE_MODELS = {
    "f16_reference": {
        "repo_id": "inference-optimization/Kimi-K3-0.40B",
        "revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
        "path": "models/hf/Kimi-K3-0.40B",
    },
    "mxfp4_source": {
        "repo_id": "inference-optimization/Kimi-K3-0.40B-MXFP4",
        "revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
        "path": "models/hf/Kimi-K3-0.40B-MXFP4",
    },
}

GGUF_ARTIFACTS = {
    "Kimi-K3-0.40B-F16.gguf": {
        "path": "models/gguf/Kimi-K3-0.40B-F16.gguf",
        "size_bytes": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
    },
    "Kimi-K3-0.40B-MXFP4.gguf": {
        "path": "models/gguf/Kimi-K3-0.40B-MXFP4.gguf",
        "size_bytes": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
    },
}

REQUIRED_PACKAGES = (
    "huggingface_hub",
    "transformers",
    "tiktoken",
    "torch",
    "safetensors",
    "numpy",
    "sentencepiece",
)

BUILD_CACHE_KEYS = re.compile(
    r"^(BUILD_SHARED_LIBS|GGML_|LLAMA_|CMAKE_(?:BUILD_TYPE|GENERATOR|"
    r"GENERATOR_INSTANCE|GENERATOR_PLATFORM|GENERATOR_TOOLSET|"
    r"(?:C|CXX|CUDA)_(?:COMPILER|FLAGS).*))"
)


class CaptureError(RuntimeError):
    """Raised when a mandatory observation or invariant is unavailable."""


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def command_observation(
    command: list[str],
    *,
    cwd: Path | None = None,
    required: bool,
) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        if required:
            raise CaptureError(f"mandatory command is unavailable: {command[0]}")
        return {
            "status": "unavailable",
            "command": command,
            "reason": f"command not found: {command[0]}",
        }

    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    observation: dict[str, Any] = {
        "status": "available" if completed.returncode == 0 else "error",
        "command": command,
        "executable": executable,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
    }
    if completed.stderr.strip():
        observation["stderr"] = completed.stderr.strip()
    if completed.returncode != 0 and required:
        raise CaptureError(
            f"mandatory command failed ({completed.returncode}): {' '.join(command)}"
        )
    if completed.returncode != 0:
        observation["reason"] = "command exited nonzero"
    return observation


def required_stdout(command: list[str], *, cwd: Path) -> str:
    return command_observation(command, cwd=cwd, required=True)["stdout"]


def read_os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    if not path.is_file():
        raise CaptureError(f"mandatory OS release file is unavailable: {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.lower()] = value.strip().strip('"')
    for key in ("id", "name", "version_id", "pretty_name"):
        if not values.get(key):
            raise CaptureError(f"mandatory OS release field is missing: {key}")
    return values


def cpu_facts(path: Path = Path("/proc/cpuinfo")) -> dict[str, Any]:
    if not path.is_file():
        raise CaptureError(f"mandatory CPU information is unavailable: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^model name\s*:\s*(.+)$", text, re.MULTILINE)
    if match is None or os.cpu_count() is None:
        raise CaptureError("mandatory CPU model or logical CPU count is unavailable")
    return {
        "model": match.group(1).strip(),
        "logical_cpu_count": os.cpu_count(),
        "architecture": platform.machine(),
    }


def memory_facts(path: Path = Path("/proc/meminfo")) -> dict[str, int]:
    if not path.is_file():
        raise CaptureError(f"mandatory memory information is unavailable: {path}")
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB$", line)
        if match:
            values[f"{match.group(1).lower()}_bytes"] = int(match.group(2)) * 1024
    if set(values) != {"memtotal_bytes", "memavailable_bytes"}:
        raise CaptureError("mandatory total or available memory is missing")
    return values


def parse_cmake_cache(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        raise CaptureError(f"mandatory CMake cache is unavailable: {path}")
    selected: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        if ":" not in key_and_type:
            continue
        key, value_type = key_and_type.split(":", 1)
        if BUILD_CACHE_KEYS.match(key):
            selected[key] = {"type": value_type, "value": value}
    required_keys = {
        "BUILD_SHARED_LIBS",
        "CMAKE_BUILD_TYPE",
        "CMAKE_C_COMPILER",
        "CMAKE_CXX_COMPILER",
        "CMAKE_GENERATOR",
        "GGML_CUDA",
        "LLAMA_BUILD_TESTS",
        "LLAMA_BUILD_TOOLS",
    }
    missing = sorted(required_keys - selected.keys())
    if missing:
        raise CaptureError(f"mandatory CMake cache keys are missing: {missing}")
    return dict(sorted(selected.items()))


def parse_hf_revisions(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise CaptureError(f"mandatory Hugging Face revision file is unavailable: {path}")
    revisions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{40}", parts[1]):
            raise CaptureError(f"invalid Hugging Face revision line: {line!r}")
        revisions[parts[0]] = parts[1]
    return revisions


def file_manifest(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        raise CaptureError(f"mandatory model directory is unavailable: {directory}")
    files: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if (
            not path.is_file()
            or ".cache" in path.parts
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
        ):
            continue
        files.append(
            {
                "path": path.relative_to(directory).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if not files:
        raise CaptureError(f"mandatory model directory contains no files: {directory}")
    return files


def capture_build(repo_root: Path, name: str) -> dict[str, Any]:
    cache = repo_root / "llama.cpp" / name / "CMakeCache.txt"
    return {
        "path": cache.parent.relative_to(repo_root).as_posix(),
        "cmake_cache_path": cache.relative_to(repo_root).as_posix(),
        "cmake_cache_size_bytes": cache.stat().st_size,
        "cmake_cache_sha256": sha256_file(cache),
        "configuration": parse_cmake_cache(cache),
    }


def capture_gpu(repo_root: Path) -> dict[str, Any]:
    fields = ["name", "memory.total", "driver_version", "pci.bus_id", "pstate"]
    observation = command_observation(
        ["nvidia-smi", f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
        cwd=repo_root,
        required=True,
    )
    rows = list(csv.reader(io.StringIO(observation["stdout"]), skipinitialspace=True))
    if not rows or any(len(row) != len(fields) for row in rows):
        raise CaptureError("nvidia-smi returned no GPUs or an unexpected field count")
    devices = []
    for row in rows:
        devices.append(
            {
                "name": row[0],
                "memory_total_mib": int(row[1]),
                "driver_version": row[2],
                "pci_bus_id": row[3],
                "pstate": row[4],
            }
        )
    return {
        "status": "available",
        "executable": observation["executable"],
        "devices": devices,
    }


def capture_python_tools(repo_root: Path) -> dict[str, Any]:
    python_path = os.path.abspath(sys.executable)
    hf_path_raw = shutil.which("hf")
    hf_path = os.path.abspath(hf_path_raw) if hf_path_raw else None
    if python_path != PYTHON_PATH:
        raise CaptureError(f"unexpected Python interpreter: {python_path}")
    if hf_path != HF_TOOL_PATH:
        raise CaptureError(f"unexpected Hugging Face CLI path: {hf_path}")

    packages: dict[str, str] = {}
    for package in REQUIRED_PACKAGES:
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise CaptureError(f"mandatory Python package is unavailable: {package}") from error
    return {
        "python": {
            "path": python_path,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "hugging_face_cli": {
            "path": hf_path,
            "version_output": required_stdout(["hf", "version"], cwd=repo_root),
        },
        "packages": packages,
    }


def capture_storage(repo_root: Path) -> dict[str, Any]:
    mount = command_observation(
        [
            "findmnt",
            "-b",
            "-J",
            "-T",
            str(repo_root),
            "-o",
            "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,USED,AVAIL",
        ],
        cwd=repo_root,
        required=True,
    )
    mount_data = json.loads(mount["stdout"])
    filesystems = mount_data.get("filesystems", [])
    if len(filesystems) != 1:
        raise CaptureError("findmnt did not return exactly one workspace filesystem")

    block_devices = command_observation(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,PATH,TYPE,SIZE,MODEL,FSTYPE,MOUNTPOINTS,ROTA,TRAN",
        ],
        cwd=repo_root,
        required=False,
    )
    if block_devices["status"] == "available":
        parsed = json.loads(block_devices["stdout"])
        block_devices = {
            "status": "available",
            "devices": [
                device
                for device in parsed.get("blockdevices", [])
                if device.get("type") == "disk"
            ],
        }

    usage = shutil.disk_usage(repo_root)
    return {
        "workspace_filesystem": filesystems[0],
        "workspace_disk_usage_bytes": {
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
        },
        "physical_block_devices": block_devices,
    }


def capture_environment(repo_root: Path, captured_at: str) -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name, version_args in (
        ("cmake", ["--version"]),
        ("cc", ["--version"]),
        ("c++", ["--version"]),
        ("gcc", ["--version"]),
        ("g++", ["--version"]),
    ):
        observation = command_observation([name, *version_args], cwd=repo_root, required=True)
        tools[name] = {
            "path": observation["executable"],
            "version_output": observation["stdout"],
        }

    nvcc = command_observation(["nvcc", "--version"], cwd=repo_root, required=True)
    cuda_match = re.search(r"release\s+([^,]+),\s+V([^\s]+)", nvcc["stdout"])
    if cuda_match is None:
        raise CaptureError("could not parse the mandatory CUDA toolkit version")

    hostname = platform.node()
    if hostname != "skynet":
        raise CaptureError(f"unexpected execution host: {hostname}")

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": captured_at,
        "host": {
            "hostname": hostname,
            "os_release": read_os_release(),
            "kernel": {
                "release": platform.release(),
                "version": platform.version(),
                "system": platform.system(),
            },
            "cpu": cpu_facts(),
            "memory": memory_facts(),
            "gpu": capture_gpu(repo_root),
        },
        "cuda_toolkit": {
            "status": "available",
            "path": nvcc["executable"],
            "release": cuda_match.group(1),
            "version": cuda_match.group(2),
            "version_output": nvcc["stdout"],
        },
        "tools": tools,
        "python_environment": capture_python_tools(repo_root),
        "builds": {
            "cpu": capture_build(repo_root, "build-cpu"),
            "cuda": capture_build(repo_root, "build-cuda"),
        },
        "storage": capture_storage(repo_root),
        "validation": {"status": "pass", "mandatory_errors": []},
    }


def capture_inputs(repo_root: Path, captured_at: str) -> dict[str, Any]:
    head = required_stdout(["git", "rev-parse", "HEAD"], cwd=repo_root)
    branch = required_stdout(["git", "branch", "--show-current"], cwd=repo_root)
    if branch != EXECUTION_BRANCH:
        raise CaptureError(f"unexpected execution branch: {branch}")
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXECUTION_BASE, "origin/main"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", EXECUTION_BASE, head],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    submodule_path = repo_root / "llama.cpp"
    submodule_head = required_stdout(["git", "rev-parse", "HEAD"], cwd=submodule_path)
    submodule_status = required_stdout(["git", "status", "--porcelain"], cwd=submodule_path)
    if submodule_head != LLAMA_CPP_COMMIT:
        raise CaptureError(f"unexpected llama.cpp revision: {submodule_head}")
    if submodule_status:
        raise CaptureError("llama.cpp submodule is not clean")

    recorded_revisions = parse_hf_revisions(repo_root / "models/hf/REVISIONS.txt")
    models: dict[str, Any] = {}
    for key, expected in SOURCE_MODELS.items():
        observed_revision = recorded_revisions.get(expected["repo_id"])
        if observed_revision != expected["revision"]:
            raise CaptureError(
                f"unexpected source revision for {expected['repo_id']}: {observed_revision}"
            )
        directory = repo_root / expected["path"]
        models[key] = {
            **expected,
            "files": file_manifest(directory),
        }

    artifacts: dict[str, Any] = {}
    for name, expected in GGUF_ARTIFACTS.items():
        path = repo_root / expected["path"]
        if not path.is_file():
            raise CaptureError(f"mandatory GGUF artifact is unavailable: {path}")
        observed = {
            "path": expected["path"],
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if observed["size_bytes"] != expected["size_bytes"]:
            raise CaptureError(f"unexpected size for {name}: {observed['size_bytes']}")
        if observed["sha256"] != expected["sha256"]:
            raise CaptureError(f"unexpected SHA-256 for {name}: {observed['sha256']}")
        artifacts[name] = observed

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": captured_at,
        "approved_contract": {
            "issue": "https://github.com/murillo128/k3-out-of-core/issues/7",
            "execution_profile": "STANDARD",
            "execution_base": EXECUTION_BASE,
            "execution_branch": EXECUTION_BRANCH,
            "llama_cpp_commit": LLAMA_CPP_COMMIT,
        },
        "project": {
            "repository": required_stdout(["git", "remote", "get-url", "origin"], cwd=repo_root),
            "captured_head": head,
            "branch": branch,
            "execution_base_is_ancestor_of_head": True,
            "execution_base_is_ancestor_of_origin_main": True,
        },
        "llama_cpp": {
            "repository": required_stdout(
                ["git", "remote", "get-url", "origin"], cwd=submodule_path
            ),
            "commit": submodule_head,
            "clean": True,
        },
        "source_models": models,
        "published_gguf_artifacts": artifacts,
        "validation": {"status": "pass", "mandatory_errors": []},
    }


def validate_documents(environment: dict[str, Any], inputs: dict[str, Any]) -> None:
    errors: list[str] = []
    if environment.get("schema_version") != SCHEMA_VERSION:
        errors.append("environment schema version mismatch")
    if inputs.get("schema_version") != SCHEMA_VERSION:
        errors.append("inputs schema version mismatch")
    if environment.get("host", {}).get("hostname") != "skynet":
        errors.append("environment hostname mismatch")
    contract = inputs.get("approved_contract", {})
    if contract.get("execution_base") != EXECUTION_BASE:
        errors.append("execution base mismatch")
    if contract.get("execution_branch") != EXECUTION_BRANCH:
        errors.append("execution branch mismatch")
    if inputs.get("llama_cpp", {}).get("commit") != LLAMA_CPP_COMMIT:
        errors.append("llama.cpp commit mismatch")
    if inputs.get("validation", {}).get("status") != "pass":
        errors.append("input capture did not pass")
    if environment.get("validation", {}).get("status") != "pass":
        errors.append("environment capture did not pass")
    if errors:
        raise CaptureError("; ".join(errors))


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="project repository root",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = (
        args.output_dir
        if args.output_dir.is_absolute()
        else (repo_root / args.output_dir)
    ).resolve()
    captured_at = datetime.now(timezone.utc).isoformat()
    try:
        environment = capture_environment(repo_root, captured_at)
        inputs = capture_inputs(repo_root, captured_at)
        validate_documents(environment, inputs)
        write_json_atomic(output_dir / "environment.json", environment)
        write_json_atomic(output_dir / "inputs.json", inputs)
    except (CaptureError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"capture failed: {error}", file=sys.stderr)
        return 1
    print(f"wrote {output_dir / 'environment.json'}")
    print(f"wrote {output_dir / 'inputs.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

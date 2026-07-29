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
    "hostname": "skynet",
    "gpu_name": "NVIDIA GeForce GTX 1650",
    "gpu_vram_mib": 4096,
    "project_commit": "fe6512a8fbfb6a7824672d4a86f7b7bf35141e5d",
    "submodule_commit": "84245db4c790af22135f34992689edcc11877003",
    "published_revision": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca",
    "source_revisions": {
        "inference-optimization/Kimi-K3-0.40B": "d853649387ffe8f48ce0198a29ac1a44205031f7",
        "inference-optimization/Kimi-K3-0.40B-MXFP4": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    },
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
    cmake_patterns = (
        re.compile(r"^CMAKE_BUILD_TYPE$"),
        re.compile(r"^CMAKE_GENERATOR$"),
        re.compile(r"^CMAKE_(?:C|CXX|CUDA)_COMPILER(?:_AR|_RANLIB)?$"),
        re.compile(r"^CMAKE_(?:C|CXX|CUDA)_FLAGS(?:_[A-Z]+)?$"),
    )
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("//") or line.startswith("#") or "=" not in line or ":" not in line:
            continue
        left, value = line.split("=", 1)
        key = left.split(":", 1)[0]
        if key.startswith("GGML_") or any(pattern.match(key) for pattern in cmake_patterns):
            values[key] = value
    required = {
        "CMAKE_BUILD_TYPE",
        "CMAKE_GENERATOR",
        "CMAKE_C_COMPILER",
        "CMAKE_CXX_COMPILER",
        "CMAKE_C_FLAGS",
        "CMAKE_CXX_FLAGS",
        "GGML_CPU",
        "GGML_CUDA",
    }
    if not required <= values.keys():
        raise RuntimeError(f"mandatory CMake settings missing from {path}")
    return dict(sorted(values.items()))


def tool_info(argv: list[str], *, required: bool = True) -> dict[str, Any]:
    result = run(argv, required=required)
    path = shutil.which(argv[0])
    if result["status"] == "observed" and not result["value"]:
        if required:
            raise RuntimeError(f"required command produced no output: {' '.join(argv)}")
        result = {
            "status": "unavailable",
            "value": None,
            "reason": f"{' '.join(argv)} exited successfully but produced no output",
        }
    return {"path": path, **result}


def optional_file(path: Path) -> dict[str, Any]:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError) as error:
        return {"status": "unavailable", "value": None, "reason": f"{path}: {error}"}
    if not value:
        return {"status": "unavailable", "value": None, "reason": f"{path}: empty"}
    return {"status": "observed", "value": value, "reason": None}


def source_revisions(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"mandatory source revision record missing: {path}")
    observed: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{40}", fields[1]):
            raise RuntimeError(f"malformed source revision at {path}:{line_number}")
        if fields[0] in observed:
            raise RuntimeError(f"duplicate source revision for {fields[0]}")
        observed[fields[0]] = fields[1]
    if observed != EXPECTED["source_revisions"]:
        raise RuntimeError(
            f"source revisions mismatch: expected {EXPECTED['source_revisions']}, observed {observed}"
        )
    return observed


def find_block_device(devices: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for device in devices:
        if device.get("name") == name:
            return device
        found = find_block_device(device.get("children", []), name)
        if found is not None:
            return found
    return None


def storage_facts(block_data: dict[str, Any], filesystem_data: dict[str, Any]) -> dict[str, Any]:
    filesystems = filesystem_data.get("filesystems", [])
    if not filesystems or not isinstance(filesystems[0].get("source"), str):
        raise RuntimeError("project filesystem source missing from findmnt output")
    source = filesystems[0]["source"]
    if not source.startswith("/dev/"):
        raise RuntimeError(f"project filesystem is not backed by a block device: {source}")
    partition_name = Path(source).name
    devices = block_data.get("blockdevices", [])
    partition = find_block_device(devices, partition_name)
    if partition is None:
        raise RuntimeError(f"project filesystem device absent from lsblk output: {partition_name}")
    disk_name = partition.get("pkname") or partition_name
    disk = find_block_device(devices, disk_name)
    if disk is None:
        raise RuntimeError(f"backing disk absent from lsblk output: {disk_name}")
    sysfs = Path("/sys/class/block") / disk_name / "device"
    return {
        "filesystem": filesystem_data,
        "root_partition": partition,
        "root_disk": disk,
        "firmware_revision": optional_file(sysfs / "firmware_rev"),
        "pcie_link": {
            "current_speed": optional_file(sysfs / "device/current_link_speed"),
            "current_width": optional_file(sysfs / "device/current_link_width"),
            "max_speed": optional_file(sysfs / "device/max_link_speed"),
            "max_width": optional_file(sysfs / "device/max_link_width"),
        },
    }


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


def capture(root: Path, *, command_runner=run, observed_hostname: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    hostname = observed_hostname if observed_hostname is not None else socket.gethostname()
    require_equal("hostname", hostname, EXPECTED["hostname"])
    project_sha = run(["git", "rev-parse", "HEAD"], cwd=root)["value"]
    submodule_sha = run(["git", "-C", "llama.cpp", "rev-parse", "HEAD"], cwd=root)["value"]
    run(["git", "merge-base", "--is-ancestor", EXPECTED["project_commit"], project_sha], cwd=root)
    require_equal("llama.cpp commit", submodule_sha, EXPECTED["submodule_commit"])
    if run(["git", "-C", "llama.cpp", "status", "--porcelain"], cwd=root)["value"]:
        raise RuntimeError("llama.cpp worktree is not clean")

    gpu_query = command_runner(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]
    )
    gpu_fields = [field.strip() for field in gpu_query["value"].splitlines()[0].split(",")]
    if len(gpu_fields) != 3:
        raise RuntimeError("unexpected nvidia-smi GPU query output")
    require_equal("GPU name", gpu_fields[0], EXPECTED["gpu_name"])
    require_equal("GPU VRAM MiB", gpu_fields[1], str(EXPECTED["gpu_vram_mib"]))

    os_release = run(["lsb_release", "-ds"], required=False)
    nvcc = run(["nvcc", "--version"], required=False)
    storage = run(
        ["lsblk", "-J", "-b", "-o", "NAME,PATH,PKNAME,TYPE,SIZE,MODEL,SERIAL,REV,TRAN,FSTYPE,MOUNTPOINTS"]
    )
    filesystem = run(["findmnt", "-J", "-T", str(root), "-o", "SOURCE,FSTYPE,TARGET,OPTIONS"])
    cpu_model = run(["lscpu", "-J"])

    environment = {
        "schema_version": 1,
        "capture": {"hostname": hostname, "cwd": str(root)},
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
            "cuda_toolkit": {"path": shutil.which("nvcc"), **nvcc},
            "cc": tool_info(["cc", "--version"]),
            "cxx": tool_info(["c++", "--version"]),
            "cmake": tool_info(["cmake", "--version"]),
            "nvidia_smi": tool_info(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]
            ),
            "lsblk": tool_info(["lsblk", "--version"]),
            "findmnt": tool_info(["findmnt", "--version"]),
            "lscpu": tool_info(["lscpu", "--version"]),
            "lsb_release": tool_info(["lsb_release", "--version"], required=False),
            "python": {"path": sys.executable, "status": "observed", "value": platform.python_version(), "reason": None},
            "hf": tool_info(["hf", "version"]),
            "packages": package_versions(),
        },
        "builds": {
            "cpu": cmake_cache(root / "llama.cpp/build-cpu/CMakeCache.txt"),
            "cuda": cmake_cache(root / "llama.cpp/build-cuda/CMakeCache.txt"),
        },
        "storage": storage_facts(json.loads(storage["value"]), json.loads(filesystem["value"])),
    }

    revisions_file = root / "models/hf/REVISIONS.txt"
    revisions = source_revisions(revisions_file)
    if EXPECTED["published_revision"] not in (root / "manifests/kimi-k3-0.40b-phase1.json").read_text():
        raise RuntimeError("published GGUF revision is absent from the committed manifest")
    input_manifest = {
        "schema_version": 1,
        "repositories": {
            "project": {"approved_base_commit": EXPECTED["project_commit"], "capture_commit": project_sha},
            "llama_cpp": {"commit": submodule_sha, "worktree_clean": True},
        },
        "models": {
            "source_revisions_record": {"path": str(revisions_file.relative_to(root)), "revisions": revisions},
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

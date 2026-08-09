#!/usr/bin/env python3
"""Shared fixed-fixture configuration for issue 69 evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT = (
    "<｜begin▁of▁sentence｜><｜User｜>Explain why a careful measurement should distinguish "
    "observed facts from assumptions.<｜Assistant｜><think>"
)
MODEL_REVISION = "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1"
MODEL_VARIANT = "UD-Q2_K_XL"
DEFAULT_COLD_BYTES = 16 * 1024**3
FULL_COLD_BYTES = 128 * 1024**3
RING_BYTES = 67_173_120
PEER_STAGING_BYTES = 67_108_864


def cell_arguments(cell: str) -> list[str]:
    if cell == "S0":
        return [
            "--hot-slots", "268", "--role-config", "EXPLICIT", "--resident-device", "0",
            "--expert-role-devices", "0:268", "--peer-staging-bytes", "0",
        ]
    if cell == "S1":
        return [
            "--hot-slots", "268", "--role-config", "EXPLICIT", "--resident-device", "0",
            "--expert-role-devices", "0:268,1:268", "--peer-staging-bytes", str(PEER_STAGING_BYTES),
        ]
    if cell == "D1":
        return [
            "--hot-slots", "536", "--role-config", "EXPLICIT", "--resident-device", "0",
            "--expert-role-devices", "1:536", "--peer-staging-bytes", str(PEER_STAGING_BYTES),
        ]
    if cell == "A1":
        return [
            "--hot-slots", "268", "--role-config", "EXPLICIT", "--resident-device", "0",
            "--expert-role-devices", "0:268,1:1305", "--peer-staging-bytes", str(PEER_STAGING_BYTES),
        ]
    raise ValueError(f"unknown issue 69 cell: {cell}")


def probe_command(
    probe: Path,
    model: Path,
    output: Path,
    cell: str,
    cold_bytes: int = DEFAULT_COLD_BYTES,
    runtime_mode: str = "PRODUCTION_PERFORMANCE",
    prewarm_cold_all: bool = False,
    io_workers: int | None = None,
) -> list[str]:
    compliance = runtime_mode == "COMPLIANCE"
    command = [
        str(probe), "--model", str(model), "--output", str(output),
        "--mode", "cold", "--expert-runtime-mode", runtime_mode, "--prompt", PROMPT,
        "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL",
        "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--cold-bytes", str(cold_bytes), "--ring-bytes", str(RING_BYTES),
        "--peer-transport", "HOST_STAGED", "--queue-depth", "256",
        "--trace-capacity", "65536" if compliance else "0",
        "--n-ctx", "4096", "--n-batch", "128", "--n-ubatch", "128",
        "--max-generate", "24", "--background", "0",
        "--observe-routes", "1" if compliance else "0", "--transport", "POSITIONAL",
        "--config-source", "EXPLICIT", "--integrity", "NONE",
        "--prewarm-cold-all", "1" if prewarm_cold_all else "0",
    ]
    if io_workers is not None:
        command.extend(["--io-workers", str(io_workers)])
    return command + cell_arguments(cell)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, object]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}


def cmake_build_identity(probe: Path) -> dict[str, object]:
    cache = probe.resolve().parent.parent / "CMakeCache.txt"
    if not cache.is_file():
        raise FileNotFoundError(cache)
    selected = {}
    for line in cache.read_text(errors="replace").splitlines():
        if line.startswith("//") or line.startswith("#") or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        key = key_and_type.split(":", 1)[0]
        if key in {"CMAKE_BUILD_TYPE", "CMAKE_CXX_COMPILER", "CMAKE_CXX_FLAGS_RELEASE",
                   "GGML_CUDA", "GGML_CUDA_GRAPHS", "LLAMA_PERFETTO"}:
            selected[key] = value
    return {"probe": file_identity(probe), "cmake_cache": file_identity(cache), "options": selected}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def validate_workload(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text())
    if value.get("status") != "pass" or len(value.get("generated_ids", [])) != 24:
        raise RuntimeError(f"incomplete issue 69 workload: {path}")
    lifecycle = value.get("lifecycle", {})
    active = (
        lifecycle.get("active_background_flights", 0),
        lifecycle.get("cold_current_transfer_refs", 0),
        lifecycle.get("cold_current_request_refs", 0),
        lifecycle.get("cold_current_cpu_execution_refs", 0),
        lifecycle.get("current_hot_pins", 0),
    )
    if any(active):
        raise RuntimeError(f"non-terminal issue 69 workload: {path}")
    return value


def decode_tps(workload: dict[str, object]) -> float:
    latencies = workload["latency_us"]
    decode_us = sum(latencies[1:])
    if decode_us <= 0:
        raise ValueError("decode latency is not positive")
    return (len(latencies) - 1) * 1_000_000.0 / decode_us


def output_identity(workload: dict[str, object], include_logits: bool = True) -> str:
    value = {
        "prompt_ids": workload["prompt_ids"],
        "generated_ids": workload["generated_ids"],
        "generated_text": workload["generated_text"],
    }
    if include_logits:
        value["logits_fnv64"] = workload["logits_fnv64"]
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

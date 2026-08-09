#!/usr/bin/env python3
"""Shared full-Kimi-K3 configuration and evidence helpers for issue 73."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODEL_REPOSITORY = "moonshotai/Kimi-K3"
MODEL_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
CONFIG_SHA256 = "9710e121a58d03ac92c8d6da287a19541994319afbbe6d6202af001ffd379213"
EXPERT_BUNDLE_BYTES = 17_547_264
DEFAULT_COLD_BYTES = 16 * 1024**3
DEFAULT_RING_BYTES = 128 * 1024**2
DEFAULT_PEER_STAGING_BYTES = 128 * 1024**2

PROMPT = (
    '<|open|>message role="system" type="thinking-effort"<|sep|>`thinking_effort` guides on how much '
    'to think in your thinking channel (not including the response channel), supported values include '
    '`low`, `medium`, `high`, and `max`.\nNow the system is invoked with `thinking_effort=max`.'
    '<|close|>message<|sep|><|end_of_msg|><|open|>message role="user"<|sep|>Explain why a careful '
    'measurement should distinguish observed facts from assumptions.<|close|>message<|sep|><|end_of_msg|>'
    '<|open|>message role="assistant"<|sep|><|open|>think<|sep|>'
)


def probe_command(
    probe: Path,
    model: Path,
    output: Path,
    *,
    role_devices: str,
    n_gpu_layers: int,
    max_generate: int = 24,
    cold_bytes: int = DEFAULT_COLD_BYTES,
    ring_bytes: int = DEFAULT_RING_BYTES,
    peer_staging_bytes: int = DEFAULT_PEER_STAGING_BYTES,
    io_workers: int = 4,
    queue_depth: int = 64,
    async_cold_fill: bool = False,
    transport: str = "POSITIONAL",
    runtime_mode: str = "PRODUCTION_PERFORMANCE",
    observe_routes: bool = False,
    trace_capacity: int = 0,
) -> list[str]:
    roles = [item for item in role_devices.split(",") if item]
    ordinals = [int(item.split(":", 1)[0]) for item in roles]
    if not roles or n_gpu_layers < 0 or max_generate <= 0:
        raise ValueError("invalid full-K3 probe configuration")
    if transport not in {"POSITIONAL", "BUFFERED", "DIRECT_IO", "DIRECT_IO_POSITIONAL"}:
        raise ValueError(f"unsupported transport: {transport}")
    remote_roles = len(roles) > 1 or ordinals[0] != 0
    command = [
        str(probe), "--model", str(model), "--output", str(output),
        "--mode", "cold", "--expert-runtime-mode", runtime_mode,
        "--prompt", PROMPT, "--hot-policy", "LRU", "--cold-policy", "LRU",
        "--scope", "GLOBAL", "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU",
        "--hot-slots", str(sum(int(item.split(":", 1)[1]) for item in roles)),
        "--cold-bytes", str(cold_bytes), "--ring-bytes", str(ring_bytes),
        "--role-config", "EXPLICIT", "--resident-device", "0",
        "--expert-role-devices", role_devices, "--peer-transport", "HOST_STAGED",
        "--peer-staging-bytes", str(peer_staging_bytes if remote_roles else 0),
        "--queue-depth", str(queue_depth), "--io-workers", str(io_workers),
        "--trace-capacity", str(trace_capacity), "--n-ctx", "4096",
        "--n-batch", "128", "--n-ubatch", "128", "--n-gpu-layers", str(n_gpu_layers),
        "--max-generate", str(max_generate), "--background", "0",
        "--observe-routes", "1" if observe_routes else "0", "--transport", transport,
        "--io-access", "NORMAL", "--config-source", "EXPLICIT", "--integrity", "NONE",
        "--prewarm-cold-all", "0", "--async-cold-fill", "1" if async_cold_fill else "0",
    ]
    return command


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, object]:
    return {"name": path.name, "path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def validate_workload(path: Path, generated_tokens: int) -> dict[str, object]:
    value = json.loads(path.read_text())
    if value.get("status") != "pass" or len(value.get("generated_ids", [])) != generated_tokens:
        raise RuntimeError(f"incomplete full-K3 workload: {path}")
    lifecycle = value.get("lifecycle", {})
    active = (
        lifecycle.get("active_background_flights", 0),
        lifecycle.get("cold_current_transfer_refs", 0),
        lifecycle.get("cold_current_request_refs", 0),
        lifecycle.get("cold_current_cpu_execution_refs", 0),
        lifecycle.get("current_hot_pins", 0),
    )
    if any(active):
        raise RuntimeError(f"non-terminal full-K3 workload: {path}")
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

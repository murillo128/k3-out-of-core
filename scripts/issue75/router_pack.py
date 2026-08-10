#!/usr/bin/env python3
"""Shared validation and smoke-test helpers for the Kimi K3 router pack."""

from __future__ import annotations

import array
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable


GATE_PATTERN = re.compile(r"^blk\.(\d+)\.ffn_gate_inp\.weight$")
CORRECTION_PATTERN = re.compile(r"^blk\.(\d+)\.exp_probs_b\.bias$")
ROLE_ORDER = {"router_projection_weight": 0, "selection_correction_bias": 1}


class PackError(RuntimeError):
    """Raised when a router-pack invariant is not satisfied."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PackError(f"cannot load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackError(f"expected a JSON object in {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path, chunk_size: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(chunk_size):
                digest.update(chunk)
    except OSError as exc:
        raise PackError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def parse_router_tensor_name(name: str) -> tuple[int, str]:
    match = GATE_PATTERN.fullmatch(name)
    if match:
        return int(match.group(1)), "router_projection_weight"
    match = CORRECTION_PATTERN.fullmatch(name)
    if match:
        return int(match.group(1)), "selection_correction_bias"
    if "ffn_gate_inp" in name or "exp_probs_b" in name:
        raise PackError(f"unexpected router-like tensor name: {name}")
    raise PackError(f"not a router tensor: {name}")


def payload_name(layer: int, role: str) -> str:
    if role == "router_projection_weight":
        suffix = "ffn_gate_inp.weight.f32.bin"
    elif role == "selection_correction_bias":
        suffix = "exp_probs_b.bias.f32.bin"
    else:
        raise PackError(f"unknown semantic role: {role}")
    return f"tensors/blk.{layer:03d}.{suffix}"


def expected_tensor_spec(config: dict[str, Any], layer: int, role: str) -> dict[str, Any]:
    router = config["router"]
    if role == "router_projection_weight":
        return {
            "shape": [router["hidden_dimension"], router["experts_per_layer"]],
            "dtype": router["projection_dtype"],
            "byte_length": (
                router["hidden_dimension"]
                * router["experts_per_layer"]
                * router["projection_element_bytes"]
            ),
            "source_tensor_name": f"blk.{layer}.ffn_gate_inp.weight",
        }
    if role == "selection_correction_bias":
        return {
            "shape": [router["experts_per_layer"]],
            "dtype": router["correction_dtype"],
            "byte_length": router["experts_per_layer"] * router["correction_element_bytes"],
            "source_tensor_name": f"blk.{layer}.exp_probs_b.bias",
        }
    raise PackError(f"unknown semantic role: {role}")


def expected_tensor_keys(config: dict[str, Any]) -> set[tuple[int, str]]:
    router = config["router"]
    first = int(router["first_routed_layer"])
    count = int(router["routed_layer_count"])
    return {
        (layer, role)
        for layer in range(first, first + count)
        for role in ROLE_ORDER
    }


def validate_tensor_records(records: Iterable[dict[str, Any]], config: dict[str, Any]) -> None:
    seen: set[tuple[int, str]] = set()
    source_names: set[str] = set()
    for record in records:
        try:
            layer = int(record["layer"])
            role = str(record["semantic_role"])
            source_name = str(record["source_tensor_name"])
            shape = [int(value) for value in record["shape"]]
            dtype = str(record["dtype"])
            byte_length = int(record["byte_length"])
            source_range = record["source_range"]
            start = int(source_range["offset"])
            end = int(source_range["end_exclusive"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PackError(f"malformed tensor record: {record}") from exc

        parsed_layer, parsed_role = parse_router_tensor_name(source_name)
        if (parsed_layer, parsed_role) != (layer, role):
            raise PackError(f"tensor name/role mismatch for {source_name}")
        key = (layer, role)
        if key in seen:
            raise PackError(f"duplicate routed-layer tensor: layer={layer} role={role}")
        if source_name in source_names:
            raise PackError(f"duplicate source tensor name: {source_name}")
        seen.add(key)
        source_names.add(source_name)

        expected = expected_tensor_spec(config, layer, role)
        if source_name != expected["source_tensor_name"]:
            raise PackError(f"unexpected tensor name for layer={layer} role={role}: {source_name}")
        if shape != expected["shape"]:
            raise PackError(f"shape mismatch for {source_name}: {shape} != {expected['shape']}")
        if dtype != expected["dtype"]:
            raise PackError(f"dtype mismatch for {source_name}: {dtype} != {expected['dtype']}")
        if byte_length != expected["byte_length"]:
            raise PackError(
                f"byte-length mismatch for {source_name}: {byte_length} != {expected['byte_length']}"
            )
        if start < 0 or end - start != byte_length:
            raise PackError(f"invalid source range for {source_name}: [{start}, {end})")
        if record.get("payload_path") != payload_name(layer, role):
            raise PackError(f"unexpected payload path for {source_name}")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PackError(f"invalid SHA-256 for {source_name}")

    expected_keys = expected_tensor_keys(config)
    if seen != expected_keys:
        missing = sorted(expected_keys - seen)
        unexpected = sorted(seen - expected_keys)
        raise PackError(f"router inventory mismatch: missing={missing} unexpected={unexpected}")


def validate_inventory(inventory: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    if inventory.get("schema_version") != "kimi-k3-router-tensor-inventory-v1":
        raise PackError("unsupported tensor inventory schema")
    records = inventory.get("tensors")
    if not isinstance(records, list):
        raise PackError("tensor inventory has no tensor list")
    if inventory.get("tensor_count") != len(records):
        raise PackError("tensor inventory count does not match tensor list")
    validate_tensor_records(records, config)
    return records


def validate_payload_tree(payload_root: Path, records: Iterable[dict[str, Any]]) -> dict[str, int]:
    count = 0
    total_bytes = 0
    expected_paths: set[str] = set()
    for record in records:
        relative = str(record["payload_path"])
        expected_paths.add(relative)
        path = payload_root / relative
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PackError(f"missing payload {path}: {exc}") from exc
        if size != record["byte_length"]:
            raise PackError(f"payload size mismatch for {relative}: {size} != {record['byte_length']}")
        digest = sha256_file(path)
        if digest != record["sha256"]:
            raise PackError(f"payload SHA-256 mismatch for {relative}: {digest}")
        count += 1
        total_bytes += size

    tensors_root = payload_root / "tensors"
    actual_paths = {
        path.relative_to(payload_root).as_posix()
        for path in tensors_root.rglob("*")
        if path.is_file()
    }
    if actual_paths != expected_paths:
        raise PackError(
            "payload tensor set mismatch: "
            f"missing={sorted(expected_paths - actual_paths)} "
            f"unexpected={sorted(actual_paths - expected_paths)}"
        )
    return {"tensor_count": count, "payload_bytes": total_bytes}


def _read_f32_vector(path: Path, element_offset: int, count: int) -> list[float]:
    byte_offset = element_offset * 4
    byte_length = count * 4
    try:
        with path.open("rb") as source:
            source.seek(byte_offset)
            raw = source.read(byte_length)
    except OSError as exc:
        raise PackError(f"cannot read smoke-test vector from {path}: {exc}") from exc
    if len(raw) != byte_length:
        raise PackError(f"short smoke-test read from {path}")
    values = array.array("f")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    return [float(value) for value in values]


def _norm(values: Iterable[float]) -> float:
    return math.sqrt(math.fsum(value * value for value in values))


def static_smoke_test(
    payload_root: Path,
    records: Iterable[dict[str, Any]],
    layers: Iterable[int],
    expert_a: int = 0,
    expert_b: int = 1,
) -> dict[str, Any]:
    by_key = {(int(record["layer"]), str(record["semantic_role"])): record for record in records}
    results = []
    for layer in layers:
        gate = by_key.get((layer, "router_projection_weight"))
        correction = by_key.get((layer, "selection_correction_bias"))
        if gate is None or correction is None:
            raise PackError(f"smoke-test layer {layer} is absent")
        hidden = int(gate["shape"][0])
        experts = int(gate["shape"][1])
        if not (0 <= expert_a < experts and 0 <= expert_b < experts and expert_a != expert_b):
            raise PackError("smoke-test expert indexes are invalid")
        gate_path = payload_root / gate["payload_path"]
        a = _read_f32_vector(gate_path, expert_a * hidden, hidden)
        b = _read_f32_vector(gate_path, expert_b * hidden, hidden)
        norm_a = _norm(a)
        norm_b = _norm(b)
        if norm_a == 0.0 or norm_b == 0.0:
            raise PackError(f"zero router-vector norm at layer {layer}")
        dot = math.fsum(left * right for left, right in zip(a, b, strict=True))
        corrections = _read_f32_vector(payload_root / correction["payload_path"], 0, experts)
        results.append(
            {
                "layer": layer,
                "experts": [expert_a, expert_b],
                "router_vector_norms": [norm_a, norm_b],
                "cosine_similarity": dot / (norm_a * norm_b),
                "correction_bias_norm": _norm(corrections),
                "correction_bias_min": min(corrections),
                "correction_bias_max": max(corrections),
            }
        )
    return {
        "schema_version": "kimi-k3-router-pack-smoke-v1",
        "operation": "bounded router-vector norms, cosine similarity, and correction-bias summary",
        "storage_interpretation": "GGUF F32 bytes; each expert vector is contiguous along ne[0]",
        "layers": results,
        "status": "PASS",
    }


def verify_checksums_file(root: Path, checksum_path: Path) -> int:
    count = 0
    for number, line in enumerate(checksum_path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise PackError(f"malformed checksum line {number}") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PackError(f"invalid checksum on line {number}")
        target = root / relative
        if sha256_file(target) != digest:
            raise PackError(f"checksum mismatch for {relative}")
        count += 1
    return count


def write_checksums(root: Path, relatives: Iterable[str], output: Path) -> None:
    lines = [f"{sha256_file(root / relative)}  {relative}" for relative in sorted(relatives)]
    output.write_text("\n".join(lines) + "\n")


def assert_relative_members(members: Iterable[str]) -> None:
    for member in members:
        path = Path(member)
        if path.is_absolute() or ".." in path.parts:
            raise PackError(f"unsafe archive member: {member}")
        if member and not (member == "tensors" or member.startswith("tensors/")):
            raise PackError(f"unexpected archive member: {member}")


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())

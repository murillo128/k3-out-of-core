#!/usr/bin/env python3
"""Validate Kimi-K3 MXFP4 source blocks against their GGUF representation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from safetensors import safe_open


SCHEMA_VERSION = 1
LLAMA_CPP_COMMIT = "84245db4c790af22135f34992689edcc11877003"
SOURCE_MODEL_REVISION = "ef3902c318fb8e13c3507e26055656e687fdfe38"
GGUF_SHA256 = "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169"
LAYERS = (1, 4, 7)
EXPERTS = (0, 3, 7)
PROJECTIONS = {
    "w1": "ffn_gate_exps",
    "w2": "ffn_down_exps",
    "w3": "ffn_up_exps",
}
BLOCK_POSITIONS = ("first", "middle", "last")
ABSOLUTE_TOLERANCE = 1e-6


class ValidationError(RuntimeError):
    """Raised when MXFP4 evidence violates the approved contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def decode_e2m1(code: int) -> float:
    """Decode one OCP E2M1 code without a converter or GGUF quant helper."""
    if not 0 <= code <= 0x0F:
        raise ValidationError(f"E2M1 code is outside four bits: {code}")
    magnitude_bits = code & 0x07
    exponent = magnitude_bits >> 1
    mantissa = magnitude_bits & 0x01
    if exponent == 0:
        magnitude = mantissa * 0.5
    else:
        magnitude = (1.0 + mantissa * 0.5) * (2.0 ** (exponent - 1))
    return -magnitude if code & 0x08 else magnitude


def decode_e8m0(scale_byte: int) -> float:
    """Decode one OCP E8M0 biased exponent; 0xff is the reserved NaN code."""
    if not 0 <= scale_byte <= 0xFF:
        raise ValidationError(f"E8M0 byte is outside eight bits: {scale_byte}")
    if scale_byte == 0xFF:
        raise ValidationError("E8M0 scale byte 0xff is reserved for NaN")
    return math.ldexp(1.0, scale_byte - 127)


def unpack_source_codes(packed_bytes: Sequence[int]) -> list[int]:
    """Decode source bytes: element 2i is low and element 2i+1 is high."""
    if len(packed_bytes) != 16:
        raise ValidationError(f"source block has {len(packed_bytes)} bytes, expected 16")
    codes: list[int] = []
    for byte in packed_bytes:
        value = int(byte)
        codes.extend((value & 0x0F, (value >> 4) & 0x0F))
    return codes


def unpack_gguf_codes(block: Sequence[int]) -> tuple[int, list[int]]:
    """Decode GGUF block: scale then values j / j+16 in low/high nibbles."""
    if len(block) != 17:
        raise ValidationError(f"GGUF block has {len(block)} bytes, expected 17")
    scale = int(block[0])
    low = [int(byte) & 0x0F for byte in block[1:]]
    high = [(int(byte) >> 4) & 0x0F for byte in block[1:]]
    return scale, low + high


def repack_source_block(scale_byte: int, packed_bytes: Sequence[int]) -> bytes:
    """Express a source block in GGUF byte order using only explicit bit moves."""
    codes = unpack_source_codes(packed_bytes)
    quant_bytes = bytes(codes[index] | (codes[index + 16] << 4) for index in range(16))
    return bytes((scale_byte,)) + quant_bytes


def decode_block(scale_byte: int, codes: Sequence[int]) -> list[float]:
    if len(codes) != 32:
        raise ValidationError(f"MXFP4 block has {len(codes)} values, expected 32")
    scale = decode_e8m0(scale_byte)
    return [decode_e2m1(int(code)) * scale for code in codes]


def block_indexes(block_count: int) -> dict[str, int]:
    if block_count < 1:
        raise ValidationError("MXFP4 tensor contains no blocks")
    return {
        "first": 0,
        "middle": block_count // 2,
        "last": block_count - 1,
    }


def source_names(layer: int, expert: int, projection: str) -> tuple[str, str]:
    prefix = (
        f"language_model.model.layers.{layer}.block_sparse_moe."
        f"experts.{expert}.{projection}.weight"
    )
    return f"{prefix}_packed", f"{prefix}_scale"


def gguf_name(layer: int, projection: str) -> str:
    return f"blk.{layer}.{PROJECTIONS[projection]}.weight"


def load_gguf_tensors(repo_root: Path, path: Path) -> dict[str, Any]:
    gguf_python = repo_root / "llama.cpp/gguf-py"
    sys.path.insert(0, str(gguf_python))
    try:
        from gguf import GGUFReader
    except ImportError as error:
        raise ValidationError("pinned GGUF reader is unavailable") from error
    finally:
        sys.path.pop(0)
    reader = GGUFReader(path, "r")
    return {tensor.name: tensor for tensor in reader.tensors}


def validate_sample(
    *,
    layer: int,
    projection: str,
    expert: int,
    position: str,
    packed: np.ndarray,
    scales: np.ndarray,
    gguf_data: np.ndarray,
) -> dict[str, Any]:
    rows, packed_columns = packed.shape
    if packed_columns % 16:
        raise ValidationError(f"source packed width {packed_columns} is not block aligned")
    blocks_per_row = packed_columns // 16
    expected_scale_shape = (rows, blocks_per_row)
    if tuple(scales.shape) != expected_scale_shape:
        raise ValidationError(
            f"source scale shape {tuple(scales.shape)} != {expected_scale_shape}"
        )
    expected_gguf_shape = (8, rows, blocks_per_row * 17)
    if tuple(gguf_data.shape) != expected_gguf_shape:
        raise ValidationError(
            f"GGUF byte shape {tuple(gguf_data.shape)} != {expected_gguf_shape}"
        )

    flat_index = block_indexes(rows * blocks_per_row)[position]
    row, block_in_row = divmod(flat_index, blocks_per_row)
    source_bytes = packed[row, block_in_row * 16 : (block_in_row + 1) * 16]
    source_scale = int(scales[row, block_in_row])
    expected_bytes = repack_source_block(source_scale, source_bytes)
    start = block_in_row * 17
    actual_bytes = bytes(gguf_data[expert, row, start : start + 17])
    gguf_scale, gguf_codes = unpack_gguf_codes(actual_bytes)
    source_codes = unpack_source_codes(source_bytes)
    source_values = decode_block(source_scale, source_codes)
    gguf_values = decode_block(gguf_scale, gguf_codes)
    differences = [abs(left - right) for left, right in zip(source_values, gguf_values)]
    max_error = max(differences)

    checks = {
        "scale_byte_exact": source_scale == gguf_scale,
        "codes_exact_after_reorder": source_codes == gguf_codes,
        "packed_bytes_exact_after_reorder": expected_bytes == actual_bytes,
        "decoded_values_within_tolerance": max_error <= ABSOLUTE_TOLERANCE,
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValidationError(
            f"layer {layer} {projection} expert {expert} {position}: failed {failed}"
        )

    return {
        "layer": layer,
        "projection": projection,
        "gguf_projection": PROJECTIONS[projection],
        "expert": expert,
        "position": position,
        "flat_block_index": flat_index,
        "row": row,
        "block_in_row": block_in_row,
        "blocks_per_row": blocks_per_row,
        "source_scale_byte": source_scale,
        "gguf_scale_byte": gguf_scale,
        "source_packed_hex": bytes(source_bytes).hex(),
        "expected_gguf_block_hex": expected_bytes.hex(),
        "actual_gguf_block_hex": actual_bytes.hex(),
        "source_codes": source_codes,
        "gguf_codes": gguf_codes,
        "source_values": source_values,
        "gguf_values": gguf_values,
        "maximum_absolute_error": max_error,
        "checks": checks,
    }


def known_vectors() -> dict[str, Any]:
    codes = list(range(16))
    decoded = [decode_e2m1(code) for code in codes]
    expected = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]
    expected += [-value for value in expected]
    if decoded != expected:
        raise ValidationError("independent E2M1 decoder failed its complete codebook")
    scale_vectors = {str(value): decode_e8m0(value) for value in (0, 1, 126, 127, 128, 254)}
    return {
        "e2m1_all_codes": {str(code): value for code, value in zip(codes, decoded)},
        "e8m0_boundary_and_unit_vectors": scale_vectors,
        "e8m0_0xff_rejected": True,
    }


def validate(
    repo_root: Path,
    source_path: Path,
    gguf_path: Path,
) -> dict[str, Any]:
    if not source_path.is_file() or not gguf_path.is_file():
        raise ValidationError("required source safetensors or GGUF artifact is unavailable")
    if sha256_file(gguf_path) != GGUF_SHA256:
        raise ValidationError("MXFP4 GGUF SHA-256 differs from the approved artifact")

    gguf_tensors = load_gguf_tensors(repo_root, gguf_path)
    samples: list[dict[str, Any]] = []
    with safe_open(str(source_path), framework="numpy") as source:
        for layer in LAYERS:
            for projection in PROJECTIONS:
                tensor_name = gguf_name(layer, projection)
                tensor = gguf_tensors.get(tensor_name)
                if tensor is None:
                    raise ValidationError(f"missing GGUF tensor: {tensor_name}")
                if int(tensor.tensor_type) != 39:
                    raise ValidationError(f"{tensor_name} is not GGML MXFP4 type 39")
                for expert in EXPERTS:
                    packed_name, scale_name = source_names(layer, expert, projection)
                    if packed_name not in source.keys() or scale_name not in source.keys():
                        raise ValidationError(f"missing source pair: {packed_name}, {scale_name}")
                    packed = source.get_tensor(packed_name).view(np.uint8)
                    scales = source.get_tensor(scale_name).view(np.uint8)
                    for position in BLOCK_POSITIONS:
                        samples.append(
                            validate_sample(
                                layer=layer,
                                projection=projection,
                                expert=expert,
                                position=position,
                                packed=packed,
                                scales=scales,
                                gguf_data=tensor.data,
                            )
                        )

    expected_count = len(LAYERS) * len(PROJECTIONS) * len(EXPERTS) * len(BLOCK_POSITIONS)
    if len(samples) != expected_count:
        raise ValidationError(f"captured {len(samples)} samples, expected {expected_count}")
    maxima = [sample["maximum_absolute_error"] for sample in samples]
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "independence": {
            "implementation": "project-side explicit E2M1/E8M0 and nibble decoder",
            "converter_quantization_helpers_imported": False,
            "gguf_quantization_helpers_imported": False,
            "gguf_reader_role": "metadata and raw tensor byte access only",
        },
        "format_contract": {
            "source": "16 bytes encode adjacent value pairs low/high plus one E8M0 scale per 32 values",
            "gguf": "one E8M0 scale byte followed by 16 bytes pairing values j and j+16 low/high",
            "e2m1": "sign bit 3, two exponent bits, one mantissa bit",
            "e8m0": "2**(byte-127); 0xff is reserved NaN",
            "absolute_tolerance": ABSOLUTE_TOLERANCE,
        },
        "inputs": {
            "source_safetensors": source_path.relative_to(repo_root).as_posix(),
            "source_revision": SOURCE_MODEL_REVISION,
            "gguf": gguf_path.relative_to(repo_root).as_posix(),
            "gguf_sha256": GGUF_SHA256,
            "llama_cpp_commit": LLAMA_CPP_COMMIT,
        },
        "sampling": {
            "layers": list(LAYERS),
            "projections": list(PROJECTIONS),
            "experts": list(EXPERTS),
            "positions": list(BLOCK_POSITIONS),
            "position_definition": "first, floor(total_blocks/2), and last block in row-major tensor order",
            "expected_sample_count": expected_count,
        },
        "known_vectors": known_vectors(),
        "summary": {
            "sample_count": len(samples),
            "exact_scale_byte_matches": sum(sample["checks"]["scale_byte_exact"] for sample in samples),
            "exact_code_matches": sum(sample["checks"]["codes_exact_after_reorder"] for sample in samples),
            "exact_repacked_byte_matches": sum(sample["checks"]["packed_bytes_exact_after_reorder"] for sample in samples),
            "decoded_value_matches": sum(sample["checks"]["decoded_values_within_tolerance"] for sample in samples),
            "maximum_absolute_error": max(maxima),
        },
        "samples": samples,
    }


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("models/hf/Kimi-K3-0.40B-MXFP4/model.safetensors"),
    )
    parser.add_argument(
        "--gguf",
        type=Path,
        default=Path("models/gguf/Kimi-K3-0.40B-MXFP4.gguf"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/2026-07-29/skynet/phase1-closeout-clean/mxfp4-validation.json"),
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    source = args.source if args.source.is_absolute() else repo_root / args.source
    gguf = args.gguf if args.gguf.is_absolute() else repo_root / args.gguf
    output = args.output if args.output.is_absolute() else repo_root / args.output
    try:
        document = validate(repo_root, source, gguf)
        write_json(output, document)
    except (OSError, ValidationError, ValueError) as error:
        print(f"MXFP4 validation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(document["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

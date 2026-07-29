#!/usr/bin/env python3
"""Read and validate K3 route trace schema version 1."""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path
from typing import Any


FILE_MAGIC = b"K3ROUTE\0"
TRAILER_MAGIC = b"K3DONE\0\0"
SCHEMA_VERSION = 1
RECORD_MAGIC = 0x44434552
TRAILER_SIZE = 24
MAX_COUNT = 1_000_000


class RouteTraceError(ValueError):
    """Raised when a route trace is unsupported, incomplete, or corrupt."""


class Cursor:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise RouteTraceError("truncated field")
        value = self.data[self.offset : self.offset + size]
        self.offset += size
        return value

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def string(self) -> str:
        size = self.u32()
        if size > MAX_COUNT:
            raise RouteTraceError("string length is impossible")
        try:
            return self.take(size).decode("utf-8")
        except UnicodeDecodeError as error:
            raise RouteTraceError("string is not UTF-8") from error


def _positive_count(value: int, name: str) -> int:
    if value <= 0 or value > MAX_COUNT:
        raise RouteTraceError(f"{name} is impossible")
    return value


def _positive_u64(value: int, name: str) -> int:
    if value <= 0:
        raise RouteTraceError(f"{name} is impossible")
    return value


def _read_header(data: bytes) -> tuple[dict[str, Any], int]:
    cursor = Cursor(data)
    if cursor.take(8) != FILE_MAGIC:
        raise RouteTraceError("bad file magic")
    version = cursor.u32()
    if version != SCHEMA_VERSION:
        raise RouteTraceError(f"unsupported schema version: {version}")
    header_size = cursor.u32()
    if header_size > MAX_COUNT:
        raise RouteTraceError("header length is impossible")
    header_cursor = Cursor(cursor.take(header_size))
    header = {
        "schema_version": version,
        "model_name": header_cursor.string(),
        "model_size": _positive_u64(header_cursor.u64(), "model size"),
        "model_sha256": header_cursor.string(),
        "model_source_revision": header_cursor.string(),
        "published_gguf_revision": header_cursor.string(),
        "llama_cpp_revision": header_cursor.string(),
        "run_id": header_cursor.string(),
        "expert_count": _positive_count(header_cursor.u32(), "expert count"),
        "top_k": _positive_count(header_cursor.u32(), "top-k"),
        "routed_layer_count": _positive_count(header_cursor.u32(), "routed layer count"),
    }
    if header_cursor.offset != len(header_cursor.data):
        raise RouteTraceError("unexpected header bytes")
    return header, cursor.offset


def read_route_trace(path: str | Path) -> dict[str, Any]:
    data = Path(path).read_bytes()
    if len(data) < 16 + TRAILER_SIZE:
        raise RouteTraceError("trace is truncated")

    trailer_offset = len(data) - TRAILER_SIZE
    trailer = Cursor(data[trailer_offset:])
    if trailer.take(8) != TRAILER_MAGIC:
        raise RouteTraceError("completion trailer is missing")
    expected_records = trailer.u64()
    expected_checksum = trailer.u32()
    if trailer.u32() != 0:
        raise RouteTraceError("unsupported trailer flags")
    actual_checksum = zlib.crc32(data[:trailer_offset]) & 0xFFFFFFFF
    if actual_checksum != expected_checksum:
        raise RouteTraceError("checksum mismatch")

    header, offset = _read_header(data[:trailer_offset])
    records: list[dict[str, Any]] = []
    previous_order: tuple[int, int, int, int] | None = None

    while offset < trailer_offset:
        if offset + 8 > trailer_offset:
            raise RouteTraceError("truncated record frame")
        frame_magic, payload_size = struct.unpack_from("<II", data, offset)
        offset += 8
        if frame_magic != RECORD_MAGIC:
            raise RouteTraceError("bad record magic")
        if payload_size > MAX_COUNT or offset + payload_size > trailer_offset:
            raise RouteTraceError("record length is impossible")
        payload = Cursor(data[offset : offset + payload_size])
        offset += payload_size

        record_ordinal = payload.u64()
        if record_ordinal != len(records):
            raise RouteTraceError("record ordinal is not contiguous")
        request_ordinal = payload.u64()
        ubatch_ordinal = payload.u64()
        phase = payload.u32()
        if phase not in (1, 2):
            raise RouteTraceError("record phase is unsupported")
        layer = payload.i32()
        if layer < 0:
            raise RouteTraceError("negative routed layer")
        batch_row = payload.u32()
        position = payload.i32()
        n_seq_ids = _positive_count(payload.u32(), "sequence ID count")
        sequence_ids = [payload.i32() for _ in range(n_seq_ids)]
        top_k = _positive_count(payload.u32(), "record top-k")
        if top_k != header["top_k"]:
            raise RouteTraceError("record top-k differs from header")
        selected_experts = [payload.i32() for _ in range(top_k)]
        if any(expert < 0 or expert >= header["expert_count"] for expert in selected_experts):
            raise RouteTraceError("selected expert is out of range")
        weights = [payload.f32() for _ in range(top_k)]
        if not all(math.isfinite(weight) for weight in weights):
            raise RouteTraceError("routing weight is not finite")
        if payload.offset != len(payload.data):
            raise RouteTraceError("unexpected record bytes")

        order = (request_ordinal, ubatch_ordinal, layer, batch_row)
        if previous_order is not None and order < previous_order:
            raise RouteTraceError("record order is not canonical")
        previous_order = order
        records.append(
            {
                "record_ordinal": record_ordinal,
                "request_ordinal": request_ordinal,
                "ubatch_ordinal": ubatch_ordinal,
                "phase": "PREFILL" if phase == 1 else "DECODE",
                "layer": layer,
                "batch_row": batch_row,
                "position": position,
                "sequence_ids": sequence_ids,
                "selected_experts": selected_experts,
                "weights": weights,
            }
        )

    if len(records) != expected_records:
        raise RouteTraceError("trailer record count mismatch")
    return {"header": header, "records": records, "checksum": f"{actual_checksum:08x}"}

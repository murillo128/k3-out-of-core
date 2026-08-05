#!/usr/bin/env python3
"""Deterministic, bounded helpers for the Phase 12P evidence corpus."""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

U64_MAX = (1 << 64) - 1
GIB = 1 << 30
SOURCE_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
CONFIG_SHA256 = "9710e121a58d03ac92c8d6da287a19541994319afbbe6d6202af001ffd379213"
SEED = 2608
PAYLOAD_VERSION = "phase12p-k3-payload-v1"
PAYLOAD_DOMAIN = b"k3-out-of-core/phase12p/payload/v1\0"
ROUTE_VERSION = "phase12p-route-corpus-v1"
LAYOUT_A_VERSION = "phase12p-projection-spans-v1"
LAYOUT_B_VERSION = "phase12p-contiguous-experts-v1"
JOURNAL_VERSION = "phase12p-journal-v1"
PROJECTIONS = ("gate", "up", "down")
WEIGHT_BITS = (
    "3f800000", "3f700000", "3f600000", "3f500000",
    "3f400000", "3f300000", "3f200000", "3f100000",
    "3f000000", "3ee00000", "3ec00000", "3ea00000",
    "3e800000", "3e400000", "3e000000", "3d800000",
)
SHUFFLE = (0, 8, 4, 12, 2, 10, 6, 14, 1, 9, 5, 13, 3, 11, 7, 15)


def checked_add(left: int, right: int) -> int:
    if left < 0 or right < 0 or left > U64_MAX - right:
        raise OverflowError("unsigned 64-bit addition overflow")
    return left + right


def checked_mul(left: int, right: int) -> int:
    if left < 0 or right < 0 or (left and right > U64_MAX // left):
        raise OverflowError("unsigned 64-bit multiplication overflow")
    return left * right


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


@dataclass(frozen=True)
class Scale:
    layers: int = 92
    experts: int = 896
    selected: int = 16
    projection_bytes: int = 5_849_088
    tokens: int = 32

    def __post_init__(self) -> None:
        for name in ("layers", "experts", "selected", "projection_bytes", "tokens"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.selected != 16:
            raise ValueError("Phase 12P route contract requires exactly 16 selected experts")
        if self.experts < self.selected:
            raise ValueError("expert count is smaller than selected count")

    @property
    def bundle_bytes(self) -> int:
        return checked_mul(self.projection_bytes, 3)

    @property
    def bundle_count(self) -> int:
        return checked_mul(self.layers, self.experts)

    @property
    def logical_bytes(self) -> int:
        return checked_mul(self.bundle_count, self.bundle_bytes)

    @property
    def materialized_bundles(self) -> int:
        return checked_mul(self.layers, self.selected)

    @property
    def useful_bytes(self) -> int:
        return checked_mul(self.materialized_bundles, self.bundle_bytes)

    def validate_full(self) -> None:
        expected = (92, 896, 16, 5_849_088, 32)
        actual = (self.layers, self.experts, self.selected, self.projection_bytes, self.tokens)
        if actual != expected:
            raise ValueError(f"full-scale identity mismatch: {actual} != {expected}")
        if self.bundle_bytes != 17_547_264 or self.logical_bytes != 1_446_456_066_048:
            raise ValueError("accepted Phase 8 arithmetic mismatch")
        if self.useful_bytes != 25_829_572_608:
            raise ValueError("mandatory useful-byte arithmetic mismatch")


FULL_SCALE = Scale()


def selected_experts(scale: Scale, layer: int) -> tuple[int, ...]:
    if not 0 <= layer < scale.layers:
        raise ValueError("layer out of range")
    base = (SEED + 131 * layer) % scale.experts
    values = tuple((base + 17 * rank) % scale.experts for rank in range(16))
    if len(set(values)) != 16:
        raise ValueError("selected expert formula did not produce 16 unique keys")
    return values


def logical_projection_offset(scale: Scale, layer: int, expert: int, projection: int) -> int:
    if not 0 <= layer < scale.layers or not 0 <= expert < scale.experts:
        raise ValueError("layer/expert out of range")
    if projection not in (0, 1, 2):
        raise ValueError("projection out of range")
    bundle = checked_add(checked_mul(layer, scale.experts), expert)
    offset = checked_add(checked_mul(bundle, scale.bundle_bytes), checked_mul(projection, scale.projection_bytes))
    if checked_add(offset, scale.projection_bytes) > scale.logical_bytes:
        raise OverflowError("projection extends past logical EOF")
    return offset


def record_key(layer: int, expert: int, projection: int) -> bytes:
    if not 0 <= projection < 3 or layer < 0 or expert < 0:
        raise ValueError("invalid payload record key")
    return b"".join((
        PAYLOAD_DOMAIN, SOURCE_REVISION.encode(), b"\0", bytes.fromhex(CONFIG_SHA256),
        struct.pack("<QII", SEED, layer, expert), struct.pack("B", projection),
    ))


def payload_chunks(layer: int, expert: int, projection: int, length: int) -> Iterator[bytes]:
    if length < 0:
        raise ValueError("negative payload length")
    key = record_key(layer, expert, projection)
    remaining = length
    index = 0
    while remaining:
        chunk = hashlib.sha256(key + struct.pack("<Q", index)).digest()
        yield chunk[:remaining]
        remaining -= min(remaining, len(chunk))
        index += 1


def route_document(scale: Scale) -> dict[str, object]:
    requests: list[dict[str, object]] = []
    for request_name in ("COLD_SPREAD", "LOGICAL_SHUFFLE", "HALF_HOT"):
        records: list[list[object]] = []
        for token in range(scale.tokens):
            for layer in range(scale.layers):
                keys = selected_experts(scale, layer)
                if request_name == "COLD_SPREAD":
                    order = range(16)
                elif request_name == "LOGICAL_SHUFFLE":
                    order = SHUFFLE
                else:
                    shift = (3 * token) % 8
                    order = tuple(range(8)) + tuple(8 + ((rank + shift) % 8) for rank in range(8))
                for logical_rank, key_rank in enumerate(order):
                    records.append([token, layer, logical_rank, keys[key_rank], WEIGHT_BITS[logical_rank]])
        requests.append({"request": request_name, "records": records})
    return {
        "schema_version": ROUTE_VERSION,
        "source_revision": SOURCE_REVISION,
        "config_sha256": CONFIG_SHA256,
        "seed": SEED,
        "dimensions": {"layers": scale.layers, "experts": scale.experts, "tokens": scale.tokens, "selected": 16},
        "requests": requests,
    }


def route_identity(scale: Scale) -> tuple[dict[str, object], str]:
    document = route_document(scale)
    return document, sha256_bytes(canonical_bytes(document))


def fsync_file(stream: BinaryIO) -> None:
    stream.flush()
    os.fdatasync(stream.fileno())


class Journal:
    """Append-only checksummed journal with recoverable incomplete tails."""
    STATES = ("STARTED", "WRITTEN", "REREAD_OK", "COMMITTED")

    def __init__(self, path: Path, layout: str):
        self.path = path
        self.layout = layout
        path.parent.mkdir(parents=True, exist_ok=True)
        self.records = self.read_valid(path, truncate=True)
        self.sequence = len(self.records)
        self.stream = path.open("ab", buffering=0)

    @staticmethod
    def read_valid(path: Path, *, truncate: bool = False) -> list[dict[str, object]]:
        if not path.exists():
            return []
        valid: list[dict[str, object]] = []
        valid_end = 0
        with path.open("rb") as stream:
            while True:
                line = stream.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                    checksum = record.pop("checksum")
                    if checksum != sha256_bytes(canonical_bytes(record)):
                        break
                    if record.get("schema_version") != JOURNAL_VERSION or record.get("sequence") != len(valid):
                        break
                    valid.append(record)
                    valid_end = stream.tell()
                except (ValueError, TypeError, KeyError):
                    break
        if truncate and valid_end != path.stat().st_size:
            with path.open("r+b") as stream:
                stream.truncate(valid_end)
                os.fsync(stream.fileno())
        return valid

    def append(self, layer: int, expert: int, state: str, bundle_sha256: str | None = None) -> None:
        if state not in self.STATES:
            raise ValueError("invalid journal state")
        record: dict[str, object] = {
            "schema_version": JOURNAL_VERSION, "layout": self.layout,
            "sequence": self.sequence, "layer": layer, "expert": expert, "state": state,
        }
        if bundle_sha256 is not None:
            record["bundle_sha256"] = bundle_sha256
        output = dict(record)
        output["checksum"] = sha256_bytes(canonical_bytes(record))
        self.stream.write(canonical_bytes(output))
        os.fsync(self.stream.fileno())
        self.records.append(record)
        self.sequence += 1

    def close(self) -> None:
        self.stream.close()


def validate_journal_chains(records: Iterable[dict[str, object]]) -> set[tuple[int, int]]:
    chains: dict[tuple[int, int], list[str]] = {}
    for record in records:
        key = (int(record["layer"]), int(record["expert"]))
        chains.setdefault(key, []).append(str(record["state"]))
    committed: set[tuple[int, int]] = set()
    for key, states in chains.items():
        if states == list(Journal.STATES):
            committed.add(key)
        elif states not in (["STARTED"], ["STARTED", "WRITTEN"], ["STARTED", "WRITTEN", "REREAD_OK"]):
            raise ValueError(f"invalid durable-state chain for {key}: {states}")
    return committed


def planned_ranges(scale: Scale) -> list[tuple[int, int, int, int]]:
    ranges: list[tuple[int, int, int, int]] = []
    for layer in range(scale.layers):
        for expert in selected_experts(scale, layer):
            for projection in range(3):
                ranges.append((logical_projection_offset(scale, layer, expert, projection), scale.projection_bytes, layer, expert))
    return sorted(ranges)


def seek_extent_coverage(path: Path, ranges: Iterable[tuple[int, int]]) -> dict[str, object]:
    """Prove every requested byte is data using SEEK_DATA/SEEK_HOLE."""
    if not hasattr(os, "SEEK_DATA"):
        return {"method": "SEEK_DATA_SEEK_HOLE", "supported": False, "complete": False, "reason": "platform_missing"}
    extents: set[tuple[int, int]] = set()
    with path.open("rb", buffering=0) as stream:
        fd = stream.fileno()
        for start, length in ranges:
            end = checked_add(start, length)
            cursor = start
            while cursor < end:
                try:
                    data = os.lseek(fd, cursor, os.SEEK_DATA)
                except OSError as error:
                    return {"method": "SEEK_DATA_SEEK_HOLE", "supported": False, "complete": False, "errno": error.errno}
                if data > cursor:
                    return {"method": "SEEK_DATA_SEEK_HOLE", "supported": True, "complete": False, "hole": [cursor, data]}
                hole = os.lseek(fd, data, os.SEEK_HOLE)
                extents.add((data, hole))
                if hole <= cursor:
                    raise ValueError("extent query made no progress")
                cursor = min(hole, end)
    stat = path.stat()
    lengths = sorted(end - start for start, end in extents)
    return {
        "method": "SEEK_DATA_SEEK_HOLE", "supported": True, "complete": True,
        "st_size": stat.st_size, "st_blocks_512": stat.st_blocks * 512,
        "extent_count": len(extents), "extent_lengths": lengths,
    }


def preflight(path: Path, declared_max_new_bytes: int = 64 * GIB) -> dict[str, object]:
    path.mkdir(parents=True, exist_ok=True)
    stats = os.statvfs(path)
    capacity = checked_mul(stats.f_blocks, stats.f_frsize)
    available = checked_mul(stats.f_bavail, stats.f_frsize)
    reserve = max(96 * GIB, (capacity + 9) // 10)
    required = checked_add(declared_max_new_bytes, reserve)
    return {
        "schema_version": "phase12p-storage-preflight-v1",
        "path": str(path.resolve()), "capacity_bytes": capacity, "available_bytes": available,
        "available_inodes": stats.f_favail, "declared_max_new_bytes": declared_max_new_bytes,
        "post_high_water_reserve_bytes": reserve, "required_available_bytes": required,
        "persistent_ceiling_bytes": 56 * GIB, "temporary_ceiling_bytes": 8 * GIB,
        "absolute_high_water_bytes": 64 * GIB, "passed": available >= required,
        "compression": "not_proven_inactive", "deduplication": "not_proven_inactive",
        "reflink_cow": "not_accepted_as_physical_backing",
    }

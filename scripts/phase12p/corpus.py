#!/usr/bin/env python3
"""Generate and completely verify deterministic Phase 12P Layout A and B stores."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import BinaryIO

from common import (
    CONFIG_SHA256, FULL_SCALE, LAYOUT_A_VERSION, LAYOUT_B_VERSION, PAYLOAD_VERSION,
    PROJECTIONS, ROUTE_VERSION, SEED, SOURCE_REVISION, Journal, Scale, canonical_bytes,
    fsync_file, logical_projection_offset, payload_chunks, preflight, route_identity,
    seek_extent_coverage, selected_experts, sha256_bytes, sha256_file, validate_journal_chains,
    write_json,
)

HEADER_SIZE = 4096
INDEX = struct.Struct("<II8Q32s24x")
assert INDEX.size == 128


def _pwrite_all(fd: int, payload: bytes, offset: int) -> None:
    cursor = 0
    while cursor < len(payload):
        written = os.pwrite(fd, payload[cursor:], offset + cursor)
        if written <= 0:
            raise OSError("zero-progress pwrite")
        cursor += written


def _pread_all(fd: int, length: int, offset: int) -> bytes:
    output = bytearray()
    while len(output) < length:
        try:
            block = os.pread(fd, length - len(output), offset + len(output))
        except InterruptedError:
            continue
        if not block:
            raise EOFError(f"zero progress before requested length at {offset + len(output)}")
        output.extend(block)
    return bytes(output)


def _hash_span(fd: int, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    cursor = 0
    while cursor < length:
        block = _pread_all(fd, min(1 << 20, length - cursor), offset + cursor)
        digest.update(block)
        cursor += len(block)
    return digest.hexdigest()


def _atomic_seal(directory: Path, manifest: Path) -> None:
    marker = {"schema_version": "phase12p-seal-v1", "manifest_sha256": sha256_file(manifest)}
    temporary = directory / ".sealed.tmp"
    temporary.write_bytes(canonical_bytes(marker))
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, directory / "SEALED.json")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _layout_definition(scale: Scale, route_sha256: str, layout: str) -> dict[str, object]:
    return {
        "schema_version": layout, "source_revision": SOURCE_REVISION,
        "config_sha256": CONFIG_SHA256, "payload_generator": PAYLOAD_VERSION,
        "route_schema": ROUTE_VERSION, "route_sha256": route_sha256, "seed": SEED,
        "projection_order": list(PROJECTIONS), "projection_bytes": scale.projection_bytes,
        "bundle_bytes": scale.bundle_bytes, "logical_bytes": scale.logical_bytes,
        "materialized_bundles": scale.materialized_bundles, "useful_bytes": scale.useful_bytes,
    }


def generate(root: Path, scale: Scale, *, enforce_full_preflight: bool = True) -> dict[str, object]:
    """Generate both layouts in an empty directory without a second payload copy."""
    if root.exists() and any(root.iterdir()):
        raise ValueError("generation directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    if scale == FULL_SCALE:
        scale.validate_full()
        gate = preflight(root)
        write_json(root / "storage-preflight.json", gate)
        if enforce_full_preflight and not gate["passed"]:
            raise RuntimeError("storage reserve gate failed before logical store creation")

    route, route_sha = route_identity(scale)
    write_json(root / "route-corpus.json", route)
    a_dir, b_dir = root / "layout-a", root / "layout-b"
    a_dir.mkdir(); b_dir.mkdir()
    a_path, b_path = a_dir / "projection-spans.bin", b_dir / "contiguous-experts.bin"
    keys = sorted((layer, expert) for layer in range(scale.layers) for expert in selected_experts(scale, layer))
    if scale.bundle_bytes % HEADER_SIZE:
        raise ValueError("Layout B bundle size must be 4 KiB aligned")

    a_file = a_path.open("w+b", buffering=0)
    b_file = b_path.open("w+b", buffering=0)
    a_journal, b_journal = Journal(a_dir / "journal.jsonl", LAYOUT_A_VERSION), Journal(b_dir / "journal.jsonl", LAYOUT_B_VERSION)
    entries: list[dict[str, object]] = []
    aggregate = hashlib.sha256()
    try:
        os.ftruncate(a_file.fileno(), scale.logical_bytes)
        os.ftruncate(b_file.fileno(), HEADER_SIZE + scale.useful_bytes)
        for ordinal, (layer, expert) in enumerate(keys):
            a_journal.append(layer, expert, "STARTED")
            b_journal.append(layer, expert, "STARTED")
            projection_hashes: list[str] = []
            bundle_digest = hashlib.sha256()
            b_offset = HEADER_SIZE + ordinal * scale.bundle_bytes
            source_offsets: list[int] = []
            for projection in range(3):
                a_offset = logical_projection_offset(scale, layer, expert, projection)
                source_offsets.append(a_offset)
                projection_digest = hashlib.sha256()
                cursor = 0
                for block in payload_chunks(layer, expert, projection, scale.projection_bytes):
                    _pwrite_all(a_file.fileno(), block, a_offset + cursor)
                    _pwrite_all(b_file.fileno(), block, b_offset + projection * scale.projection_bytes + cursor)
                    projection_digest.update(block); bundle_digest.update(block); aggregate.update(block)
                    cursor += len(block)
                projection_hashes.append(projection_digest.hexdigest())
            bundle_sha = bundle_digest.hexdigest()
            fsync_file(a_file); fsync_file(b_file)
            a_journal.append(layer, expert, "WRITTEN", bundle_sha)
            b_journal.append(layer, expert, "WRITTEN", bundle_sha)
            a_hashes = [_hash_span(a_file.fileno(), source_offsets[p], scale.projection_bytes) for p in range(3)]
            b_hash = _hash_span(b_file.fileno(), b_offset, scale.bundle_bytes)
            if a_hashes != projection_hashes or b_hash != bundle_sha:
                raise ValueError(f"reread mismatch for {(layer, expert)}")
            a_journal.append(layer, expert, "REREAD_OK", bundle_sha)
            b_journal.append(layer, expert, "REREAD_OK", bundle_sha)
            entries.append({
                "layer": layer, "expert": expert, "ordinal": ordinal,
                "layout_a_offsets": source_offsets, "layout_b_offset": b_offset,
                "projection_bytes": scale.projection_bytes, "bundle_bytes": scale.bundle_bytes,
                "projection_sha256": projection_hashes, "bundle_sha256": bundle_sha,
                "exact_adjacency": source_offsets[1] == source_offsets[0] + scale.projection_bytes and source_offsets[2] == source_offsets[1] + scale.projection_bytes,
                "operations_uncoalesced": 3, "operations_exact_coalesced": 1,
            })
            a_journal.append(layer, expert, "COMMITTED", bundle_sha)
            b_journal.append(layer, expert, "COMMITTED", bundle_sha)

        index_offset = HEADER_SIZE + scale.useful_bytes
        index_digest = hashlib.sha256()
        b_file.seek(index_offset)
        for entry in entries:
            packed = INDEX.pack(
                entry["layer"], entry["expert"], entry["layout_b_offset"], scale.bundle_bytes,
                *entry["layout_a_offsets"], scale.projection_bytes, scale.projection_bytes,
                scale.projection_bytes, bytes.fromhex(entry["bundle_sha256"]),
            )
            b_file.write(packed); index_digest.update(packed)
        fsync_file(b_file)
        header_basis = {
            "magic": "K3P12PCONTIG", "version": LAYOUT_B_VERSION, "byte_order": "little",
            "source_revision": SOURCE_REVISION, "config_sha256": CONFIG_SHA256,
            "generator": PAYLOAD_VERSION, "route_sha256": route_sha, "record_count": len(entries),
            "useful_bytes": scale.useful_bytes, "index_offset": index_offset,
            "index_length": len(entries) * INDEX.size, "index_sha256": index_digest.hexdigest(),
        }
        header = dict(header_basis); header["header_basis_sha256"] = sha256_bytes(canonical_bytes(header_basis))
        encoded = canonical_bytes(header)
        if len(encoded) > HEADER_SIZE:
            raise ValueError("Layout B header exceeds 4 KiB")
        _pwrite_all(b_file.fileno(), encoded + b"\0" * (HEADER_SIZE - len(encoded)), 0)
        fsync_file(b_file)
    finally:
        a_journal.close(); b_journal.close(); a_file.close(); b_file.close()

    if validate_journal_chains(Journal.read_valid(a_dir / "journal.jsonl")) != set(keys):
        raise ValueError("Layout A journal is not completely committed")
    if validate_journal_chains(Journal.read_valid(b_dir / "journal.jsonl")) != set(keys):
        raise ValueError("Layout B journal is not completely committed")
    write_json(a_dir / "index.json", {"schema_version": "phase12p-layout-a-index-v1", "entries": entries})
    a_extent = seek_extent_coverage(a_path, ((entry["layout_a_offsets"][p], scale.projection_bytes) for entry in entries for p in range(3)))
    b_extent = seek_extent_coverage(b_path, ((HEADER_SIZE, scale.useful_bytes + len(entries) * INDEX.size),))
    a_manifest = _layout_definition(scale, route_sha, LAYOUT_A_VERSION) | {
        "file": a_path.name, "index": "index.json", "journal": "journal.jsonl", "extent_proof": a_extent,
        "aggregate_useful_sha256": aggregate.hexdigest(), "entries_sha256": sha256_bytes(canonical_bytes(entries)),
    }
    b_manifest = _layout_definition(scale, route_sha, LAYOUT_B_VERSION) | {
        "file": b_path.name, "header_size": HEADER_SIZE, "index_entry_size": INDEX.size,
        "journal": "journal.jsonl", "extent_proof": b_extent,
        "aggregate_useful_sha256": aggregate.hexdigest(), "reverse_compared_to_layout_a": True,
    }
    write_json(a_dir / "manifest.json", a_manifest); write_json(b_dir / "manifest.json", b_manifest)
    _atomic_seal(a_dir, a_dir / "manifest.json"); _atomic_seal(b_dir, b_dir / "manifest.json")
    summary = {
        "schema_version": "phase12p-generation-v1", "scale": scale.__dict__,
        "route_sha256": route_sha, "layout_a_definition_sha256": sha256_bytes(canonical_bytes(_layout_definition(scale, route_sha, LAYOUT_A_VERSION))),
        "layout_b_definition_sha256": sha256_bytes(canonical_bytes(_layout_definition(scale, route_sha, LAYOUT_B_VERSION))),
        "aggregate_useful_sha256": aggregate.hexdigest(), "record_count": len(entries),
        "layout_a_manifest_sha256": sha256_file(a_dir / "manifest.json"),
        "layout_b_manifest_sha256": sha256_file(b_dir / "manifest.json"),
    }
    write_json(root / "generation.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", action="store_true", help="bounded 2-layer fixture, never decision evidence")
    args = parser.parse_args()
    scale = Scale(layers=2, experts=32, selected=16, projection_bytes=4096, tokens=4) if args.fixture else FULL_SCALE
    result = generate(args.output, scale)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

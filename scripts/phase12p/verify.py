#!/usr/bin/env python3
"""Independent semantic verifier for Phase 12P generated corpora."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from common import (
    CONFIG_SHA256, LAYOUT_A_VERSION, LAYOUT_B_VERSION, PAYLOAD_VERSION, ROUTE_VERSION,
    SOURCE_REVISION, Journal, Scale, canonical_bytes, logical_projection_offset,
    payload_chunks, route_identity, seek_extent_coverage, selected_experts, sha256_bytes,
    sha256_file, validate_journal_chains,
)
from corpus import HEADER_SIZE, INDEX, _pread_all


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def _verify_seal(directory: Path) -> None:
    seal = _load_json(directory / "SEALED.json")
    if seal != {"schema_version": "phase12p-seal-v1", "manifest_sha256": sha256_file(directory / "manifest.json")}:
        raise ValueError(f"invalid seal: {directory}")


def _expected_projection_sha(layer: int, expert: int, projection: int, length: int) -> str:
    digest = hashlib.sha256()
    for block in payload_chunks(layer, expert, projection, length):
        digest.update(block)
    return digest.hexdigest()


def verify(root: Path) -> dict[str, object]:
    generation = _load_json(root / "generation.json")
    scale = Scale(**generation["scale"])
    route = _load_json(root / "route-corpus.json")
    expected_route, route_sha = route_identity(scale)
    if route != expected_route or route_sha != generation["route_sha256"]:
        raise ValueError("route identity mismatch")
    if route["schema_version"] != ROUTE_VERSION:
        raise ValueError("route schema mismatch")

    a_dir, b_dir = root / "layout-a", root / "layout-b"
    _verify_seal(a_dir); _verify_seal(b_dir)
    a_manifest, b_manifest = _load_json(a_dir / "manifest.json"), _load_json(b_dir / "manifest.json")
    for manifest, version in ((a_manifest, LAYOUT_A_VERSION), (b_manifest, LAYOUT_B_VERSION)):
        if manifest["schema_version"] != version or manifest["source_revision"] != SOURCE_REVISION:
            raise ValueError("layout source/schema mismatch")
        if manifest["config_sha256"] != CONFIG_SHA256 or manifest["payload_generator"] != PAYLOAD_VERSION:
            raise ValueError("layout generator/config mismatch")
        if manifest["route_sha256"] != route_sha:
            raise ValueError("layout route mismatch")

    entries = _load_json(a_dir / "index.json")["entries"]
    keys = sorted((layer, expert) for layer in range(scale.layers) for expert in selected_experts(scale, layer))
    if [(entry["layer"], entry["expert"]) for entry in entries] != keys:
        raise ValueError("index ordering or coverage mismatch")
    for directory, version in ((a_dir, LAYOUT_A_VERSION), (b_dir, LAYOUT_B_VERSION)):
        records = Journal.read_valid(directory / "journal.jsonl")
        if validate_journal_chains(records) != set(keys) or any(record["layout"] != version for record in records):
            raise ValueError("journal chain mismatch")

    a_path, b_path = a_dir / "projection-spans.bin", b_dir / "contiguous-experts.bin"
    if a_path.stat().st_size != scale.logical_bytes:
        raise ValueError("Layout A logical EOF mismatch")
    expected_b_size = HEADER_SIZE + scale.useful_bytes + len(entries) * INDEX.size
    if b_path.stat().st_size != expected_b_size:
        raise ValueError("Layout B EOF mismatch")

    aggregate = hashlib.sha256()
    with a_path.open("rb", buffering=0) as a_file, b_path.open("rb", buffering=0) as b_file:
        header_raw = _pread_all(b_file.fileno(), HEADER_SIZE, 0)
        try:
            header = json.loads(header_raw.rstrip(b"\0"))
        except ValueError as error:
            raise ValueError("invalid Layout B header") from error
        basis = dict(header); basis_sha = basis.pop("header_basis_sha256", None)
        if basis_sha != sha256_bytes(canonical_bytes(basis)) or header["magic"] != "K3P12PCONTIG":
            raise ValueError("Layout B header checksum/magic mismatch")
        index_bytes = _pread_all(b_file.fileno(), len(entries) * INDEX.size, header["index_offset"])
        if sha256_bytes(index_bytes) != header["index_sha256"]:
            raise ValueError("Layout B index checksum mismatch")
        for ordinal, entry in enumerate(entries):
            layer, expert = entry["layer"], entry["expert"]
            packed = INDEX.unpack_from(index_bytes, ordinal * INDEX.size)
            if packed[:2] != (layer, expert) or packed[2] != entry["layout_b_offset"] or packed[3] != scale.bundle_bytes:
                raise ValueError("Layout B fixed index identity mismatch")
            if tuple(packed[4:7]) != tuple(entry["layout_a_offsets"]) or tuple(packed[7:10]) != (scale.projection_bytes,) * 3:
                raise ValueError("Layout B component/source span mismatch")
            if packed[10].hex() != entry["bundle_sha256"]:
                raise ValueError("Layout B index bundle checksum mismatch")
            bundle_digest = hashlib.sha256()
            for projection in range(3):
                expected_offset = logical_projection_offset(scale, layer, expert, projection)
                if entry["layout_a_offsets"][projection] != expected_offset:
                    raise ValueError("Layout A logical offset mismatch")
                a_bytes = _pread_all(a_file.fileno(), scale.projection_bytes, expected_offset)
                b_offset = entry["layout_b_offset"] + projection * scale.projection_bytes
                b_bytes = _pread_all(b_file.fileno(), scale.projection_bytes, b_offset)
                if a_bytes != b_bytes:
                    raise ValueError("Layout A/B byte mismatch")
                observed = sha256_bytes(a_bytes)
                expected = _expected_projection_sha(layer, expert, projection, scale.projection_bytes)
                if observed != expected or observed != entry["projection_sha256"][projection]:
                    raise ValueError("payload/projection checksum mismatch")
                bundle_digest.update(a_bytes); aggregate.update(a_bytes)
            if bundle_digest.hexdigest() != entry["bundle_sha256"]:
                raise ValueError("complete bundle checksum mismatch")

    a_extents = seek_extent_coverage(a_path, ((entry["layout_a_offsets"][p], scale.projection_bytes) for entry in entries for p in range(3)))
    b_extents = seek_extent_coverage(b_path, ((HEADER_SIZE, scale.useful_bytes + len(entries) * INDEX.size),))
    if not a_extents.get("complete") or not b_extents.get("complete"):
        raise ValueError("planned range contains a hole or extent proof is unavailable")
    if aggregate.hexdigest() != generation["aggregate_useful_sha256"]:
        raise ValueError("aggregate useful-byte identity mismatch")
    return {
        "schema_version": "phase12p-verification-v1", "status": "PASS",
        "record_count": len(entries), "verified_useful_bytes_per_layout": scale.useful_bytes,
        "aggregate_useful_sha256": aggregate.hexdigest(), "route_sha256": route_sha,
        "layout_a_extent_proof": a_extents, "layout_b_extent_proof": b_extents,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.root)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

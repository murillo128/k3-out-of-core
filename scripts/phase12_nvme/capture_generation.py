#!/usr/bin/env python3
"""Capture compact, checksum-addressed identity for one verified generation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
from common import Scale, seek_extent_coverage  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path, root: Path) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(root)),
        "size": stat.st_size,
        "allocated_bytes": stat.st_blocks * 512,
        "sha256": sha256_file(path),
    }


def physical_identity(path: Path, root: Path, content_sha256: str) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(root)),
        "logical_size": stat.st_size,
        "allocated_bytes": stat.st_blocks * 512,
        "planned_content_sha256": content_sha256,
    }


def compact_extent(proof: dict[str, object]) -> dict[str, object]:
    lengths = [int(value) for value in proof.get("extent_lengths", [])]
    output = {key: value for key, value in proof.items() if key != "extent_lengths"}
    output["extent_length_distribution"] = {
        "count": len(lengths),
        "minimum": min(lengths) if lengths else 0,
        "maximum": max(lengths) if lengths else 0,
        "histogram": {str(key): value for key, value in sorted(Counter(lengths).items())},
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.generation.resolve()
    generation = json.loads((root / "generation.json").read_text())
    verification = json.loads((root / "verification.json").read_text())
    if verification.get("status") != "PASS":
        raise ValueError("generation is not completely verified")
    a_manifest = json.loads((root / "layout-a/manifest.json").read_text())
    b_manifest = json.loads((root / "layout-b/manifest.json").read_text())
    scale = Scale(**generation["scale"])
    entries = json.loads((root / "layout-a/index.json").read_text())["entries"]
    verification["layout_a_extent_proof"] = seek_extent_coverage(
        root / "layout-a/projection-spans.bin",
        ((entry["layout_a_offsets"][projection], scale.projection_bytes) for entry in entries for projection in range(3)),
    )
    verification["layout_b_extent_proof"] = seek_extent_coverage(
        root / "layout-b/contiguous-experts.bin",
        ((4096, scale.useful_bytes + len(entries) * 128),),
    )
    if not verification["layout_a_extent_proof"].get("complete") or not verification["layout_b_extent_proof"].get("complete"):
        raise ValueError("refreshed extent proof failed")
    with (root / "layout-b/contiguous-experts.bin").open("rb") as stream:
        b_header = json.loads(stream.read(4096).rstrip(b"\0"))
    retained = [
        root / "generation.json",
        root / "verification.json",
        root / "storage-preflight.json",
        root / "route-corpus.json",
        root / "layout-a/manifest.json",
        root / "layout-a/index.json",
        root / "layout-a/journal.jsonl",
        root / "layout-a/SEALED.json",
        root / "layout-b/manifest.json",
        root / "layout-b/journal.jsonl",
        root / "layout-b/SEALED.json",
    ]
    files = [identity(path, root) for path in retained]
    stores = [
        physical_identity(root / "layout-a/projection-spans.bin", root, generation["aggregate_useful_sha256"]),
        physical_identity(root / "layout-b/contiguous-experts.bin", root, generation["aggregate_useful_sha256"]),
    ]
    du = subprocess.run(["du", "--bytes", "--summarize", str(root)], text=True, capture_output=True, check=True)
    allocated_du = subprocess.run(["du", "--block-size=1", "--summarize", str(root)], text=True, capture_output=True, check=True)
    document = {
        "schema_version": "phase12-nvme-generation-capture-v1",
        "name": args.name,
        "generation": generation,
        "verification": {
            "status": verification["status"],
            "record_count": verification["record_count"],
            "verified_useful_bytes_per_layout": verification["verified_useful_bytes_per_layout"],
            "aggregate_useful_sha256": verification["aggregate_useful_sha256"],
            "route_sha256": verification["route_sha256"],
            "layout_a_extent_proof": compact_extent(verification["layout_a_extent_proof"]),
            "layout_b_extent_proof": compact_extent(verification["layout_b_extent_proof"]),
        },
        "layout_identity": {
            "layout_a_entries_sha256": a_manifest["entries_sha256"],
            "layout_a_manifest_sha256": sha256_file(root / "layout-a/manifest.json"),
            "layout_b_manifest_sha256": sha256_file(root / "layout-b/manifest.json"),
            "layout_b_index_sha256": b_header["index_sha256"],
        },
        "metadata_files": files,
        "physical_stores": stores,
        "du_apparent_bytes": int(du.stdout.split()[0]),
        "du_allocated_bytes": int(allocated_du.stdout.split()[0]),
        "swap_used_bytes": int(next((line.split()[3] for line in subprocess.check_output(["swapon", "--show", "--bytes", "--noheadings"], text=True).splitlines() if line.split()), "0")),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "name": args.name,
        "status": verification["status"],
        "aggregate_useful_sha256": verification["aggregate_useful_sha256"],
        "route_sha256": verification["route_sha256"],
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

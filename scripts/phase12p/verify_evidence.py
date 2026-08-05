#!/usr/bin/env python3
"""Verify the checksum-addressed Phase 12P blocked handoff."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import FULL_SCALE, PAYLOAD_VERSION, ROUTE_VERSION, route_identity, sha256_file

ROOT = Path(__file__).resolve().parents[2]


def verify(results: Path) -> None:
    manifest = json.loads((results / "phase12p-manifest.json").read_text())
    if manifest["phase12p_schema_version"] != "phase12p-manifest-v1" or manifest["execution_profile"] != "STANDARD":
        raise ValueError("manifest schema/profile mismatch")
    if manifest["status"] != "blocked-review-candidate" or manifest["preliminary_screening_disposition"] != "SCREENING_BLOCKED":
        raise ValueError("blocked manifest disposition mismatch")
    if manifest["phase12p_final_project_head"] is not None:
        raise ValueError("unpublished blocked manifest must not invent a final head")
    if manifest["generator_version"] != PAYLOAD_VERSION or manifest["route_schema_version"] != ROUTE_VERSION:
        raise ValueError("generator/route version mismatch")
    if manifest["route_artifact_sha256"] != route_identity(FULL_SCALE)[1]:
        raise ValueError("full-scale route identity mismatch")
    block = manifest["blocking_evidence"]
    if block["status"] != "BLOCKED_BEFORE_CORPUS" or block["thresholds"]["passed"]:
        raise ValueError("storage block is not established")
    threshold = block["thresholds"]
    if threshold["available_bytes"] >= threshold["required_available_bytes"]:
        raise ValueError("blocked disposition conflicts with capacity arithmetic")
    if any(manifest[field] is not None for field in ("corpus_identity", "layout_a_definition_sha256", "layout_b_definition_sha256", "per_layout_physical_allocation_and_extent_proof", "best_fair_buffered_pread_cell_per_layout_and_route_class")):
        raise ValueError("blocked manifest invents unexecuted full-scale evidence")
    checksum_index = json.loads((results / "phase12p-checksums.json").read_text())
    if sha256_file(results / "phase12p-checksums.json") != manifest["checksum_index_sha256"]:
        raise ValueError("checksum-index identity mismatch")
    for item in checksum_index["files"]:
        path = ROOT / item["path"]
        if path.stat().st_size != item["size"] or sha256_file(path) != item["sha256"]:
            raise ValueError(f"artifact identity mismatch: {item['path']}")
    archive = ROOT / manifest["raw_archive_uri"]
    if archive.stat().st_size != manifest["raw_archive_size"] or sha256_file(archive) != manifest["raw_archive_sha256"]:
        raise ValueError("raw archive identity mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args(); verify(args.results.resolve()); print("phase12p blocked evidence verified"); return 0


if __name__ == "__main__":
    raise SystemExit(main())

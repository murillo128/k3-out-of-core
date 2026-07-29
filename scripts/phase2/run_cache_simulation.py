#!/usr/bin/env python3
"""Replay a Phase 2 route trace against deterministic cache baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cache_simulator import canonical_json, sha256_file, simulate_manifest
from route_trace import read_route_trace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--storage-map", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    trace = read_route_trace(args.trace)
    storage_map = json.loads(args.storage_map.read_text())
    manifest = json.loads(args.manifest.read_text())
    result = simulate_manifest(
        trace,
        storage_map,
        manifest,
        sha256_file(args.trace),
        sha256_file(args.storage_map),
        sha256_file(args.manifest),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(result))
    print(
        canonical_json({"output": str(args.output), "scenarios": len(result["scenarios"])}),
        end="",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

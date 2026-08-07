#!/usr/bin/env python3
"""Fail closed unless two full clean generations share all required identities."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


PATHS = (
    ("generation", "scale"),
    ("generation", "route_sha256"),
    ("generation", "layout_a_definition_sha256"),
    ("generation", "layout_b_definition_sha256"),
    ("generation", "aggregate_useful_sha256"),
    ("generation", "record_count"),
    ("verification", "status"),
    ("verification", "record_count"),
    ("verification", "verified_useful_bytes_per_layout"),
    ("verification", "aggregate_useful_sha256"),
    ("verification", "route_sha256"),
    ("layout_identity", "layout_a_entries_sha256"),
    ("layout_identity", "layout_b_index_sha256"),
)


def lookup(document: dict[str, object], path: tuple[str, ...]) -> object:
    value: object = document
    for component in path:
        if not isinstance(value, dict):
            raise ValueError(f"non-object at {'.'.join(path)}")
        value = value[component]
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    first = json.loads(args.first.read_text())
    second = json.loads(args.second.read_text())
    comparisons = []
    passed = True
    for path in PATHS:
        left, right = lookup(first, path), lookup(second, path)
        equal = left == right
        passed = passed and equal
        comparisons.append({"field": ".".join(path), "first": left, "second": right, "equal": equal})
    first_stores = [(item["logical_size"], item["planned_content_sha256"]) for item in first["physical_stores"]]
    second_stores = [(item["logical_size"], item["planned_content_sha256"]) for item in second["physical_stores"]]
    stores_equal = first_stores == second_stores
    passed = passed and stores_equal
    comparisons.append({"field": "physical_stores.logical_size_and_content", "first": first_stores, "second": second_stores, "equal": stores_equal})
    output = {
        "schema_version": "phase12-nvme-clean-generation-comparison-v1",
        "status": "PASS" if passed else "FAIL",
        "first": str(args.first),
        "second": str(args.second),
        "comparisons": comparisons,
        "manifest_hash_note": "Layout A manifest hashes may differ only when extent-summary representation changes; the compared layout definition, full entry hash, aggregate payload, sizes, and complete proofs are authoritative.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": output["status"], "comparisons": len(comparisons), "output": str(args.output)}, sort_keys=True))
    if not passed:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare the Checkpoint A S0 legacy and explicit Mode-C runs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, required=True)
    parser.add_argument("--explicit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main() -> None:
    args = parse_args()
    legacy = load(args.legacy)
    explicit = load(args.explicit)
    identity_keys = ("prompt_ids", "generated_ids", "generated_text", "logits_fnv64", "routes")
    legacy_identity = {key: legacy[key] for key in identity_keys}
    explicit_identity = {key: explicit[key] for key in identity_keys}
    structure_equal = legacy["role_path_structure"] == explicit["role_path_structure"]
    identity_equal = legacy_identity == explicit_identity
    legacy_role = legacy["expert_roles"]
    explicit_role = explicit["expert_roles"]
    same_physical_role = {
        key: legacy_role[key] for key in ("shape", "resident", "experts", "total_hot_slots")
    } == {
        key: explicit_role[key] for key in ("shape", "resident", "experts", "total_hot_slots")
    }
    exact_provider_capacity = all(
        len(run.get("multi_gpu", {}).get("devices", [])) == 1 and
        run["multi_gpu"]["devices"][0].get("hot_requested_slots") == 268 and
        run["multi_gpu"]["devices"][0].get("hot_effective_slots") == 268
        for run in (legacy, explicit)
    )
    lifecycle_keys = (
        "active_background_flights", "current_hot_pins", "cold_current_transfer_refs",
        "cold_current_request_refs", "cold_current_cpu_execution_refs",
    )
    clean_lifecycle = all(
        all(run.get("lifecycle", {}).get(key, 0) == 0 for key in lifecycle_keys)
        for run in (legacy, explicit)
    )
    passed = all((
        legacy.get("status") == "pass",
        explicit.get("status") == "pass",
        not legacy_role.get("explicit"),
        bool(explicit_role.get("explicit")),
        identity_equal,
        structure_equal,
        same_physical_role,
        exact_provider_capacity,
        clean_lifecycle,
    ))
    result = {
        "schema_version": "issue65-checkpoint-a-compliance-v1",
        "status": "pass" if passed else "fail",
        "identity_equal": identity_equal,
        "identity_sha256": sha256(legacy_identity),
        "generated_tokens_per_process": len(legacy["generated_ids"]),
        "logits_digests_per_process": len(legacy["logits_fnv64"]),
        "route_records_per_process": len(legacy["routes"]),
        "role_path_structure_equal": structure_equal,
        "role_path_structure_sha256": sha256(legacy["role_path_structure"]),
        "role_path_structure": legacy["role_path_structure"],
        "same_physical_role": same_physical_role,
        "exact_provider_capacity": exact_provider_capacity,
        "clean_lifecycle": clean_lifecycle,
        "legacy_resolved_role": legacy_role,
        "explicit_resolved_role": explicit_role,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    if not passed:
        raise SystemExit("Checkpoint A Mode-C comparison failed")


if __name__ == "__main__":
    main()

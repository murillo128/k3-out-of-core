#!/usr/bin/env python3
"""Adapt immutable Phase 2 route records to canonical Phase 9 replay input.

The adapter reuses only the stable Phase 2 binary parser.  Each Phase 2 atomic
expert request becomes a one-demand remap checkpoint.  That preserves the
published Phase 2 request order exactly while satisfying Phase 9's stable-key
rule trivially; Phase 2 records already reject duplicate selected experts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase2"))
from route_trace import read_route_trace  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from cache_policy_simulator import ReplayError, canonical_json, validate_config  # noqa: E402


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_storage_map(path: Path, trace: dict[str, Any]) -> tuple[dict[tuple[int, int], int], dict[str, Any]]:
    value = json.loads(path.read_text())
    if value.get("schema_version") != "expert-storage-map-v1":
        raise ReplayError("unsupported Phase 2 storage map")
    model = value.get("model", {})
    header = trace["header"]
    identities = {
        "name": (model.get("name"), header.get("model_name")),
        "size": (model.get("size"), header.get("model_size")),
        "sha256": (model.get("sha256"), header.get("model_sha256")),
        "source_revision": (model.get("source_revision"), header.get("model_source_revision")),
        "published_gguf_revision": (
            model.get("published_gguf_revision"), header.get("published_gguf_revision")
        ),
    }
    mismatch = [name for name, pair in identities.items() if pair[0] != pair[1]]
    if mismatch:
        raise ReplayError(f"trace/storage identity mismatch: {', '.join(mismatch)}")
    sizes: dict[tuple[int, int], int] = {}
    for entry in value.get("entries", []):
        key = (entry.get("layer"), entry.get("expert_id"))
        size = entry.get("atomic_bundle_bytes")
        if key in sizes or not all(isinstance(item, int) and item >= 0 for item in key):
            raise ReplayError("duplicate or invalid storage-map key")
        if not isinstance(size, int) or size <= 0:
            raise ReplayError("invalid atomic bundle size")
        sizes[key] = size
    if not sizes or len(set(sizes.values())) != 1:
        raise ReplayError("Phase 9 replay input requires one physical footprint per topology")
    return sizes, value


def adapt_phase2(
    trace_path: Path,
    storage_map_path: Path,
    hot_slots: int,
    cold_slots: int,
    hot_config: dict[str, Any],
    cold_config: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not 0 < hot_slots <= cold_slots:
        raise ReplayError("inclusive capacities require 0 < hot slots <= cold slots")
    trace = read_route_trace(trace_path)
    sizes, storage_map = load_storage_map(storage_map_path, trace)
    validate_config(hot_config, "HOT")
    validate_config(cold_config, "COLD")
    layers = sorted({key[0] for key in sizes})
    experts_per_layer = max(key[1] for key in sizes) + 1
    footprint = next(iter(sizes.values()))

    by_request: dict[int, list[dict[str, Any]]] = {}
    for record in trace["records"]:
        target = by_request.setdefault(record["request_ordinal"], [])
        if len(record["selected_experts"]) != len(set(record["selected_experts"])):
            raise ReplayError("Phase 2 record contains duplicate expert lanes")
        for expert in record["selected_experts"]:
            key = (record["layer"], expert)
            if key not in sizes:
                raise ReplayError(f"trace references absent storage key {key}")
            target.append({
                "checkpoint_ordinal": len(target) + 1,
                "ubatch_ordinal": record["ubatch_ordinal"],
                "phase": record["phase"],
                "demands": [{
                    "layer": key[0],
                    "expert": key[1],
                    "occurrence_count": 1,
                    "logical_payload_bytes": sizes[key],
                    "hot_admission": "MANDATORY_CURRENT_OUTPUT",
                }],
            })
    request_ids = sorted(by_request)
    if request_ids != list(range(request_ids[0], request_ids[0] + len(request_ids))):
        raise ReplayError("Phase 2 request ordinals are not contiguous")
    requests = [
        {
            "request_ordinal": index + 1,
            "checkpoints": by_request[source_ordinal],
            "outcome": "SUCCESS",
        }
        for index, source_ordinal in enumerate(request_ids)
    ]
    replay_input = {
        "schema_version": "cache-policy-replay-input-v1",
        "topology": {
            "routed_layers": layers,
            "experts_per_layer": experts_per_layer,
            "physical_slot_footprint_bytes": footprint,
        },
        "hot": {"slots": hot_slots, "config": hot_config},
        "cold": {"slots": cold_slots, "config": cold_config},
        "requests": requests,
    }
    lineage = {
        "schema_version": "phase9-phase2-adapter-lineage-v1",
        "adapter_semantics": "one Phase 2 atomic expert request per one-demand Phase 9 checkpoint",
        "trace": {
            "path": str(trace_path),
            "sha256": sha256_file(trace_path),
            "checksum": trace["checksum"],
            "records": len(trace["records"]),
            "atomic_expert_requests": sum(len(record["selected_experts"]) for record in trace["records"]),
            "run_id": trace["header"]["run_id"],
        },
        "storage_map": {
            "path": str(storage_map_path),
            "sha256": sha256_file(storage_map_path),
            "model": storage_map["model"],
            "entries": len(sizes),
            "atomic_bundle_bytes": footprint,
        },
    }
    return replay_input, lineage


def lru_config(scope: str = "GLOBAL") -> dict[str, Any]:
    return {
        "schema_version": "cache-policy-config-v1",
        "policy": "LRU",
        "scope": scope,
        "slru_protected_ratio_bps": 0,
        "admission": "ALWAYS",
        "admission_window_events": 0,
        "lfu_aging_interval_events": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--storage-map", type=Path, required=True)
    parser.add_argument("--hot-slots", type=int, required=True)
    parser.add_argument("--cold-slots", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--lineage-output", type=Path)
    arguments = parser.parse_args()
    replay_input, lineage = adapt_phase2(
        arguments.trace, arguments.storage_map, arguments.hot_slots, arguments.cold_slots,
        lru_config(), lru_config(),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(replay_input))
    if arguments.lineage_output:
        arguments.lineage_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.lineage_output.write_text(canonical_json(lineage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Derive exact Phase 9 working sets and legal budget boundaries."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import (  # noqa: E402
    canonical_json, distribution, file_identity, host_safe_ceiling, legal_budget_grid,
)


def demand_events(capture: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for tier in ("hot", "cold"):
        events.extend(event for event in capture[tier]["events"] if event["type"] == "DEMAND")
    # Cold and hot see overlapping hierarchy demands. Prefer cold when active, otherwise hot.
    cold = [event for event in capture["cold"]["events"] if event["type"] == "DEMAND"]
    hot = [event for event in capture["hot"]["events"] if event["type"] == "DEMAND"]
    return cold or hot


def reuse_distances(events: list[dict[str, Any]]) -> list[int]:
    stack: list[tuple[int, int]] = []
    result: list[int] = []
    for event in events:
        key = (event["layer"], event["expert"])
        if key in stack:
            index = stack.index(key)
            result.append(index)
            stack.pop(index)
        stack.insert(0, key)
    return result


def event_working_sets(capture: dict[str, Any]) -> dict[str, Any]:
    events = demand_events(capture)
    if not events:
        raise ValueError("capture has no demand events")
    footprint_values = {event["physical_slot_footprint_bytes"] for event in events}
    if len(footprint_values) != 1 or 0 in footprint_values:
        raise ValueError("capture demand footprint is not one exact nonzero value")
    footprint = footprint_values.pop()
    checkpoints: dict[tuple[Any, ...], set[tuple[int, int]]] = defaultdict(set)
    phase_checkpoints: dict[str, list[int]] = defaultdict(list)
    key_counts: Counter[tuple[int, int]] = Counter()
    layer_counts: Counter[int] = Counter()
    layer_width: Counter[int] = Counter()
    for event in events:
        key = (event["layer"], event["expert"])
        checkpoint = (event["request_ordinal"], event["ubatch_ordinal"], event["phase"])
        checkpoints[checkpoint].add(key)
        key_counts[key] += 1
        layer_counts[event["layer"]] += event["occurrence_count"]
    for (_, _, phase), keys in sorted(checkpoints.items()):
        phase_checkpoints[phase].append(len(keys)*footprint)
    routes = capture.get("routes", [])
    token_sets: dict[tuple[Any, ...], set[tuple[int, int]]] = defaultdict(set)
    for route in routes:
        used = route["n_expert_used"]
        layer_width[route["layer"]] = max(layer_width[route["layer"]], used)
        for token in range(route["n_tokens"]):
            group = (route["request_ordinal"], route["phase"], token)
            for expert in route["selected_experts"][token*used:(token + 1)*used]:
                token_sets[group].add((route["layer"], expert))
    token_by_phase: dict[str, list[int]] = defaultdict(list)
    if token_sets:
        for (_, phase, _), keys in sorted(token_sets.items()): token_by_phase[phase].append(len(keys)*footprint)
    else:
        request_tokens: dict[tuple[int, str], set[tuple[int, int]]] = defaultdict(set)
        request_layer_keys: dict[tuple[int, str, int], set[int]] = defaultdict(set)
        for event in events:
            request_tokens[(event["request_ordinal"], event["phase"])].add((event["layer"], event["expert"]))
            request_layer_keys[(event["request_ordinal"], event["phase"], event["layer"])].add(event["expert"])
        for (_, phase), keys in sorted(request_tokens.items()): token_by_phase[phase].append(len(keys)*footprint)
        for (_, _, layer), keys in request_layer_keys.items(): layer_width[layer] = max(layer_width[layer], len(keys))
    routed_layers = sorted(layer_width)
    theoretical = sum(layer_width[layer]*footprint for layer in routed_layers)
    decode_counts = Counter((event["layer"], event["expert"]) for event in events if event["phase"] == "DECODE")
    protected = sum(footprint for count in decode_counts.values() if count >= 2)
    return {
        "one_expert_footprint_bytes": footprint,
        "distinct_key_count": len(key_counts),
        "token_working_set_bytes": {phase.lower(): distribution(values) for phase, values in token_by_phase.items()},
        "checkpoint_working_set_bytes": {phase.lower(): distribution(values) for phase, values in phase_checkpoints.items()},
        "theoretical_token_working_set_bytes": theoretical,
        "protected_decode_set_bytes": protected,
        "reuse_distance": distribution(reuse_distances(events)),
        "per_layer_demand_occurrences": {str(key): value for key, value in sorted(layer_counts.items())},
        "per_layer_max_simultaneous_unique": {str(key): value for key, value in sorted(layer_width.items())},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online-summary", type=Path, required=True)
    parser.add_argument("--synthetic-store", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--host-obligations-bytes", type=int, default=4*1024**3)
    parser.add_argument("--operator-ceiling-bytes", type=int)
    args = parser.parse_args()
    online = json.loads(args.online_summary.read_text())
    descriptor = json.loads(args.synthetic_store.read_text())
    meminfo: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        meminfo[key] = int(value.strip().split()[0])*1024
    headroom = host_safe_ceiling(meminfo["MemTotal"], meminfo["MemAvailable"],
                                 args.host_obligations_bytes, args.operator_ceiling_bytes)
    cases = []
    for case in online["cases"]:
        capture_path = Path(case["capture"]["path"])
        capture = json.loads(capture_path.read_text())
        derived = event_working_sets(capture)
        decode = derived["token_working_set_bytes"].get("decode", {})
        working_set = decode.get("max") or derived["theoretical_token_working_set_bytes"]
        derived["cold_budget_grid"] = legal_budget_grid(
            working_set, derived["one_expert_footprint_bytes"],
            capture["capacities"]["cold_actual_bytes"], headroom["safe_ceiling_bytes"])
        cases.append({"name": case["name"], "model": case["model"], "capture": file_identity(capture_path),
                      "observed_capacities": capture["capacities"],
                      "derivation": "canonical route records when available; otherwise canonical demand checkpoint ordinals",
                      **derived})
    layout = descriptor["layout"]
    recomputed_bundle = layout["projection_bytes"]*layout["projections_per_bundle"]
    if recomputed_bundle != layout["bundle_bytes"] or layout["bundle_count"] != layout["layers"]*layout["experts_per_layer"]:
        raise RuntimeError("full-K3 descriptor constants do not recompute")
    full_w = layout["layers"]*8*layout["bundle_bytes"]
    full_k3 = {
        "descriptor": file_identity(args.synthetic_store), "descriptor_constants_verified": True,
        "one_expert_footprint_bytes": layout["bundle_bytes"], "theoretical_token_working_set_bytes": full_w,
        "budget_grid": legal_budget_grid(full_w, layout["bundle_bytes"], 0, headroom["safe_ceiling_bytes"]),
        "scope_limit": "exact-layout sparse-store mechanism/residency only; no quality or token-throughput claim",
    }
    output = {"schema_version": "cache-working-set-v1", "status": "pass",
              "inputs": {"online": file_identity(args.online_summary), "synthetic_store": file_identity(args.synthetic_store)},
              "headroom": headroom, "cases": cases, "full_k3_mxfp4": full_k3}
    schema = json.loads((ROOT / "schemas/phase9/cache-working-set-v1.schema.json").read_text())
    jsonschema.validate(output, schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "output": str(args.output), "cases": len(cases)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare exact and cache-aware Phase 13 route streams using actual selections."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from cache_aware_replay import Key, ReplayError, TieredLRU, canonical_json, validate_capture


SCHEMA_VERSION = "phase13-real-route-comparison-v1"


def load_capture(path: Path) -> dict[str, Any]:
    try:
        with path.open() as source:
            return validate_capture(json.load(source))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError(f"{path}: unable to load route capture") from exc


def file_identity(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024*1024):
            digest.update(chunk)
            size += len(chunk)
    return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}


def add_totals(target: dict[str, int], source: dict[str, int]) -> None:
    for name, value in source.items():
        target[name] += value


def locality_summary(
        totals: dict[str, int], routed_tokens: int, output_tokens: int,
        bundle_bytes: int) -> dict[str, Any]:
    requests = totals["requests"]
    misses = totals["misses"]
    return {
        **dict(totals),
        "hit_ratio": (totals["hot_hits"] + totals["cold_hits"])/requests if requests else 0.0,
        "miss_ratio": misses/requests if requests else 0.0,
        "backing_store_bytes": misses*bundle_bytes,
        "routed_tokens": routed_tokens,
        "backing_loads_per_routed_token": misses/routed_tokens if routed_tokens else 0.0,
        "backing_store_bytes_per_routed_token":
            misses*bundle_bytes/routed_tokens if routed_tokens else 0.0,
        "generated_output_tokens": output_tokens,
        "backing_store_bytes_per_output_token":
            misses*bundle_bytes/output_tokens if output_tokens else 0.0,
    }


def simulate_selected_routes(
        capture: dict[str, Any], capacity_slots: int, bundle_bytes: int) -> dict[str, Any]:
    cache = TieredLRU(0, capacity_slots)
    totals: dict[str, dict[str, int]] = {
        "PREFILL": defaultdict(int),
        "DECODE": defaultdict(int),
        "ALL": defaultdict(int),
    }
    decisions = {"PREFILL": 0, "DECODE": 0, "ALL": 0}
    routed_tokens: dict[str, set[tuple[int, int]]] = {
        "PREFILL": set(), "DECODE": set(), "ALL": set(),
    }
    for route in capture["routes"]:
        top_k = route["n_expert_used"]
        keys = [Key(route["layer"], expert) for expert in route["selected_experts"]]
        observed = cache.demand_batch(keys)
        phase = route["phase"]
        add_totals(totals[phase], observed)
        add_totals(totals["ALL"], observed)
        decisions[phase] += route["n_tokens"]
        decisions["ALL"] += route["n_tokens"]
        for position in route["positions"]:
            identity = (route.get("request_ordinal", 0), position)
            routed_tokens[phase].add(identity)
            routed_tokens["ALL"].add(identity)
        if len(keys) != route["n_tokens"]*top_k:
            raise ReplayError("selected route cardinality changed during simulation")
    output_tokens = len(capture.get("generated_ids", []))
    return {
        "route_decisions": decisions,
        "prefill": locality_summary(
            totals["PREFILL"], len(routed_tokens["PREFILL"]), output_tokens, bundle_bytes),
        "decode": locality_summary(
            totals["DECODE"], len(routed_tokens["DECODE"]), output_tokens, bundle_bytes),
        "all_phases": locality_summary(
            totals["ALL"], len(routed_tokens["ALL"]), output_tokens, bundle_bytes),
    }


def common_prefix_length(left: list[int], right: list[int]) -> int:
    count = 0
    for first, second in zip(left, right):
        if first != second:
            break
        count += 1
    return count


def compare_route_membership(
        exact: dict[str, Any], changed: dict[str, Any]) -> dict[str, Any]:
    if exact.get("prompt_ids") != changed.get("prompt_ids"):
        raise ReplayError("route captures do not use the same prompt")
    exact_routes = exact["routes"]
    changed_routes = changed["routes"]
    if len(exact_routes) != len(changed_routes):
        raise ReplayError("route captures have different record counts")

    decisions = decode_decisions = intentional = intentional_decode = induced = final = swaps = 0
    decode_decisions_by_swaps: dict[int, int] = defaultdict(int)
    cumulative_regret = 0.0
    first_intentional: dict[str, int] | None = None
    for exact_route, changed_route in zip(exact_routes, changed_routes):
        identity = ("phase", "layer", "n_tokens", "n_expert_used", "n_candidates", "positions")
        if any(exact_route[name] != changed_route[name] for name in identity):
            raise ReplayError("route capture record identity mismatch")
        top_k = exact_route["n_expert_used"]
        retained = exact_route["n_candidates"]
        for token in range(exact_route["n_tokens"]):
            decisions += 1
            is_decode = exact_route["phase"] == "DECODE"
            decode_decisions += is_decode
            exact_selected = exact_route["selected_experts"][token*top_k:(token + 1)*top_k]
            changed_selected = changed_route["selected_experts"][token*top_k:(token + 1)*top_k]
            exact_candidates = exact_route["candidate_experts"][
                token*retained:(token + 1)*retained]
            changed_candidates = changed_route["candidate_experts"][
                token*retained:(token + 1)*retained]
            changed_scores = changed_route["candidate_selection_scores"][
                token*retained:(token + 1)*retained]
            exact_intrinsic = exact_candidates[:top_k]
            changed_intrinsic = changed_candidates[:top_k]
            if exact_selected != exact_intrinsic:
                raise ReplayError("exact capture contains non-exact routing")
            was_intentional = changed_selected != changed_intrinsic
            intentional += was_intentional
            intentional_decode += was_intentional and is_decode
            induced += changed_intrinsic != exact_intrinsic
            final += changed_selected != exact_selected
            if was_intentional and first_intentional is None:
                first_intentional = {
                    "phase": exact_route["phase"],
                    "position": exact_route["positions"][token],
                    "layer": exact_route["layer"],
                }
            decision_swaps = 0
            for rank, selected in enumerate(changed_selected):
                if selected == changed_intrinsic[rank]:
                    continue
                candidate_rank = changed_candidates.index(selected)
                regret = changed_scores[rank] - changed_scores[candidate_rank]
                if not math.isfinite(regret) or regret < 0:
                    raise ReplayError("changed capture contains invalid swap regret")
                swaps += 1
                decision_swaps += 1
                cumulative_regret += regret
            if is_decode:
                decode_decisions_by_swaps[decision_swaps] += 1

    generated_exact = exact.get("generated_ids", [])
    generated_changed = changed.get("generated_ids", [])
    return {
        "decisions": decisions,
        "decode_decisions": decode_decisions,
        "intentional_changed_decisions": intentional,
        "intentional_decode_decisions": intentional_decode,
        "intentional_decode_fraction":
            intentional_decode/decode_decisions if decode_decisions else 0.0,
        "intentional_swaps": swaps,
        "decode_decisions_by_intentional_swaps": {
            str(count): decode_decisions_by_swaps[count]
            for count in sorted(decode_decisions_by_swaps)
        },
        "maximum_intentional_swaps_per_decode_decision":
            max(decode_decisions_by_swaps, default=0),
        "cumulative_score_regret": cumulative_regret,
        "mean_score_regret_per_swap": cumulative_regret/swaps if swaps else 0.0,
        "induced_exact_topk_divergent_decisions": induced,
        "final_route_divergent_decisions": final,
        "first_intentional_swap": first_intentional,
        "exact_generated_ids": generated_exact,
        "changed_generated_ids": generated_changed,
        "common_generated_prefix_tokens": common_prefix_length(generated_exact, generated_changed),
    }


def compare(
        exact_path: Path, changed_path: Path, capacity_slots: int,
        bundle_bytes: int) -> dict[str, Any]:
    if capacity_slots <= 0 or bundle_bytes <= 0:
        raise ReplayError("capacity slots and bundle bytes must be positive")
    exact = load_capture(exact_path)
    changed = load_capture(changed_path)
    if exact["candidate_count"] != changed["candidate_count"]:
        raise ReplayError("route captures use different candidate counts")
    exact_config = exact.get("cache_aware_routing", {"enabled": False})
    changed_config = changed.get("cache_aware_routing", {"enabled": False})
    if exact_config.get("enabled") or not changed_config.get("enabled") or \
            changed_config.get("capacity_slots") != capacity_slots:
        raise ReplayError("capture routing configuration does not match exact/changed comparison")

    routing = compare_route_membership(exact, changed)
    exact_locality = simulate_selected_routes(exact, capacity_slots, bundle_bytes)
    changed_locality = simulate_selected_routes(changed, capacity_slots, bundle_bytes)
    exact_misses = exact_locality["decode"]["misses"]
    changed_misses = changed_locality["decode"]["misses"]
    avoided = exact_misses - changed_misses
    exact_bytes = exact_locality["decode"]["backing_store_bytes"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "configuration": {
            "capacity_slots": capacity_slots,
            "bundle_bytes": bundle_bytes,
            "candidate_count": exact["candidate_count"],
            "measured_phase": "DECODE",
            "prefill_role": "cache_warmup",
            "request_accounting": "unique_layer_expert_keys_at_batch_snapshot",
        },
        "exact_capture": file_identity(exact_path),
        "changed_capture": file_identity(changed_path),
        "changed_policy": changed_config,
        "routing": routing,
        "exact_locality": exact_locality,
        "changed_locality": changed_locality,
        "decode_comparison": {
            "backing_loads_avoided": avoided,
            "backing_store_bytes_avoided": avoided*bundle_bytes,
            "backing_store_byte_reduction_fraction": avoided/exact_misses if exact_misses else 0.0,
            "service_cost_units_avoided":
                exact_locality["decode"]["service_cost_units"] -
                changed_locality["decode"]["service_cost_units"],
            "backing_store_bytes_avoided_per_changed_output_token":
                avoided*bundle_bytes/len(changed.get("generated_ids", []))
                if changed.get("generated_ids") else 0.0,
            "backing_store_bytes_avoided_per_routed_decode_token":
                avoided*bundle_bytes/changed_locality["decode"]["routed_tokens"]
                if changed_locality["decode"]["routed_tokens"] else 0.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-capture", type=Path, required=True)
    parser.add_argument("--changed-capture", type=Path, required=True)
    parser.add_argument("--capacity-slots", type=int, required=True)
    parser.add_argument("--bundle-bytes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare(
            args.exact_capture, args.changed_capture, args.capacity_slots, args.bundle_bytes)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(canonical_json(result))
    except (OSError, ReplayError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

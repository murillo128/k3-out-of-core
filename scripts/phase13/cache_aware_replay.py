#!/usr/bin/env python3
"""Independent Phase 13 exact-route cache-aware opportunity replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


GIB = 1024**3
TIERS = ("HOT", "COLD", "BACKING")
TIER_COST = {"HOT": 0, "COLD": 1, "BACKING": 2}


class ReplayError(ValueError):
    """The capture, policy configuration, or replay state is invalid."""


@dataclass(frozen=True, order=True)
class Key:
    layer: int
    expert: int


@dataclass(frozen=True)
class Swap:
    selected_rank: int
    selected_expert: int
    candidate_rank: int
    candidate_expert: int
    selected_tier: str
    candidate_tier: str
    tier_improvement: int
    regret: float


class TieredLRU:
    """Deterministic exclusive two-tier LRU with hot demotion to cold."""

    def __init__(self, hot_slots: int, cold_slots: int):
        if hot_slots < 0 or cold_slots < 0 or hot_slots + cold_slots <= 0:
            raise ReplayError("cache capacity must contain at least one slot")
        self.hot_slots = hot_slots
        self.cold_slots = cold_slots
        self.hot: OrderedDict[Key, None] = OrderedDict()
        self.cold: OrderedDict[Key, None] = OrderedDict()

    def tier(self, key: Key) -> str:
        if key in self.hot:
            return "HOT"
        if key in self.cold:
            return "COLD"
        return "BACKING"

    def demand_batch(self, keys: Iterable[Key]) -> dict[str, int]:
        ordered = list(dict.fromkeys(keys))
        tiers = [self.tier(key) for key in ordered]
        result = {"requests": len(ordered), "hot_hits": 0, "cold_hits": 0, "misses": 0,
                  "service_cost_units": 0}
        for tier in tiers:
            if tier == "HOT":
                result["hot_hits"] += 1
            elif tier == "COLD":
                result["cold_hits"] += 1
            else:
                result["misses"] += 1
            result["service_cost_units"] += TIER_COST[tier]
        for key in ordered:
            self._demand(key)
        return result

    def _demand(self, key: Key) -> None:
        if key in self.hot:
            self.hot.move_to_end(key)
            return
        if key in self.cold:
            self.cold.pop(key)
        self._admit_hot(key)

    def _admit_hot(self, key: Key) -> None:
        if self.hot_slots == 0:
            self._admit_cold(key)
            return
        self.hot[key] = None
        if len(self.hot) > self.hot_slots:
            demoted, _ = self.hot.popitem(last=False)
            self._admit_cold(demoted)

    def _admit_cold(self, key: Key) -> None:
        if self.cold_slots == 0:
            return
        self.cold.pop(key, None)
        self.cold[key] = None
        if len(self.cold) > self.cold_slots:
            self.cold.popitem(last=False)


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _finite_number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ReplayError(f"{name} must be finite")
    return float(value)


def select_cache_aware(
        baseline: list[int], candidates: list[int], scores: list[float], tiers: list[str],
        candidate_count: int, max_swaps: int, max_score_regret: float) -> tuple[list[int], list[Swap]]:
    """Apply the issue-defined bounded deterministic substitution policy."""
    top_k = len(baseline)
    if top_k == 0 or len(candidates) != len(scores) or len(candidates) != len(tiers):
        raise ReplayError("routing arrays have inconsistent cardinality")
    if not top_k <= candidate_count <= len(candidates):
        raise ReplayError("candidate_count is outside the retained routing list")
    if not 0 <= max_swaps <= top_k:
        raise ReplayError("max_swaps is outside the exact top-k")
    if not math.isfinite(max_score_regret) or max_score_regret < 0:
        raise ReplayError("max_score_regret must be non-negative and finite")
    if len(set(candidates[:candidate_count])) != candidate_count or baseline != candidates[:top_k]:
        raise ReplayError("candidate list is not a unique exact top-k extension")
    if any(tier not in TIERS for tier in tiers[:candidate_count]):
        raise ReplayError("unknown service tier")
    if any(not math.isfinite(score) for score in scores[:candidate_count]):
        raise ReplayError("candidate score is not finite")
    if max_score_regret == 0 or max_swaps == 0 or candidate_count == top_k:
        return list(baseline), []

    options: list[tuple[tuple[Any, ...], Swap]] = []
    for selected_rank in range(top_k):
        selected_cost = TIER_COST[tiers[selected_rank]]
        for candidate_rank in range(top_k, candidate_count):
            candidate_cost = TIER_COST[tiers[candidate_rank]]
            improvement = selected_cost - candidate_cost
            regret = scores[selected_rank] - scores[candidate_rank]
            if improvement <= 0 or regret < 0 or regret > max_score_regret:
                continue
            swap = Swap(
                selected_rank=selected_rank,
                selected_expert=baseline[selected_rank],
                candidate_rank=candidate_rank,
                candidate_expert=candidates[candidate_rank],
                selected_tier=tiers[selected_rank],
                candidate_tier=tiers[candidate_rank],
                tier_improvement=improvement,
                regret=regret,
            )
            order = (-improvement, regret, candidate_rank, selected_rank,
                     candidates[candidate_rank], baseline[selected_rank])
            options.append((order, swap))

    final = list(baseline)
    swaps: list[Swap] = []
    used_slots: set[int] = set()
    used_candidates: set[int] = set()
    for _, swap in sorted(options, key=lambda item: item[0]):
        if len(swaps) >= max_swaps:
            break
        if swap.selected_rank in used_slots or swap.candidate_expert in used_candidates:
            continue
        final[swap.selected_rank] = swap.candidate_expert
        swaps.append(swap)
        used_slots.add(swap.selected_rank)
        used_candidates.add(swap.candidate_expert)

    if len(final) != top_k or len(set(final)) != top_k:
        raise ReplayError("bounded selection violated unique exact cardinality")
    return final, swaps


def validate_capture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema_version") != "phase13-exact-topm-capture-v1" or \
            value.get("status") != "pass" or not isinstance(value.get("routes"), list):
        raise ReplayError("unsupported Phase 13 route capture")
    retained = value.get("candidate_count")
    if not isinstance(retained, int) or retained <= 0:
        raise ReplayError("capture candidate_count is invalid")
    routing = value.get("cache_aware_routing", {"enabled": False})
    if not isinstance(routing, dict) or not isinstance(routing.get("enabled"), bool):
        raise ReplayError("capture cache-aware routing configuration is invalid")
    routing_enabled = routing["enabled"]
    if routing_enabled:
        max_swaps = routing.get("max_swaps")
        max_regret = _finite_number(routing.get("max_score_regret"), "max_score_regret")
        if not isinstance(max_swaps, int) or isinstance(max_swaps, bool) or not 0 <= max_swaps <= 16 or \
                max_regret < 0 or routing.get("candidate_count") != retained or \
                routing.get("prefill_rerouting") is not False:
            raise ReplayError("capture cache-aware routing bounds are invalid")
    else:
        max_swaps = 0
        max_regret = 0.0

    for ordinal, record in enumerate(value["routes"]):
        if not isinstance(record, dict):
            raise ReplayError(f"route {ordinal} is not an object")
        n_tokens = record.get("n_tokens")
        top_k = record.get("n_expert_used")
        n_candidates = record.get("n_candidates")
        if not all(isinstance(item, int) and item > 0 for item in (n_tokens, top_k, n_candidates)) or \
                n_candidates != retained or top_k > n_candidates:
            raise ReplayError(f"route {ordinal} cardinality is invalid")
        expected_selected = n_tokens*top_k
        expected_candidates = n_tokens*n_candidates
        for name, expected in (("selected_experts", expected_selected), ("weights", expected_selected),
                               ("candidate_experts", expected_candidates),
                               ("candidate_selection_scores", expected_candidates),
                               ("candidate_probabilities", expected_candidates)):
            if not isinstance(record.get(name), list) or len(record[name]) != expected:
                raise ReplayError(f"route {ordinal} {name} cardinality is invalid")
        for name in ("weights", "candidate_selection_scores", "candidate_probabilities"):
            for item in record[name]:
                _finite_number(item, f"route {ordinal} {name}")
        for token in range(n_tokens):
            selected = record["selected_experts"][token*top_k:(token + 1)*top_k]
            candidates = record["candidate_experts"][token*n_candidates:(token + 1)*n_candidates]
            scores = record["candidate_selection_scores"][
                token*n_candidates:(token + 1)*n_candidates]
            if len(set(selected)) != top_k or \
                    any(not isinstance(item, int) or isinstance(item, bool) or item < 0
                        for item in selected) or \
                    len(set(candidates)) != n_candidates or \
                    any(not isinstance(item, int) or isinstance(item, bool) or item < 0
                        for item in candidates) or \
                    any(scores[index] > scores[index - 1]
                        for index in range(1, n_candidates)):
                raise ReplayError(f"route {ordinal} routing arrays are invalid")
            changed = 0
            for rank, expert in enumerate(selected):
                if expert == candidates[rank]:
                    continue
                changed += 1
                if not routing_enabled or max_regret == 0 or record.get("phase") == "PREFILL" or \
                        expert not in candidates[top_k:] or \
                        scores[rank] - scores[candidates.index(expert)] < 0 or \
                        scores[rank] - scores[candidates.index(expert)] > max_regret:
                    raise ReplayError(f"route {ordinal} violates bounded cache-aware membership")
            if changed > max_swaps:
                raise ReplayError(f"route {ordinal} exceeds max_swaps")
        positions = record.get("positions")
        if not isinstance(positions, list) or len(positions) != n_tokens or \
                any(not isinstance(item, int) or isinstance(item, bool) or item < 0
                    for item in positions):
            raise ReplayError(f"route {ordinal} positions are invalid")
        if any(item < 0 for item in record["weights"] + record["candidate_probabilities"]):
            raise ReplayError(f"route {ordinal} contains a negative routing probability")
        if record.get("phase") not in ("PREFILL", "DECODE") or not isinstance(record.get("layer"), int):
            raise ReplayError(f"route {ordinal} identity is invalid")
    if not value["routes"]:
        raise ReplayError("route capture is empty")
    return value


def observed_gaps(capture: dict[str, Any], phases: set[str]) -> list[float]:
    result: list[float] = []
    for record in capture["routes"]:
        if record["phase"] not in phases:
            continue
        top_k = record["n_expert_used"]
        retained = record["n_candidates"]
        for token in range(record["n_tokens"]):
            scores = record["candidate_selection_scores"][token*retained:(token + 1)*retained]
            for rank in range(top_k, retained):
                gap = scores[top_k - 1] - scores[rank]
                if gap >= 0 and math.isfinite(gap):
                    result.append(gap)
    return sorted(result)


def _quantile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    return values[round((len(values) - 1)*fraction)]


def observed_thresholds(gaps: list[float]) -> tuple[list[float], dict[str, float]]:
    positive = [value for value in gaps if value > 0]
    distribution = {
        "count": len(gaps),
        "positive_count": len(positive),
        "minimum": positive[0] if positive else 0.0,
        "p10": _quantile(positive, 0.10),
        "p25": _quantile(positive, 0.25),
        "p50": _quantile(positive, 0.50),
        "p75": _quantile(positive, 0.75),
        "p90": _quantile(positive, 0.90),
        "maximum": positive[-1] if positive else 0.0,
    }
    thresholds = sorted(set([0.0] + [value for name, value in distribution.items()
        if name not in ("count", "positive_count") and value > 0]))
    return thresholds, distribution


def _accumulate(target: dict[str, int], source: dict[str, int]) -> None:
    for name, value in source.items():
        target[name] += value


def replay_point(
        capture: dict[str, Any], bundle_bytes: int, hot_slots: int, cold_slots: int,
        candidate_count: int, max_swaps: int, max_score_regret: float,
        reroute_phases: set[str]) -> dict[str, Any]:
    baseline_cache = TieredLRU(hot_slots, cold_slots)
    policy_cache = TieredLRU(hot_slots, cold_slots)
    baseline_totals = defaultdict(int)
    policy_totals = defaultdict(int)
    candidate_tiers = defaultdict(int)
    decisions = warmup_decisions = changed_decisions = swap_count = boundary_swaps = 0
    regrets: list[float] = []
    baseline_token_keys: dict[tuple[Any, ...], set[Key]] = defaultdict(set)
    policy_token_keys: dict[tuple[Any, ...], set[Key]] = defaultdict(set)

    for record in capture["routes"]:
        top_k = record["n_expert_used"]
        retained = record["n_candidates"]
        if candidate_count < top_k or candidate_count > retained:
            raise ReplayError("requested candidate_count is outside capture")
        baseline_batch: list[Key] = []
        policy_batch: list[Key] = []
        measured = record["phase"] in reroute_phases
        for token in range(record["n_tokens"]):
            selected = record["selected_experts"][token*top_k:(token + 1)*top_k]
            candidates = record["candidate_experts"][token*retained:(token + 1)*retained]
            scores = record["candidate_selection_scores"][token*retained:(token + 1)*retained]
            tiers = [policy_cache.tier(Key(record["layer"], expert))
                     for expert in candidates[:candidate_count]]
            if measured:
                for tier in tiers:
                    candidate_tiers[tier.lower()] += 1
                decisions += 1
                final, swaps = select_cache_aware(
                    selected, candidates[:candidate_count], scores[:candidate_count], tiers,
                    candidate_count, max_swaps, max_score_regret)
            else:
                warmup_decisions += 1
                final, swaps = list(selected), []
            if swaps:
                changed_decisions += 1
                swap_count += len(swaps)
                regrets.extend(swap.regret for swap in swaps)
                boundary_swaps += sum(swap.candidate_rank == candidate_count - 1 for swap in swaps)

            baseline_keys = [Key(record["layer"], expert) for expert in selected]
            policy_keys = [Key(record["layer"], expert) for expert in final]
            baseline_batch.extend(baseline_keys)
            policy_batch.extend(policy_keys)
            position = record.get("positions", [])
            token_identity = (
                record.get("request_ordinal", 0), record.get("ubatch_ordinal", 0),
                record["phase"], position[token] if len(position) == record["n_tokens"] else token,
            )
            if measured:
                baseline_token_keys[token_identity].update(baseline_keys)
                policy_token_keys[token_identity].update(policy_keys)

        baseline_result = baseline_cache.demand_batch(baseline_batch)
        policy_result = policy_cache.demand_batch(policy_batch)
        if measured:
            _accumulate(baseline_totals, baseline_result)
            _accumulate(policy_totals, policy_result)

    token_count = len(baseline_token_keys)
    baseline_misses = baseline_totals["misses"]
    policy_misses = policy_totals["misses"]
    avoided = baseline_misses - policy_misses
    baseline_bytes = baseline_misses*bundle_bytes
    policy_bytes = policy_misses*bundle_bytes
    reduction = 0.0 if baseline_bytes == 0 else (baseline_bytes - policy_bytes)/baseline_bytes
    def tier_summary(totals: dict[str, int], backing_bytes: int,
                     token_keys: dict[tuple[Any, ...], set[Key]]) -> dict[str, Any]:
        requests = totals["requests"]
        return {
            **dict(totals),
            "hit_ratio": (totals["hot_hits"] + totals["cold_hits"])/requests if requests else 0.0,
            "miss_ratio": totals["misses"]/requests if requests else 0.0,
            "requests_per_token": requests/token_count if token_count else 0.0,
            "distinct_expert_loads_per_token": totals["misses"]/token_count if token_count else 0.0,
            "backing_store_bytes": backing_bytes,
            "backing_store_bytes_per_token": backing_bytes/token_count if token_count else 0.0,
            "service_cost_units_per_token": totals["service_cost_units"]/token_count if token_count else 0.0,
            "distinct_expert_keys_per_token": sum(map(len, token_keys.values()))/token_count,
        }
    return {
        "hot_capacity_slots": hot_slots,
        "cold_capacity_slots": cold_slots,
        "total_capacity_bytes": (hot_slots + cold_slots)*bundle_bytes,
        "candidate_count": candidate_count,
        "max_swaps": max_swaps,
        "max_score_regret": max_score_regret,
        "measured_phases": sorted(reroute_phases),
        "warmup_route_decisions": warmup_decisions,
        "route_decisions": decisions,
        "changed_route_decisions": changed_decisions,
        "changed_route_fraction": changed_decisions/decisions if decisions else 0.0,
        "swaps": swap_count,
        "swaps_per_token": swap_count/token_count if token_count else 0.0,
        "swaps_per_routed_layer": swap_count/decisions if decisions else 0.0,
        "per_swap_regret": {
            "count": len(regrets),
            "minimum": min(regrets) if regrets else 0.0,
            "mean": sum(regrets)/len(regrets) if regrets else 0.0,
            "maximum": max(regrets) if regrets else 0.0,
            "cumulative": sum(regrets),
        },
        "candidate_tiers_at_selection": dict(candidate_tiers),
        "baseline": tier_summary(baseline_totals, baseline_bytes, baseline_token_keys),
        "cache_aware": tier_summary(policy_totals, policy_bytes, policy_token_keys),
        "provider_loads_avoided": avoided,
        "backing_store_bytes_avoided": baseline_bytes - policy_bytes,
        "backing_store_byte_reduction_fraction": reduction,
        "candidate_boundary_swaps": boundary_swaps,
    }


def _frontier(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    groups: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
    for point in points:
        groups[(point["hot_capacity_slots"], point["cold_capacity_slots"],
                point["candidate_count"])].append(point)
    for values in groups.values():
        unique: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for point in sorted(values, key=lambda item: (
                item["max_score_regret"], item["max_swaps"],
                item["per_swap_regret"]["cumulative"],
                -item["backing_store_bytes_avoided"])):
            outcome = (
                point["per_swap_regret"]["cumulative"],
                point["backing_store_bytes_avoided"],
                point["swaps"],
                point["changed_route_decisions"],
            )
            if outcome in seen:
                continue
            seen.add(outcome)
            unique.append(point)
        for point in unique:
            dominated = any(
                other is not point and
                other["per_swap_regret"]["cumulative"] <= point["per_swap_regret"]["cumulative"] and
                other["backing_store_bytes_avoided"] >= point["backing_store_bytes_avoided"] and
                (other["per_swap_regret"]["cumulative"] < point["per_swap_regret"]["cumulative"] or
                 other["backing_store_bytes_avoided"] > point["backing_store_bytes_avoided"])
                for other in unique)
            if not dominated:
                result.append(point)
    return sorted(result, key=lambda point: (
        point["total_capacity_bytes"], point["candidate_count"],
        point["per_swap_regret"]["cumulative"], -point["backing_store_bytes_avoided"]))


def run_replay(
        capture: dict[str, Any], bundle_bytes: int, capacities_gib: list[float], hot_capacity_gib: float,
        candidate_counts: list[int], max_swaps_values: list[int], reroute_phases: set[str],
        material_reduction: float) -> dict[str, Any]:
    capture = validate_capture(capture)
    if bundle_bytes <= 0 or not capacities_gib or \
            any(not math.isfinite(value) or value <= 0 for value in capacities_gib):
        raise ReplayError("bundle size and cache capacities must be positive")
    if not math.isfinite(hot_capacity_gib) or hot_capacity_gib < 0 or \
            hot_capacity_gib > min(capacities_gib) or not 0 < material_reduction < 1:
        raise ReplayError("gate configuration is invalid")
    if not reroute_phases or not reroute_phases <= {"PREFILL", "DECODE"}:
        raise ReplayError("reroute phases must be a nonempty subset of PREFILL and DECODE")
    gaps = observed_gaps(capture, reroute_phases)
    thresholds, distribution = observed_thresholds(gaps)
    if not thresholds:
        thresholds = [0.0]
    points = []
    for capacity_gib in capacities_gib:
        total_slots = int(capacity_gib*GIB)//bundle_bytes
        hot_slots = int(hot_capacity_gib*GIB)//bundle_bytes
        cold_slots = total_slots - hot_slots
        if total_slots <= 0:
            raise ReplayError("a cache capacity cannot hold one expert bundle")
        for candidate_count in candidate_counts:
            for max_swaps in max_swaps_values:
                for threshold in thresholds:
                    points.append(replay_point(
                        capture, bundle_bytes, hot_slots, cold_slots, candidate_count,
                        max_swaps, threshold, reroute_phases))

    initial_count = min(32, capture["candidate_count"])
    small_regret = distribution["p25"]
    qualifying = [point for point in points
        if point["candidate_count"] == initial_count and 0 < point["max_swaps"] <= 2 and
        0 < point["max_score_regret"] <= small_regret and
        point["backing_store_byte_reduction_fraction"] >= material_reduction]
    disposition = "positive-frontier" if qualifying else "negative-locality"
    return {
        "schema_version": "phase13-offline-routing-frontier-v1",
        "disposition": disposition,
        "configuration": {
            "bundle_bytes": bundle_bytes,
            "capacities_gib": capacities_gib,
            "hot_capacity_gib": hot_capacity_gib,
            "candidate_counts": candidate_counts,
            "max_swaps": max_swaps_values,
            "reroute_phases": sorted(reroute_phases),
            "threshold_source": "observed_topk_boundary_gap_quantiles",
        },
        "observed_score_gap_distribution": distribution,
        "observed_score_regret_thresholds": thresholds,
        "gate": {
            "minimum_backing_store_byte_reduction_fraction": material_reduction,
            "maximum_small_regret_threshold": small_regret,
            "maximum_swaps": 2,
            "candidate_count": initial_count,
            "qualifying_points": len(qualifying),
        },
        "points": points,
        "frontier": _frontier(points),
    }


def csv_numbers(text: str, caster: Any) -> list[Any]:
    try:
        values = [caster(item) for item in text.split(",")]
    except ValueError as error:
        raise ReplayError("invalid comma-separated numeric list") from error
    if not values or len(set(values)) != len(values):
        raise ReplayError("numeric sweep values must be nonempty and unique")
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bundle-bytes", type=int, required=True)
    parser.add_argument("--capacities-gib", default="20,32,40,60,64,80,96")
    parser.add_argument("--hot-capacity-gib", type=float, default=0.0)
    parser.add_argument("--candidate-counts", default="16,24,32")
    parser.add_argument("--max-swaps", default="0,1,2,4")
    parser.add_argument("--reroute-phases", default="DECODE")
    parser.add_argument("--material-reduction", type=float, default=0.05)
    args = parser.parse_args()
    capture_bytes = args.input.read_bytes()
    capture = json.loads(capture_bytes)
    result = run_replay(
        capture=capture,
        bundle_bytes=args.bundle_bytes,
        capacities_gib=csv_numbers(args.capacities_gib, float),
        hot_capacity_gib=args.hot_capacity_gib,
        candidate_counts=csv_numbers(args.candidate_counts, int),
        max_swaps_values=csv_numbers(args.max_swaps, int),
        reroute_phases=set(args.reroute_phases.split(",")),
        material_reduction=args.material_reduction,
    )
    result["input"] = {
        "path": str(args.input),
        "sha256": hashlib.sha256(capture_bytes).hexdigest(),
        "schema_version": capture.get("schema_version"),
    }
    args.output.write_text(canonical_json(result))
    print(f"PHASE13_OFFLINE_REPLAY disposition={result['disposition']} points={len(result['points'])} "
          f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

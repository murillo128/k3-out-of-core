#!/usr/bin/env python3
"""Replay real Kimi-K3 routes through global LRU/ALWAYS and project NVMe service envelopes."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import resource
import statistics
import time
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from typing import Any, NamedTuple


ROOT = Path(__file__).resolve().parents[2]
BUNDLE_BYTES = 17_547_264
EXPERTS_PER_LAYER = 896
ROUTED_LAYERS = 92
TOP_K = 16
GIB = 1 << 30
CAPACITIES_GIB = (0, 8, 16, 32, 64, 96)
COLD_DECODE_TOKENS = 32


class Event(NamedTuple):
    request: int
    phase: str
    token: int
    layer: int
    rank: int
    expert: int


class Fenwick:
    def __init__(self, size: int):
        self.values = [0] * (size + 1)

    def add(self, index: int, delta: int) -> None:
        while index < len(self.values):
            self.values[index] += delta
            index += index & -index

    def total(self, index: int) -> int:
        result = 0
        while index:
            result += self.values[index]
            index -= index & -index
        return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    path = path.resolve()
    try:
        shown = str(path.relative_to(ROOT))
    except ValueError:
        shown = str(path)
    return {"path": shown, "size": path.stat().st_size, "sha256": sha256_file(path)}


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def percentile(values: list[float] | list[int], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    return float(ordered[max(0, math.ceil(fraction * len(ordered)) - 1)])


def distribution(values: list[int] | list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": statistics.mean(values) if values else None,
        "p50": percentile(values, 0.50) if values else None,
        "p90": percentile(values, 0.90) if values else None,
        "p95": percentile(values, 0.95) if values else None,
        "p99": percentile(values, 0.99) if values else None,
        "max": max(values) if values else None,
    }


def load_events(capture_paths: list[Path]) -> tuple[list[Event], list[dict[str, Any]]]:
    events: list[Event] = []
    captures: list[dict[str, Any]] = []
    request_ids: set[int] = set()
    for capture_path in capture_paths:
        capture = json.loads(capture_path.read_text())
        if capture.get("status") != "PASS" or capture.get("disposition") != "accepted":
            raise ValueError(f"routing capture did not pass: {capture_path}")
        if capture.get("model_revision") != "9f62e4e9fffbd0a83ddd60e1c209d828994b3569":
            raise ValueError("routing capture model revision changed")
        if capture.get("environment", {}).get("K3_TOPP") != "0":
            raise ValueError("routing capture pruned exact top-16 routing")
        request_id = int(capture["request_id"])
        if request_id in request_ids:
            raise ValueError("routing capture request ids are not unique")
        request_ids.add(request_id)
        route_artifact = capture["raw_artifacts"]["normalized-route.tsv"]
        route_path = Path(route_artifact["path"])
        if not route_path.is_file() or route_path.stat().st_size != int(route_artifact["size"]):
            raise ValueError("normalized routing trace is unavailable or changed")
        if sha256_file(route_path) != route_artifact["sha256"]:
            raise ValueError("normalized routing trace checksum changed")
        with route_path.open(newline="") as stream:
            reader = csv.DictReader(stream, delimiter="\t")
            if reader.fieldnames != ["request", "phase", "token", "layer", "rank", "expert_id"]:
                raise ValueError("normalized routing trace schema changed")
            for row in reader:
                event = Event(
                    int(row["request"]), row["phase"], int(row["token"]),
                    int(row["layer"]), int(row["rank"]), int(row["expert_id"]),
                )
                if event.request != request_id or event.phase not in ("PREFILL", "DECODE"):
                    raise ValueError("normalized routing trace request/phase mismatch")
                if not 0 <= event.rank < TOP_K or not 0 <= event.expert < EXPERTS_PER_LAYER:
                    raise ValueError("normalized routing trace rank/expert is invalid")
                events.append(event)
        captures.append({
            "summary": identity(capture_path),
            "route": identity(route_path),
            "request_id": request_id,
            "prompt": capture["prompt"],
            "prompt_utf8_sha256": capture["prompt_utf8_sha256"],
            "runtime": capture["runtime"],
            "routing_trace": capture["routing_trace"],
            "binary": capture["binary"],
            "colibri_commit": capture["colibri_commit"],
            "model_revision": capture["model_revision"],
            "environment": capture["environment"],
        })
    if not events:
        raise ValueError("routing corpus contains no expert requests")
    validate_event_order(events)
    return events, captures


def validate_event_order(events: list[Event]) -> None:
    if len(events) % TOP_K:
        raise ValueError("routing corpus ends inside a top-k group")
    groups: list[tuple[int, str, int, int]] = []
    for start in range(0, len(events), TOP_K):
        group = events[start:start + TOP_K]
        identity_fields = {(item.request, item.phase, item.token, item.layer) for item in group}
        if len(identity_fields) != 1 or [item.rank for item in group] != list(range(TOP_K)):
            raise ValueError("routing corpus does not preserve request/token/layer/rank order")
        if len({item.expert for item in group}) != TOP_K:
            raise ValueError("routing corpus contains duplicate experts in one top-k")
        groups.append(next(iter(identity_fields)))

    by_token: dict[tuple[int, str, int], list[int]] = defaultdict(list)
    token_order: list[tuple[int, str, int]] = []
    for request, phase, token, layer in groups:
        key = (request, phase, token)
        if key not in by_token:
            token_order.append(key)
        by_token[key].append(layer)
    expected_layers: list[int] | None = None
    for key in token_order:
        layers = by_token[key]
        if layers != sorted(layers) or len(layers) != ROUTED_LAYERS or len(set(layers)) != ROUTED_LAYERS:
            raise ValueError(f"routing token {key} does not contain the exact routed-layer sequence")
        if expected_layers is None:
            expected_layers = layers
        elif layers != expected_layers:
            raise ValueError(f"routing token {key} changed routed-layer identity")
    previous: tuple[int, int, int] | None = None
    phase_value = {"PREFILL": 0, "DECODE": 1}
    for request, phase, token in token_order:
        current = (request, phase_value[phase], token)
        if previous is not None and current <= previous:
            raise ValueError("routing tokens are not in request/prefill/decode order")
        previous = current
    for request in sorted({item.request for item in events}):
        for phase in ("PREFILL", "DECODE"):
            tokens = sorted({item.token for item in events if item.request == request and item.phase == phase})
            if tokens and tokens != list(range(tokens[-1] + 1)):
                raise ValueError(f"routing tokens are not contiguous for request {request} {phase}")


def window_output(counter: Counter[str]) -> dict[str, Any]:
    requests = counter["requests"]
    hits = counter["hits"]
    misses = counter["misses"]
    useful = requests * BUNDLE_BYTES
    avoided = hits * BUNDLE_BYTES
    required = misses * BUNDLE_BYTES
    return {
        "expert_requests": requests,
        "useful_expert_bytes": useful,
        "hits": hits,
        "misses": misses,
        "hit_ratio_by_expert_requests": hits / requests if requests else None,
        "hit_ratio_by_useful_bytes": avoided / useful if useful else None,
        "nvme_bytes_avoided": avoided,
        "required_nvme_bytes": required,
    }


def replay_capacity(
    events: list[Event],
    slots: int,
    cold_decode_tokens: int = COLD_DECODE_TOKENS,
    expected_requests_per_decode_token: int = ROUTED_LAYERS * TOP_K,
) -> dict[str, Any]:
    if slots < 0:
        raise ValueError("cache slots must be nonnegative")
    cache: OrderedDict[int, None] = OrderedDict()
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    admissions = 0
    evictions = 0
    digest = hashlib.sha256()
    decode_order: dict[tuple[int, int], int] = {}
    token_misses: dict[tuple[int, int], int] = Counter()
    token_requests: dict[tuple[int, int], int] = Counter()
    occupancy: list[dict[str, int]] = []
    active_decode: tuple[int, int] | None = None

    for event in events:
        if event.phase == "DECODE":
            token_key = (event.request, event.token)
            if token_key not in decode_order:
                decode_order[token_key] = len(decode_order)
            if active_decode is not None and token_key != active_decode:
                occupancy.append({
                    "request": active_decode[0], "token": active_decode[1],
                    "slots": len(cache), "bytes": len(cache) * BUNDLE_BYTES,
                })
            active_decode = token_key
        key = event.layer * EXPERTS_PER_LAYER + event.expert
        hit = key in cache
        victim = -1
        if hit:
            cache.move_to_end(key)
        elif slots:
            if len(cache) == slots:
                victim, _ = cache.popitem(last=False)
                evictions += 1
            cache[key] = None
            admissions += 1

        windows = ["all", "prefill" if event.phase == "PREFILL" else "decode"]
        if event.phase == "DECODE":
            ordinal = decode_order[(event.request, event.token)]
            windows.append("decode_cold_start" if ordinal < cold_decode_tokens else "decode_steady_state")
            token_requests[(event.request, event.token)] += 1
            if not hit:
                token_misses[(event.request, event.token)] += 1
        for window in windows:
            counters[window]["requests"] += 1
            counters[window]["hits" if hit else "misses"] += 1
        digest.update(
            f"{event.request}\t{event.phase}\t{event.token}\t{event.layer}\t{event.rank}\t"
            f"{event.expert}\t{'H' if hit else 'M'}\t{victim}\n".encode()
        )
    if active_decode is not None:
        occupancy.append({
            "request": active_decode[0], "token": active_decode[1],
            "slots": len(cache), "bytes": len(cache) * BUNDLE_BYTES,
        })

    ordered_tokens = sorted(decode_order, key=decode_order.get)
    misses = [token_misses[token] for token in ordered_tokens]
    requests = [token_requests[token] for token in ordered_tokens]
    if any(value != expected_requests_per_decode_token for value in requests):
        raise ValueError("decode token does not contain exact 92-layer top-16 demand")
    miss_bytes = [value * BUNDLE_BYTES for value in misses]
    return {
        "slots": slots,
        "usable_capacity_bytes": slots * BUNDLE_BYTES,
        "windows": {
            name: window_output(counters[name])
            for name in ("all", "prefill", "decode", "decode_cold_start", "decode_steady_state")
        },
        "admissions": admissions,
        "evictions": evictions,
        "final_occupancy_slots": len(cache),
        "final_occupancy_bytes": len(cache) * BUNDLE_BYTES,
        "occupancy_after_each_decode_token": occupancy,
        "decode_misses_per_token": {"values": misses, **distribution(misses)},
        "decode_required_nvme_bytes_per_token": {"values": miss_bytes, **distribution(miss_bytes)},
        "final_lru_state_sha256": canonical_sha256(list(cache)),
        "deterministic_replay_digest": digest.hexdigest(),
    }


def logarithmic_histogram(values: list[int]) -> dict[str, int]:
    result: Counter[str] = Counter()
    for value in values:
        if value == 0:
            result["0"] += 1
        else:
            lower = 1 << (value.bit_length() - 1)
            upper = (lower << 1) - 1
            result[f"{lower}-{upper}"] += 1
    return dict(result)


def reuse_statistics(events: list[Event], capacities: dict[int, int]) -> dict[str, Any]:
    fenwick = Fenwick(len(events))
    last_position: dict[int, int] = {}
    last_forward: dict[int, int] = {}
    forward_ids: dict[tuple[int, str, int], int] = {}
    stack_distances: list[int] = []
    event_intervals: list[int] = []
    forward_intervals: list[int] = []
    decode_stack_distances: list[int] = []
    decode_reuses = 0
    decode_first_references = 0
    hits_by_capacity: Counter[int] = Counter()
    for position, event in enumerate(events, 1):
        forward_key = (event.request, event.phase, event.token)
        if forward_key not in forward_ids:
            forward_ids[forward_key] = len(forward_ids)
        forward = forward_ids[forward_key]
        key = event.layer * EXPERTS_PER_LAYER + event.expert
        previous = last_position.get(key)
        if previous is not None:
            distance = fenwick.total(position - 1) - fenwick.total(previous)
            interval = position - previous
            stack_distances.append(distance)
            event_intervals.append(interval)
            forward_intervals.append(forward - last_forward[key])
            if event.phase == "DECODE":
                decode_reuses += 1
                decode_stack_distances.append(distance)
                for gib, slots in capacities.items():
                    if slots and distance < slots:
                        hits_by_capacity[gib] += 1
            fenwick.add(previous, -1)
        elif event.phase == "DECODE":
            decode_first_references += 1
        fenwick.add(position, 1)
        last_position[key] = position
        last_forward[key] = forward
    return {
        "definition": (
            "exact LRU stack distance is the number of distinct (layer,expert) keys referenced since the prior "
            "reference; a request hits a C-slot cache iff distance < C"
        ),
        "all_references": len(events),
        "first_references": len(events) - len(stack_distances),
        "reuses": len(stack_distances),
        "stack_distance_distinct_experts": distribution(stack_distances),
        "stack_distance_log2_histogram": logarithmic_histogram(stack_distances),
        "reuse_interval_expert_requests": distribution(event_intervals),
        "reuse_interval_forwards": distribution(forward_intervals),
        "reuse_interval_forwards_log2_histogram": logarithmic_histogram(forward_intervals),
        "decode": {
            "first_references": decode_first_references,
            "reuses": decode_reuses,
            "stack_distance_distinct_experts": distribution(decode_stack_distances),
            "theoretical_lru_hits_by_capacity_gib": {str(gib): hits_by_capacity[gib] for gib in capacities},
        },
    }


def storage_projection(miss_bytes: list[int], central_gbps: float, observed_gbps: list[float]) -> dict[str, Any]:
    seconds = [value / (central_gbps * 1e9) for value in miss_bytes]
    mean_bytes = statistics.mean(miss_bytes)
    return {
        "service_throughput_gbps": central_gbps,
        "observed_throughput_gbps_range": [min(observed_gbps), max(observed_gbps)],
        "projected_storage_seconds_per_decode_token": {"values": seconds, **distribution(seconds)},
        "mean_storage_seconds_range_from_observed_throughput": [
            mean_bytes / (max(observed_gbps) * 1e9),
            mean_bytes / (min(observed_gbps) * 1e9),
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--baseline-analysis", type=Path, required=True)
    parser.add_argument("--dual-comparison", type=Path, required=True)
    parser.add_argument("--host-preflight", type=Path, required=True)
    parser.add_argument("--colibri-preflight", type=Path, required=True)
    parser.add_argument("--colibri-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    events, captures = load_events([path.resolve() for path in args.capture])
    decode_tokens = sorted({(item.request, item.token) for item in events if item.phase == "DECODE"})
    if len(decode_tokens) < 256:
        raise ValueError(f"only {len(decode_tokens)} complete decode-token forwards are available")

    baseline = json.loads(args.baseline_analysis.read_text())
    dual = json.loads(args.dual_comparison.read_text())
    host = json.loads(args.host_preflight.read_text())
    colibri_preflight = json.loads(args.colibri_preflight.read_text())
    colibri_reference = json.loads(args.colibri_reference.read_text())
    if any(value.get("status") != "PASS" for value in (baseline, dual, host, colibri_preflight, colibri_reference)):
        raise ValueError("an accepted service or RAM envelope input did not pass")

    capacity_slots = {gib: (gib * GIB) // BUNDLE_BYTES for gib in CAPACITIES_GIB}
    safe_ceiling = int(colibri_preflight["declared_ceilings"]["memory_bytes"])
    existing_slots = int(colibri_reference["metrics"]["expert_cache_slots_per_layer"]) * ROUTED_LAYERS
    existing_cache_bytes = existing_slots * BUNDLE_BYTES
    observed_rss = int(colibri_reference["process_resources"]["max_rss_bytes"])
    inferred_non_cache_rss = max(0, observed_rss - existing_cache_bytes)
    supported: dict[int, dict[str, Any]] = {}
    for gib, slots in capacity_slots.items():
        projected = inferred_non_cache_rss + slots * BUNDLE_BYTES
        supported[gib] = {
            "status": "SUPPORTED" if projected <= safe_ceiling else "UNSUPPORTED",
            "requested_capacity_bytes": gib * GIB,
            "slots": slots,
            "usable_capacity_bytes": slots * BUNDLE_BYTES,
            "projected_process_rss_bytes": projected,
            "declared_safe_memory_ceiling_bytes": safe_ceiling,
        }

    rows: list[dict[str, Any]] = []
    for gib in CAPACITIES_GIB:
        support = supported[gib]
        if support["status"] == "UNSUPPORTED":
            rows.append({"capacity_gib": gib, "support": support})
            continue
        replay = replay_capacity(events, capacity_slots[gib])
        rows.append({"capacity_gib": gib, "support": support, "replay": replay})

    reuse = reuse_statistics(events, capacity_slots)
    for row in rows:
        if row["support"]["status"] != "SUPPORTED":
            continue
        gib = int(row["capacity_gib"])
        observed = int(row["replay"]["windows"]["decode"]["hits"])
        theoretical = int(reuse["decode"]["theoretical_lru_hits_by_capacity_gib"][str(gib)])
        if observed != theoretical:
            raise ValueError(f"LRU replay/reuse-distance mismatch at {gib} GiB")

    frozen_single = float(baseline["frozen_baseline"]["cold"]["useful_gbps"])
    single_observed = [frozen_single] + [float(pair["single"]["useful_gbps"]) for pair in dual["pairs"]]
    dual_observed = [float(pair["dual"]["useful_gbps"]) for pair in dual["pairs"]]
    dual_central = statistics.mean(dual_observed)
    for row in rows:
        if row["support"]["status"] != "SUPPORTED":
            continue
        miss_bytes = row["replay"]["decode_required_nvme_bytes_per_token"]["values"]
        row["storage_only_projection"] = {
            "single_nvme": storage_projection(miss_bytes, frozen_single, single_observed),
            "dual_nvme": storage_projection(miss_bytes, dual_central, dual_observed),
            "claim_boundary": (
                "miss-byte service component only; not actual Colibrì TPS, projected project end-to-end TPS, "
                "H2D, CUDA, GPU compute, or overlap"
            ),
        }

    thresholds: dict[str, Any] = {}
    for percent in (10, 20, 30):
        selected = next((
            row for row in rows
            if row["support"]["status"] == "SUPPORTED"
            and row["replay"]["windows"]["decode"]["hit_ratio_by_useful_bytes"] >= percent / 100
        ), None)
        thresholds[f"at_least_{percent}_percent_nvme_bytes_avoided"] = (
            {"reached": True, "smallest_capacity_gib": selected["capacity_gib"],
             "observed_fraction": selected["replay"]["windows"]["decode"]["hit_ratio_by_useful_bytes"]}
            if selected else {"reached": False, "smallest_capacity_gib": None, "observed_fraction": None}
        )

    replay_digest = canonical_sha256({
        "route_sha256": [capture["route"]["sha256"] for capture in captures],
        "semantics": "GLOBAL_LRU_ALWAYS_ONE_DEMAND_PER_REQUEST_TOKEN_LAYER_RANK",
        "bundle_bytes": BUNDLE_BYTES,
        "capacities": [
            {
                "capacity_gib": row["capacity_gib"],
                "status": row["support"]["status"],
                "digest": row.get("replay", {}).get("deterministic_replay_digest"),
                "state": row.get("replay", {}).get("final_lru_state_sha256"),
            }
            for row in rows
        ],
    })
    document = {
        "schema_version": "phase12-nvme-real-route-cache-locality-v1",
        "status": "PASS",
        "disposition": "accepted_evidence_only",
        "scope": "CPU-only real Kimi-K3 routing locality under existing project global LRU plus ALWAYS admission",
        "model_revision": captures[0]["model_revision"],
        "colibri_commit": captures[0]["colibri_commit"],
        "routing_corpus": {
            "captures": captures,
            "request_count": len(captures),
            "complete_decode_token_forwards": len(decode_tokens),
            "expert_requests": len(events),
            "useful_expert_bytes": len(events) * BUNDLE_BYTES,
            "ordering": "request, phase (prefill then decode), token, layer, rank, expert_id",
            "top_k": TOP_K,
            "routed_layers": ROUTED_LAYERS,
            "expert_bundle_bytes": BUNDLE_BYTES,
        },
        "policy": {
            "policy": "LRU",
            "scope": "GLOBAL",
            "admission": "ALWAYS",
            "capacity_unit": "binary GiB converted to floor(capacity_bytes / exact useful expert bundle bytes)",
            "initial_state": "empty",
            "request_boundary": "cache persists across captured requests in request-id order",
            "cold_start_window": (
                f"empty-cache prefill is reported separately; decode-cold is the first {COLD_DECODE_TOKENS} "
                "complete decode-token forwards after that real prefill"
            ),
            "steady_state_window": f"remaining {len(decode_tokens) - COLD_DECODE_TOKENS} complete decode-token forwards",
            "default_or_policy_change": False,
        },
        "ram_support_method": {
            "host_memory": host["memory"],
            "declared_safe_memory_ceiling_bytes": safe_ceiling,
            "accepted_colibri_max_rss_bytes": observed_rss,
            "accepted_colibri_cache_slots": existing_slots,
            "accepted_colibri_cache_usable_bytes": existing_cache_bytes,
            "inferred_non_cache_rss_bytes": inferred_non_cache_rss,
            "method": "accepted max RSS minus the exact configured expert-slot footprint, plus each replay capacity",
        },
        "capacity_curve": rows,
        "reuse": reuse,
        "nvme_avoidance_thresholds": thresholds,
        "service_envelopes": {
            "single_nvme": {
                "central_gbps": frozen_single,
                "source": identity(args.baseline_analysis),
                "observed_values_gbps": single_observed,
            },
            "dual_nvme": {
                "central_gbps": dual_central,
                "source": identity(args.dual_comparison),
                "observed_values_gbps": dual_observed,
            },
        },
        "actual_colibri_reference": {
            "source": identity(args.colibri_reference),
            "decode_tokens_per_second": colibri_reference["metrics"]["decode_tokens_per_second"],
            "comparison_boundary": "separate actual external-runtime observation; not used as project TPS or projection",
        },
        "deterministic_replay_digest": replay_digest,
        "bounded_resources": {
            "algorithm": "O(events log unique_keys) exact reuse distance plus six O(events) bounded LRU replays",
            "payload_bytes_allocated": 0,
            "event_count": len(events),
            "maximum_simulated_slots": max(capacity_slots.values()),
            "wall_seconds": time.monotonic() - started,
            "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        },
        "interpretation": {
            "one_decode_forward_working_set": {
                "expert_bundles": ROUTED_LAYERS * TOP_K,
                "useful_bytes": ROUTED_LAYERS * TOP_K * BUNDLE_BYTES,
                "conclusion": (
                    "8 and 16 GiB hold fewer than one complete decode forward, so global LRU churns every "
                    "entry before its next eligible reuse and records zero hits"
                ),
            },
            "reuse_distance": {
                "decode_median_distinct_experts": reuse["decode"]["stack_distance_distinct_experts"]["p50"],
                "decode_median_equivalent_useful_bytes": (
                    int(reuse["decode"]["stack_distance_distinct_experts"]["p50"]) * BUNDLE_BYTES
                ),
                "conclusion": (
                    "32 GiB captures only the shortest real-route reuse tail; 64 GiB remains below the median "
                    "stack distance; 96 GiB exceeds it, producing the observed nonlinear capacity curve"
                ),
            },
            "equal_bundle_sizes": (
                "count-hit and useful-byte/NVMe-avoidance ratios coincide numerically because every captured K3 "
                "expert bundle is exactly 17,547,264 useful bytes; both are recorded independently"
            ),
            "claim_boundary": (
                "the curve measures real-route NVMe avoidance only; it selects no new policy, default, automatic "
                "capacity, storage format, linear end-to-end scaling, or project TPS claim"
            ),
        },
        "phase12_action": (
            "use the frozen curve to choose explicit RAM/VRAM capacities, rerun the storage shortlist, and validate "
            "actual H2D/GPU/overlap and end-to-end gains on real NVMe plus discrete CUDA"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"],
        "decode_forwards": len(decode_tokens),
        "replay_digest": replay_digest,
        "thresholds": thresholds,
        "output": str(args.output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Deterministic GGML/CUDA-independent cache simulation for Phase 2 traces."""

from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SIMULATION_MANIFEST_VERSION = "phase2-simulation-manifest-v1"
SIMULATION_OUTPUT_VERSION = "phase2-simulation-output-v1"
POLICIES = ("lru", "belady_min")
PHASES = ("PREFILL", "DECODE")
TIERS = ("hot", "cold", "backing_store")


class SimulationError(ValueError):
    """Raised when simulation input or configuration is inconsistent."""


@dataclass(frozen=True, order=True)
class ExpertKey:
    layer: int
    expert_id: int


@dataclass(frozen=True)
class ExpertRequest:
    key: ExpertKey
    size_bytes: int
    phase: str


@dataclass(frozen=True)
class Capacity:
    slots: int
    bytes: int

    @classmethod
    def from_json(cls, value: dict[str, Any], name: str) -> "Capacity":
        if set(value) != {"slots", "bytes"}:
            raise SimulationError(f"{name} capacity fields do not match version 1")
        if not all(isinstance(value[field], int) and value[field] >= 0 for field in value):
            raise SimulationError(f"{name} capacity must use non-negative integers")
        return cls(slots=value["slots"], bytes=value["bytes"])

    def can_hold(self, size_bytes: int) -> bool:
        return self.slots > 0 and size_bytes <= self.bytes


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _validate_model_identity(trace: dict[str, Any], storage_map: dict[str, Any]) -> None:
    header = trace.get("header", {})
    model = storage_map.get("model", {})
    comparisons = {
        "name": (header.get("model_name"), model.get("name")),
        "size": (header.get("model_size"), model.get("size")),
        "sha256": (header.get("model_sha256"), model.get("sha256")),
        "source_revision": (header.get("model_source_revision"), model.get("source_revision")),
        "published_gguf_revision": (
            header.get("published_gguf_revision"),
            model.get("published_gguf_revision"),
        ),
    }
    mismatches = [name for name, pair in comparisons.items() if pair[0] != pair[1]]
    if mismatches:
        raise SimulationError(
            f"trace and storage-map model identity differ: {', '.join(mismatches)}"
        )


def requests_from_trace(
    trace: dict[str, Any], storage_map: dict[str, Any]
) -> list[ExpertRequest]:
    """Flatten canonical trace records into atomic expert-bundle requests."""
    if storage_map.get("schema_version") != "expert-storage-map-v1":
        raise SimulationError("unsupported expert storage-map version")
    _validate_model_identity(trace, storage_map)
    sizes: dict[ExpertKey, int] = {}
    for entry in storage_map.get("entries", []):
        key = ExpertKey(entry.get("layer"), entry.get("expert_id"))
        size = entry.get("atomic_bundle_bytes")
        if (
            key in sizes
            or not isinstance(key.layer, int)
            or key.layer < 0
            or not isinstance(key.expert_id, int)
            or key.expert_id < 0
            or not isinstance(size, int)
            or size <= 0
        ):
            raise SimulationError("storage map contains a duplicate or invalid expert bundle")
        sizes[key] = size
    if not sizes:
        raise SimulationError("storage map contains no expert bundles")

    requests = []
    for record in trace.get("records", []):
        phase = record.get("phase")
        selected = record.get("selected_experts")
        if phase not in PHASES or not isinstance(selected, list) or not selected:
            raise SimulationError("trace contains an invalid phase or selected-expert list")
        if len(set(selected)) != len(selected):
            raise SimulationError("trace record selects the same expert more than once")
        for expert_id in selected:
            key = ExpertKey(record.get("layer"), expert_id)
            if key not in sizes:
                raise SimulationError(f"trace references unmapped expert {key}")
            requests.append(ExpertRequest(key=key, size_bytes=sizes[key], phase=phase))
    if not requests:
        raise SimulationError("trace contains no expert requests")
    return requests


class FutureUses:
    def __init__(self, requests: list[ExpertRequest]):
        self.positions: dict[ExpertKey, deque[int]] = defaultdict(deque)
        for index, request in enumerate(requests):
            self.positions[request.key].append(index)

    def consume(self, key: ExpertKey, index: int) -> None:
        positions = self.positions[key]
        if not positions or positions[0] != index:
            raise SimulationError("oracle future-use index is inconsistent")
        positions.popleft()

    def next(self, key: ExpertKey) -> float:
        positions = self.positions.get(key)
        return float(positions[0]) if positions else math.inf


class TierCache:
    def __init__(self, capacity: Capacity, policy: str, future: FutureUses | None):
        self.capacity = capacity
        self.policy = policy
        self.future = future
        self.items: OrderedDict[ExpertKey, int] = OrderedDict()
        self.total_bytes = 0

    def __contains__(self, key: ExpertKey) -> bool:
        return key in self.items

    def touch(self, key: ExpertKey) -> None:
        if self.policy == "lru" and key in self.items:
            self.items.move_to_end(key)

    def remove(self, key: ExpertKey) -> bool:
        size = self.items.pop(key, None)
        if size is None:
            return False
        self.total_bytes -= size
        return True

    def _over_capacity(self, items: dict[ExpertKey, int]) -> bool:
        return len(items) > self.capacity.slots or sum(items.values()) > self.capacity.bytes

    def admit(self, key: ExpertKey, size_bytes: int) -> tuple[bool, list[ExpertKey]]:
        if key in self.items:
            self.touch(key)
            return False, []
        if not self.capacity.can_hold(size_bytes):
            return False, []

        candidates = dict(self.items)
        candidates[key] = size_bytes
        evicted: list[ExpertKey] = []
        while self._over_capacity(candidates):
            if self.policy == "lru":
                victim = next(iter(self.items))
            else:
                if self.future is None:
                    raise SimulationError("Belady/MIN requires future-use state")
                victim = max(candidates, key=lambda item: (self.future.next(item), item))
            del candidates[victim]
            if victim != key:
                evicted.append(victim)
                self.remove(victim)
            elif self.policy == "lru":
                raise SimulationError("LRU attempted to evict the incoming item")

        if key not in candidates:
            return False, evicted
        self.items[key] = size_bytes
        self.total_bytes += size_bytes
        self.touch(key)
        return True, evicted


def _empty_activity() -> dict[str, Any]:
    return {
        "logical_requests": 0,
        "tiers": {
            tier: {
                "requests": 0,
                "hits": 0,
                "misses": 0,
                "bytes_requested": 0,
                "bytes_transferred": 0,
            }
            for tier in TIERS
        },
        "cache_activity": {
            "hot": {"admissions": 0, "evictions": 0},
            "cold": {"admissions": 0, "evictions": 0},
        },
    }


def _increment(activity: dict[str, Any], path: tuple[str, ...], amount: int = 1) -> None:
    target = activity
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] += amount


def _record_lookup(activity: dict[str, Any], source: str, size_bytes: int) -> None:
    activity["logical_requests"] += 1
    for tier in TIERS:
        metrics = activity["tiers"][tier]
        metrics["requests"] += 1
        metrics["bytes_requested"] += size_bytes
        if tier == source:
            metrics["hits"] += 1
            metrics["bytes_transferred"] += size_bytes
            break
        metrics["misses"] += 1


def _nearest_rank(values: list[float | int], percentile: float) -> float | int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def reuse_distances(requests: list[ExpertRequest]) -> list[int | None]:
    """Return distinct-key reuse distance for every request in O(n log n)."""
    tree = [0] * (len(requests) + 1)

    def add(index: int, delta: int) -> None:
        index += 1
        while index < len(tree):
            tree[index] += delta
            index += index & -index

    def prefix(index: int) -> int:
        total = 0
        while index > 0:
            total += tree[index]
            index -= index & -index
        return total

    last: dict[ExpertKey, int] = {}
    result: list[int | None] = []
    for index, request in enumerate(requests):
        previous = last.get(request.key)
        if previous is None:
            result.append(None)
        else:
            result.append(prefix(index) - prefix(previous + 1))
            add(previous, -1)
        add(index, 1)
        last[request.key] = index
    return result


def _reuse_report(values: Iterable[int | None]) -> dict[str, Any]:
    values = list(values)
    finite = [value for value in values if value is not None]
    histogram: dict[str, int] = {"cold": len(values) - len(finite)}
    for value in finite:
        name = str(value)
        histogram[name] = histogram.get(name, 0) + 1
    return {
        "definition": (
            "number of distinct atomic expert bundles referenced since the previous reference"
        ),
        "cold_references": len(values) - len(finite),
        "finite_references": len(finite),
        "histogram": dict(
            sorted(
                histogram.items(),
                key=lambda item: (
                    item[0] != "cold",
                    int(item[0]) if item[0] != "cold" else -1,
                ),
            )
        ),
        "p50": _nearest_rank(finite, 0.50),
        "p95": _nearest_rank(finite, 0.95),
        "p99": _nearest_rank(finite, 0.99),
    }


def _skew_report(requests: Iterable[ExpertRequest]) -> dict[str, Any]:
    counts: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for request in requests:
        counts[request.key.layer][request.key.expert_id] += 1
    result = {}
    for layer in sorted(counts):
        experts = counts[layer]
        total = sum(experts.values())
        result[str(layer)] = {
            "requests": total,
            "experts": {str(expert): experts[expert] for expert in sorted(experts)},
            "maximum_expert_share": max(experts.values()) / total,
        }
    return result


def _stall_report(values: Iterable[float], cost_model: dict[str, Any]) -> dict[str, Any]:
    values = list(values)
    return {
        "unit": "microseconds",
        "overlap_model": cost_model["overlap_model"],
        "samples": len(values),
        "total": sum(values),
        "mean": statistics_fmean(values),
        "p50": _nearest_rank(values, 0.50),
        "p95": _nearest_rank(values, 0.95),
        "p99": _nearest_rank(values, 0.99),
    }


def statistics_fmean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _finalize_activity(activity: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(activity))
    logical_requests = result["logical_requests"]
    for tier in TIERS:
        metrics = result["tiers"][tier]
        requests = metrics["requests"]
        metrics["hit_rate"] = metrics["hits"] / requests if requests else None
        metrics["miss_rate"] = metrics["misses"] / requests if requests else None
    result["backing_store_request_rate"] = (
        result["tiers"]["backing_store"]["requests"] / logical_requests
        if logical_requests else None
    )
    return result


def _validate_cost_model(value: dict[str, Any]) -> dict[str, Any]:
    if value.get("overlap_model") != "serial_no_overlap":
        raise SimulationError("version 1 supports only explicit serial_no_overlap cost accounting")
    tiers = value.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != set(TIERS):
        raise SimulationError("cost model must define hot, cold, and backing_store tiers")
    for tier, parameters in tiers.items():
        if set(parameters) != {"fixed_latency_us", "bandwidth_bytes_per_second"}:
            raise SimulationError(f"{tier} cost fields do not match version 1")
        latency = parameters["fixed_latency_us"]
        bandwidth = parameters["bandwidth_bytes_per_second"]
        if not isinstance(latency, (int, float)) or latency < 0:
            raise SimulationError(f"{tier} fixed latency must be non-negative")
        if not isinstance(bandwidth, (int, float)) or bandwidth <= 0:
            raise SimulationError(f"{tier} bandwidth must be positive")
    return value


def _stall_us(source: str, size_bytes: int, cost_model: dict[str, Any]) -> float:
    parameters = cost_model["tiers"][source]
    return (
        parameters["fixed_latency_us"]
        + size_bytes / parameters["bandwidth_bytes_per_second"] * 1e6
    )


def simulate_policy(
    requests: list[ExpertRequest],
    hot_capacity: Capacity,
    cold_capacity: Capacity,
    cost_model: dict[str, Any],
    policy: str,
) -> dict[str, Any]:
    if policy not in POLICIES:
        raise SimulationError(f"unsupported policy: {policy}")
    if hot_capacity.slots > cold_capacity.slots or hot_capacity.bytes > cold_capacity.bytes:
        raise SimulationError("inclusive hot capacity cannot exceed cold capacity")
    cost_model = _validate_cost_model(cost_model)
    if policy == "belady_min" and len({request.size_bytes for request in requests}) != 1:
        raise SimulationError(
            "Belady/MIN exact lower-bound mode requires equal-sized referenced expert bundles"
        )

    future = FutureUses(requests) if policy == "belady_min" else None
    hot = TierCache(hot_capacity, policy, future)
    cold = TierCache(cold_capacity, policy, future)
    overall = _empty_activity()
    phase_activity = {phase: _empty_activity() for phase in PHASES}
    overall_stalls: list[float] = []
    phase_stalls: dict[str, list[float]] = {phase: [] for phase in PHASES}
    distances = reuse_distances(requests)

    peak = {"hot_slots": 0, "hot_bytes": 0, "cold_slots": 0, "cold_bytes": 0}
    for index, request in enumerate(requests):
        if future is not None:
            future.consume(request.key, index)

        if request.key in hot:
            source = "hot"
            hot.touch(request.key)
            cold.touch(request.key)
        elif request.key in cold:
            source = "cold"
            cold.touch(request.key)
            admitted, evicted = hot.admit(request.key, request.size_bytes)
            if admitted:
                for activity in (overall, phase_activity[request.phase]):
                    _increment(activity, ("cache_activity", "hot", "admissions"))
            for _ in evicted:
                for activity in (overall, phase_activity[request.phase]):
                    _increment(activity, ("cache_activity", "hot", "evictions"))
        else:
            source = "backing_store"
            admitted, evicted = cold.admit(request.key, request.size_bytes)
            if admitted:
                for activity in (overall, phase_activity[request.phase]):
                    _increment(activity, ("cache_activity", "cold", "admissions"))
            for victim in evicted:
                for activity in (overall, phase_activity[request.phase]):
                    _increment(activity, ("cache_activity", "cold", "evictions"))
                if hot.remove(victim):
                    for activity in (overall, phase_activity[request.phase]):
                        _increment(activity, ("cache_activity", "hot", "evictions"))
            if request.key in cold:
                admitted, evicted = hot.admit(request.key, request.size_bytes)
                if admitted:
                    for activity in (overall, phase_activity[request.phase]):
                        _increment(activity, ("cache_activity", "hot", "admissions"))
                for _ in evicted:
                    for activity in (overall, phase_activity[request.phase]):
                        _increment(activity, ("cache_activity", "hot", "evictions"))

        for activity in (overall, phase_activity[request.phase]):
            _record_lookup(activity, source, request.size_bytes)
        stall = _stall_us(source, request.size_bytes, cost_model)
        overall_stalls.append(stall)
        phase_stalls[request.phase].append(stall)
        peak["hot_slots"] = max(peak["hot_slots"], len(hot.items))
        peak["hot_bytes"] = max(peak["hot_bytes"], hot.total_bytes)
        peak["cold_slots"] = max(peak["cold_slots"], len(cold.items))
        peak["cold_bytes"] = max(peak["cold_bytes"], cold.total_bytes)
        if not set(hot.items).issubset(cold.items):
            raise SimulationError("inclusive hierarchy invariant failed")

    def report_for(
        indices: list[int], activity: dict[str, Any], stalls: list[float]
    ) -> dict[str, Any]:
        selected_requests = [requests[index] for index in indices]
        return {
            **_finalize_activity(activity),
            "reuse_distance": _reuse_report(distances[index] for index in indices),
            "per_layer_expert_skew": _skew_report(selected_requests),
            "theoretical_stall": _stall_report(stalls, cost_model),
        }

    result = {
        "policy": policy,
        "policy_classification": (
            "deterministic test baseline"
            if policy == "lru"
            else "perfect-future offline lower bound; not a production policy"
        ),
        "overall": report_for(list(range(len(requests))), overall, overall_stalls),
        "by_phase": {
            phase: report_for(
                [index for index, request in enumerate(requests) if request.phase == phase],
                phase_activity[phase],
                phase_stalls[phase],
            )
            for phase in PHASES
        },
        "final_cache_state": {
            "hot": {
                "resident_slots": len(hot.items),
                "resident_bytes": hot.total_bytes,
                "peak_slots": peak["hot_slots"],
                "peak_bytes": peak["hot_bytes"],
            },
            "cold": {
                "resident_slots": len(cold.items),
                "resident_bytes": cold.total_bytes,
                "peak_slots": peak["cold_slots"],
                "peak_bytes": peak["cold_bytes"],
            },
        },
    }
    return result


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if set(manifest) != {"schema_version", "description", "cost_model", "scenarios"}:
        raise SimulationError("simulation manifest fields do not match version 1")
    if manifest.get("schema_version") != SIMULATION_MANIFEST_VERSION:
        raise SimulationError("unsupported simulation manifest version")
    if not isinstance(manifest["description"], str) or not manifest["description"]:
        raise SimulationError("simulation manifest description must be non-empty")
    cost_model = _validate_cost_model(manifest.get("cost_model", {}))
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise SimulationError("simulation manifest requires at least one scenario")
    names = set()
    validated = []
    for scenario in scenarios:
        if set(scenario) != {"name", "hot_capacity", "cold_capacity"}:
            raise SimulationError("scenario fields do not match version 1")
        name = scenario["name"]
        if not isinstance(name, str) or not name or name in names:
            raise SimulationError("scenario names must be non-empty and unique")
        names.add(name)
        hot = Capacity.from_json(scenario["hot_capacity"], "hot")
        cold = Capacity.from_json(scenario["cold_capacity"], "cold")
        if hot.slots > cold.slots or hot.bytes > cold.bytes:
            raise SimulationError("inclusive hot capacity cannot exceed cold capacity")
        validated.append((name, hot, cold))
    return {
        "description": manifest["description"],
        "cost_model": cost_model,
        "scenarios": validated,
    }


def simulate_manifest(
    trace: dict[str, Any],
    storage_map: dict[str, Any],
    manifest: dict[str, Any],
    trace_sha256: str,
    storage_map_sha256: str,
    manifest_sha256: str,
) -> dict[str, Any]:
    requests = requests_from_trace(trace, storage_map)
    validated = validate_manifest(manifest)
    return {
        "schema_version": SIMULATION_OUTPUT_VERSION,
        "inputs": {
            "trace": {
                "sha256": trace_sha256,
                "schema_version": trace["header"]["schema_version"],
                "run_id": trace["header"]["run_id"],
                "records": len(trace["records"]),
                "expert_requests": len(requests),
                "llama_cpp_revision": trace["header"]["llama_cpp_revision"],
            },
            "storage_map": {
                "sha256": storage_map_sha256,
                "schema_version": storage_map["schema_version"],
                "entries": len(storage_map["entries"]),
                "llama_cpp_revision": storage_map["model"]["llama_cpp_revision"],
            },
            "manifest_sha256": manifest_sha256,
            "model": storage_map["model"],
        },
        "accounting_semantics": {
            "hierarchy": "inclusive: every hot resident must also be cold-resident",
            "request_cascade": "hot -> cold -> backing_store",
            "bytes_requested": "atomic bundle bytes presented to each tier reached by the cascade",
            "bytes_transferred": "atomic bundle bytes served by the first tier that hits",
            "phase_state": (
                "cache residency carries across phases; metrics are attributed to the phase of each request"
            ),
            "theoretical_stall": (
                "source-tier fixed latency plus bytes/bandwidth under serial_no_overlap"
            ),
        },
        "manifest_description": validated["description"],
        "cost_model": validated["cost_model"],
        "scenarios": [
            {
                "name": name,
                "hot_capacity": {"slots": hot.slots, "bytes": hot.bytes},
                "cold_capacity": {"slots": cold.slots, "bytes": cold.bytes},
                "policies": {
                    policy: simulate_policy(requests, hot, cold, validated["cost_model"], policy)
                    for policy in POLICIES
                },
            }
            for name, hot, cold in validated["scenarios"]
        ],
    }

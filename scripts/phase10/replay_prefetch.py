#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prefetch_common import (FNV_OFFSET, Phase10Error, config_digest, cross_candidates, fnv_append, load_json,
    predictor_candidates, require_fields, require_uint, validate_profile, write_json)


UINT64_MAX = (1 << 64) - 1
LIMIT_FIELDS = {"cold_capacity_bytes", "hot_capacity_slots", "max_speculative_flights",
    "max_speculative_storage_bytes_in_flight", "max_speculative_h2d_bytes_in_flight",
    "max_speculative_storage_bytes_per_token", "max_speculative_h2d_bytes_per_token",
    "max_speculative_cold_slots", "max_speculative_hot_slots"}


@dataclass
class Entry:
    layer: int
    expert: int
    payload_bytes: int
    physical_bytes: int
    cold_slot: int
    hot_slot: int
    origin: str
    deadline_token: int
    deadline_layer: int
    score: int
    flight: int
    last_touch: int
    phase: str


def validate_limits(value: Any, active: bool) -> dict[str, int]:
    require_fields(value, LIMIT_FIELDS, "limits")
    limits = {name: require_uint(value[name], name, maximum=UINT64_MAX) for name in LIMIT_FIELDS}
    if limits["cold_capacity_bytes"] == 0 or limits["hot_capacity_slots"] == 0:
        raise Phase10Error("cache replay capacity must be nonzero")
    speculative = LIMIT_FIELDS - {"cold_capacity_bytes", "hot_capacity_slots"}
    if active and any(limits[name] == 0 for name in speculative):
        raise Phase10Error("active replay speculative limits must be nonzero")
    if not active and any(limits[name] != 0 for name in speculative):
        raise Phase10Error("disabled replay speculative limits must be zero")
    return limits


def simulate(
        profile: dict[str, Any],
        events: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        transport: str,
        readiness: str,
        limits: dict[str, int],
        seed_mode: str,
        demand_mode: str) -> dict[str, Any]:
    sizes = {(item["layer"], item["expert"]): (item["payload_bytes"], item["physical_bytes"])
        for item in profile["target"]["expert_bytes"]}
    minimum_physical = min(item[1] for item in sizes.values())
    cold_slot_capacity = limits["cold_capacity_bytes"]//minimum_physical
    if cold_slot_capacity == 0 or cold_slot_capacity > (1 << 31) - 1 or \
            limits["hot_capacity_slots"] > (1 << 31) - 1:
        raise Phase10Error("cold capacity cannot hold one expert")
    layers = profile["target"]["routed_layers"]
    layer_indices = {layer: index for index, layer in enumerate(layers)}
    cost = next((item for item in profile["costs"] if item["transport"] == transport and
        item["readiness"] == readiness), None)
    if cost is None:
        raise Phase10Error("replay transport/readiness cost is unavailable")
    candidate_batches: dict[tuple[int, str, int], list[dict[str, Any]]] = {}
    for flight, item in enumerate(candidates):
        item["flight_ordinal"] = flight
        batch_layer = item["source_layer"] if item["trigger"] == "ROUTER_RESULT" else -1
        candidate_batches.setdefault((item["trigger_token"], item["trigger"], batch_layer), []).append(item)
    entries: dict[tuple[int, int], Entry] = {}
    actions: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    token_storage: dict[int, int] = {}
    token_h2d: dict[int, int] = {}
    clock = 0
    digest = FNV_OFFSET
    counters = {"predictions": len(candidates), "accepted": 0, "timely_useful": 0, "late_joined": 0,
        "wasted_unused": 0, "cancelled_before_io": 0, "cancelled_drained": 0, "rejected": 0,
        "demand_keys": 0, "demand_hits": 0, "demand_loads": 0, "speculative_replacements": 0,
        "prevented_demand_evictions": 0, "storage_bytes": 0, "h2d_bytes": 0, "wasted_storage_bytes": 0,
        "wasted_h2d_bytes": 0}

    def emit(container: list[dict[str, Any]], record: dict[str, Any]) -> None:
        nonlocal digest
        record = {"sequence": len(actions) + len(outcomes), **record}
        container.append(record)
        codes = {"DEMAND_ISSUE": 1, "DEMAND_HIT": 2, "DEMAND_LOAD": 3, "ENQUEUE": 4, "READY": 5,
            "REJECTED": 6, "EVICT": 7, "TIMELY_USEFUL": 8, "LATE_JOINED": 9, "WASTED_UNUSED": 10,
            "SEED_LOAD": 11, "SEED_TOUCH": 12, "DEMAND_PROMOTE": 13,
            "CANCELLED_BEFORE_IO": 14, "CANCELLED_DRAINED": 15}
        digest = fnv_append(digest, codes[record["type"]])
        for name in ("flight_ordinal", "token", "layer", "expert", "cold_slot", "hot_slot"):
            value = record.get(name, -1)
            digest = fnv_append(digest, UINT64_MAX if value < 0 else value)

    def occupied_bytes() -> int:
        return sum(entry.physical_bytes for entry in entries.values() if entry.cold_slot >= 0)

    def free_slot(attribute: str, capacity: int) -> int:
        used = {getattr(entry, attribute) for entry in entries.values() if getattr(entry, attribute) >= 0}
        return next((slot for slot in range(capacity) if slot not in used), -1)

    def discard(key: tuple[int, int], reason: str) -> None:
        entry = entries.pop(key)
        if entry.origin == "SPECULATIVE":
            if entry.phase == "QUEUED":
                outcome = "CANCELLED_BEFORE_IO"
                counters["cancelled_before_io"] += 1
            elif entry.phase == "SUBMITTED":
                outcome = "CANCELLED_DRAINED"
                counters["cancelled_drained"] += 1
                counters["wasted_storage_bytes"] += entry.physical_bytes
                counters["wasted_h2d_bytes"] += entry.payload_bytes if readiness == "DEVICE_READY" else 0
            else:
                outcome = "WASTED_UNUSED"
                counters["wasted_unused"] += 1
                counters["wasted_storage_bytes"] += entry.physical_bytes
                counters["wasted_h2d_bytes"] += entry.payload_bytes if readiness == "DEVICE_READY" else 0
            emit(outcomes, {"type": outcome, "flight_ordinal": entry.flight,
                "token": entry.deadline_token, "layer": entry.layer, "expert": entry.expert,
                "cold_slot": entry.cold_slot, "hot_slot": entry.hot_slot, "reason": reason,
                "completion_phase": entry.phase})

    def demand_victim(tier: str) -> tuple[int, int] | None:
        resident = [(key, entry) for key, entry in entries.items()
            if (entry.cold_slot if tier == "cold" else entry.hot_slot) >= 0]
        if not resident:
            return None
        return min(resident, key=lambda item: (item[1].last_touch, item[1].layer, item[1].expert))[0]

    def ensure_demand_capacity(payload: int, physical: int) -> tuple[int, int]:
        while occupied_bytes() + physical > limits["cold_capacity_bytes"] or \
                free_slot("cold_slot", cold_slot_capacity) < 0:
            victim = demand_victim("cold")
            if victim is None:
                raise Phase10Error("mandatory cold demand cannot be admitted")
            discard(victim, "demand_cold_eviction")
        cold_slot = free_slot("cold_slot", cold_slot_capacity)
        while free_slot("hot_slot", limits["hot_capacity_slots"]) < 0:
            victim = demand_victim("hot")
            if victim is None:
                raise Phase10Error("mandatory hot demand cannot be admitted")
            victim_entry = entries[victim]
            if victim_entry.origin == "SPECULATIVE":
                discard(victim, "demand_hot_eviction")
            else:
                victim_entry.hot_slot = -1
        return cold_slot, free_slot("hot_slot", limits["hot_capacity_slots"])

    def consume_demands(token: int, layer: int, selected: list[int]) -> None:
        nonlocal clock
        if demand_mode == "ISSUE_AHEAD":
            for expert in selected:
                emit(actions, {"type": "DEMAND_ISSUE", "flight_ordinal": -1, "token": token,
                    "layer": layer, "expert": expert, "cold_slot": -1, "hot_slot": -1,
                    "priority": "DEMAND_CURRENT_LAYER"})
        for expert in selected:
            if demand_mode == "SERIAL":
                emit(actions, {"type": "DEMAND_ISSUE", "flight_ordinal": -1, "token": token,
                    "layer": layer, "expert": expert, "cold_slot": -1, "hot_slot": -1,
                    "priority": "DEMAND_CURRENT_LAYER"})
            clock += 1
            key = (layer, expert)
            counters["demand_keys"] += 1
            entry = entries.get(key)
            if entry is not None and (entry.origin != "SPECULATIVE" or entry.phase == "READY"):
                source_origin = entry.origin
                if entry.origin == "SPECULATIVE":
                    counters["timely_useful"] += 1
                    emit(outcomes, {"type": "TIMELY_USEFUL", "flight_ordinal": entry.flight, "token": token,
                        "layer": layer, "expert": expert, "cold_slot": entry.cold_slot,
                        "hot_slot": entry.hot_slot, "reason": "ready_before_demand"})
                    entry.origin = "DEMAND"
                elif entry.origin == "STATIC_SEED":
                    entry.origin = "DEMAND"
                if entry.hot_slot < 0:
                    while free_slot("hot_slot", limits["hot_capacity_slots"]) < 0:
                        victim = demand_victim("hot")
                        if victim is None:
                            raise Phase10Error("mandatory hot promotion cannot be admitted")
                        victim_entry = entries[victim]
                        if victim_entry.origin == "SPECULATIVE":
                            discard(victim, "demand_hot_eviction")
                        else:
                            victim_entry.hot_slot = -1
                    entry.hot_slot = free_slot("hot_slot", limits["hot_capacity_slots"])
                entry.last_touch = clock
                counters["demand_hits"] += 1
                emit(actions, {"type": "DEMAND_HIT", "flight_ordinal": entry.flight, "token": token,
                    "layer": layer, "expert": expert, "cold_slot": entry.cold_slot, "hot_slot": entry.hot_slot,
                    "source_origin": source_origin})
                continue
            if entry is not None and entry.origin == "SPECULATIVE":
                source_phase = entry.phase
                counters["late_joined"] += 1
                counters["demand_loads"] += 1
                emit(outcomes, {"type": "LATE_JOINED", "flight_ordinal": entry.flight, "token": token,
                    "layer": layer, "expert": expert, "cold_slot": entry.cold_slot, "hot_slot": entry.hot_slot,
                    "reason": "demand_promoted_exact_generation", "completion_phase": source_phase})
                entry.origin = "DEMAND"
                entry.phase = "READY"
                if entry.hot_slot < 0:
                    while free_slot("hot_slot", limits["hot_capacity_slots"]) < 0:
                        victim = demand_victim("hot")
                        if victim is None:
                            raise Phase10Error("mandatory late promotion cannot be admitted")
                        victim_entry = entries[victim]
                        if victim_entry.origin == "SPECULATIVE":
                            discard(victim, "demand_hot_eviction")
                        else:
                            victim_entry.hot_slot = -1
                    entry.hot_slot = free_slot("hot_slot", limits["hot_capacity_slots"])
                entry.last_touch = clock
                emit(actions, {"type": "DEMAND_PROMOTE", "flight_ordinal": entry.flight, "token": token,
                    "layer": layer, "expert": expert, "cold_slot": entry.cold_slot, "hot_slot": entry.hot_slot,
                    "source_phase": source_phase, "priority": "DEMAND_CURRENT_LAYER"})
                continue
            if entry is not None:
                discard(key, "demand_replaced_unready")
            payload, physical = sizes[key]
            cold_slot, hot_slot = ensure_demand_capacity(payload, physical)
            entries[key] = Entry(layer, expert, payload, physical, cold_slot, hot_slot, "DEMAND",
                token, layer, 0, -1, clock, "READY")
            counters["demand_loads"] += 1
            emit(actions, {"type": "DEMAND_LOAD", "flight_ordinal": -1, "token": token,
                "layer": layer, "expert": expert, "cold_slot": cold_slot, "hot_slot": hot_slot})

    def speculative_victims(
            required_physical: int,
            needs_hot: bool,
            require_cold_victim: bool,
            require_hot_victim: bool,
            excluded: set[tuple[int, int]]) -> list[tuple[int, int]] | None:
        victims: list[tuple[int, int]] = []
        simulated_bytes = occupied_bytes()
        cold_free = free_slot("cold_slot", cold_slot_capacity) >= 0 and not require_cold_victim
        hot_free = not needs_hot or (free_slot("hot_slot", limits["hot_capacity_slots"]) >= 0 and
            not require_hot_victim)
        available = sorted(((key, entry) for key, entry in entries.items()
            if entry.origin == "SPECULATIVE" and key not in excluded),
            key=lambda item: (item[1].deadline_token, layer_indices[item[1].deadline_layer],
                item[1].score, item[1].cold_slot, item[1].hot_slot))
        for key, entry in available:
            if simulated_bytes + required_physical <= limits["cold_capacity_bytes"] and cold_free and hot_free:
                break
            victims.append(key)
            simulated_bytes -= entry.physical_bytes
            cold_free = True
            if entry.hot_slot >= 0:
                hot_free = True
        if simulated_bytes + required_physical > limits["cold_capacity_bytes"] or not cold_free or not hot_free:
            return None
        return victims

    def submit_batch(batch: list[dict[str, Any]]) -> None:
        active_flights = [entry for entry in entries.values()
            if entry.origin == "SPECULATIVE" and entry.phase != "READY"]
        in_flight = len(active_flights)
        storage_in_flight = sum(entry.physical_bytes for entry in active_flights)
        h2d_in_flight = sum(entry.payload_bytes for entry in active_flights) if readiness == "DEVICE_READY" else 0
        accepted: list[tuple[tuple[int, int], Entry]] = []
        for candidate in batch:
            token = candidate["trigger_token"]
            deadline_token = token if candidate["trigger"] == "ROUTER_RESULT" else token + 1
            deadline_layer = candidate["target_layer"]
            key = (deadline_layer, candidate["expert"])
            payload, physical = sizes[key]
            h2d = payload if readiness == "DEVICE_READY" else 0
            reason = ""
            if key in entries:
                reason = "target_ready_or_higher_priority"
            elif in_flight + 1 > limits["max_speculative_flights"]:
                reason = "flight_budget"
            elif storage_in_flight + physical > limits["max_speculative_storage_bytes_in_flight"]:
                reason = "storage_in_flight_budget"
            elif h2d_in_flight + h2d > limits["max_speculative_h2d_bytes_in_flight"]:
                reason = "h2d_in_flight_budget"
            elif token_storage.get(token, 0) + physical > limits["max_speculative_storage_bytes_per_token"]:
                reason = "storage_token_budget"
            elif token_h2d.get(token, 0) + h2d > limits["max_speculative_h2d_bytes_per_token"]:
                reason = "h2d_token_budget"
            cold_speculative = sum(entry.origin == "SPECULATIVE" for entry in entries.values())
            hot_speculative = sum(entry.origin == "SPECULATIVE" and entry.hot_slot >= 0 for entry in entries.values())
            victims = None if reason else speculative_victims(physical, readiness == "DEVICE_READY",
                cold_speculative >= limits["max_speculative_cold_slots"],
                readiness == "DEVICE_READY" and hot_speculative >= limits["max_speculative_hot_slots"],
                {key for key, _ in accepted})
            if not reason and victims is None:
                reason = "demand_state_protected"
                counters["prevented_demand_evictions"] += 1
            if reason:
                counters["rejected"] += 1
                emit(actions, {"type": "REJECTED", "flight_ordinal": candidate["flight_ordinal"],
                    "token": token, "layer": key[0], "expert": key[1], "cold_slot": -1, "hot_slot": -1,
                    "reason": reason})
                continue
            for victim in victims or []:
                victim_entry = entries[victim]
                emit(actions, {"type": "EVICT", "flight_ordinal": victim_entry.flight, "token": token,
                    "layer": victim[0], "expert": victim[1], "cold_slot": victim_entry.cold_slot,
                    "hot_slot": victim_entry.hot_slot, "reason": "speculative_replacement"})
                counters["speculative_replacements"] += 1
                discard(victim, "speculative_replacement")
            cold_slot = free_slot("cold_slot", cold_slot_capacity)
            hot_slot = free_slot("hot_slot", limits["hot_capacity_slots"]) if readiness == "DEVICE_READY" else -1
            entry = Entry(key[0], key[1], payload, physical, cold_slot, hot_slot, "SPECULATIVE",
                deadline_token, deadline_layer, candidate["score"], candidate["flight_ordinal"], clock,
                "QUEUED" if cost["predictor_compute_ns"] >= cost["lead_ns"] else
                    ("SUBMITTED" if cost["predictor_compute_ns"] + cost["speculative_service_ns"] >
                        cost["lead_ns"] else "READY"))
            entries[key] = entry
            accepted.append((key, entry))
            in_flight += 1
            storage_in_flight += physical
            h2d_in_flight += h2d
            token_storage[token] = token_storage.get(token, 0) + physical
            token_h2d[token] = token_h2d.get(token, 0) + h2d
            counters["accepted"] += 1
            submitted_storage = 0 if entry.phase == "QUEUED" else physical
            submitted_h2d = 0 if entry.phase == "QUEUED" else h2d
            counters["storage_bytes"] += submitted_storage
            counters["h2d_bytes"] += submitted_h2d
            emit(actions, {"type": "ENQUEUE", "flight_ordinal": candidate["flight_ordinal"], "token": token,
                "layer": key[0], "expert": key[1], "cold_slot": cold_slot, "hot_slot": hot_slot,
                "deadline_token": deadline_token, "deadline_layer": deadline_layer,
                "priority": "PREFETCH_NEXT" if candidate["trigger"] == "ROUTER_RESULT" else "PREFETCH_SPECULATIVE",
                "storage_bytes": physical, "h2d_bytes": h2d, "submitted_storage_bytes": submitted_storage,
                "submitted_h2d_bytes": submitted_h2d, "completion_phase": entry.phase})
        for key, entry in accepted:
            if entry.phase == "READY":
                emit(actions, {"type": "READY", "flight_ordinal": entry.flight, "token": entry.deadline_token,
                    "layer": key[0], "expert": key[1], "cold_slot": entry.cold_slot, "hot_slot": entry.hot_slot,
                    "readiness": readiness})

    def resolve_deadline(token: int, layer: int) -> None:
        expired = sorted((key for key, entry in entries.items() if entry.origin == "SPECULATIVE" and
            entry.deadline_token == token and entry.deadline_layer == layer))
        for key in expired:
            discard(key, "deadline_unused")

    if seed_mode == "BLOCKING_HOT":
        seed = profile["seed"]
        seed_bytes = sum(item["physical_bytes"] for item in seed)
        if not seed or seed_bytes > limits["cold_capacity_bytes"] or len(seed) > limits["hot_capacity_slots"] or \
                len(seed) > cold_slot_capacity:
            raise Phase10Error("blocking seed does not fit exact replay capacity")
        for item in seed:
            clock += 1
            key = (item["layer"], item["expert"])
            cold_slot = free_slot("cold_slot", cold_slot_capacity)
            hot_slot = free_slot("hot_slot", limits["hot_capacity_slots"])
            entries[key] = Entry(item["layer"], item["expert"], item["payload_bytes"],
                item["physical_bytes"], cold_slot, hot_slot, "STATIC_SEED", 0, item["layer"],
                item["count"], -1, clock, "READY")
            emit(actions, {"type": "SEED_LOAD", "flight_ordinal": -1, "token": 0, "layer": item["layer"],
                "expert": item["expert"], "cold_slot": cold_slot, "hot_slot": hot_slot,
                "storage_bytes": item["physical_bytes"], "h2d_bytes": item["payload_bytes"]})
        highest = sorted(seed, key=lambda item: (-item["count"], item["layer"], item["expert"]))[0]
        clock += 1
        entries[(highest["layer"], highest["expert"])].last_touch = clock
        emit(actions, {"type": "SEED_TOUCH", "flight_ordinal": -1, "token": 0, "layer": highest["layer"],
            "expert": highest["expert"], "cold_slot": entries[(highest["layer"], highest["expert"])].cold_slot,
            "hot_slot": entries[(highest["layer"], highest["expert"])].hot_slot})

    for token, event in enumerate(events):
        for layer_record in event["layers"]:
            layer = layer_record["layer"]
            consume_demands(token, layer, layer_record["experts"])
            resolve_deadline(token, layer)
            submit_batch(candidate_batches.get((token, "ROUTER_RESULT", layer), []))
        submit_batch(candidate_batches.get((token, "TOKEN_END", -1), []))
    for key in sorted(entries):
        if entries[key].origin == "SPECULATIVE":
            discard(key, "request_end")
    resident = [{"layer": key[0], "expert": key[1], "cold_slot": entry.cold_slot,
        "hot_slot": entry.hot_slot, "origin": entry.origin} for key, entry in sorted(entries.items())]
    return {"action_stream": actions, "outcome_stream": outcomes, "state_digest": digest,
        "summary": counters, "resident": resident}


def replay(document: dict) -> dict:
    require_fields(document, {"schema_version", "profile_path", "policy", "transport", "readiness", "temporal_window_tokens",
        "candidates_per_target", "request_ordinal", "events", "completion_order", "limits", "seed_mode",
        "demand_mode"}, "replay")
    if document["schema_version"] != "phase10-prefetch-replay-v1":
        raise Phase10Error("unsupported replay schema")
    profile_path = document["profile_path"]
    if not isinstance(profile_path, str) or not profile_path:
        raise Phase10Error("profile_path must be a non-empty string")
    profile = load_json(profile_path)
    validate_profile(profile)
    policy = document["policy"]
    if not isinstance(policy, str) or policy not in {"OFF", "STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY", "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"}:
        raise Phase10Error("unknown policy")
    seed_mode = document["seed_mode"]
    if seed_mode not in {"OFF", "BLOCKING_HOT"}:
        raise Phase10Error("unknown seed mode")
    demand_mode = document["demand_mode"]
    if demand_mode not in {"ISSUE_AHEAD", "SERIAL"}:
        raise Phase10Error("unknown demand mode")
    if policy != "OFF" and seed_mode != "OFF":
        raise Phase10Error("replay does not combine prediction and seed")
    if not isinstance(document["readiness"], str) or document["readiness"] not in {"HOST_READY", "DEVICE_READY"}:
        raise Phase10Error("unknown readiness")
    if not isinstance(document["transport"], str) or document["transport"] not in {"BUFFERED", "DIRECT_IO", "HOST_TO_DEVICE"}:
        raise Phase10Error("unknown transport")
    candidate_count = require_uint(document["candidates_per_target"], "candidates_per_target", positive=policy != "OFF",
        maximum=profile["target"]["experts_per_layer"])
    if policy == "OFF" and candidate_count != 0:
        raise Phase10Error("disabled replay candidate count must be zero")
    request_ordinal = require_uint(document["request_ordinal"], "request_ordinal", positive=True)
    temporal_window = require_uint(document["temporal_window_tokens"], "temporal_window_tokens", maximum=64)
    if policy == "TEMPORAL_FREQUENCY":
        if temporal_window < 2 or temporal_window & (temporal_window - 1) != 0:
            raise Phase10Error("invalid temporal window")
    elif temporal_window != 0:
        raise Phase10Error("unexpected temporal window")
    if not isinstance(document["completion_order"], list) or any(
            require_uint(value, "completion ordinal") < 0 for value in document["completion_order"]):
        raise Phase10Error("invalid completion order")
    if len(document["completion_order"]) != len(set(document["completion_order"])):
        raise Phase10Error("duplicate completion ordinal")
    limits = validate_limits(document["limits"], policy != "OFF")
    config = {"struct_size": 128, "policy": policy, "readiness": document["readiness"],
        "temporal_window_tokens": temporal_window, "candidates_per_target": candidate_count}
    profile_sha256 = hashlib.sha256(Path(profile_path).read_bytes()).hexdigest()
    digest = config_digest(config, profile_sha256)
    history: list[list[list[int]]] = []
    rolling = fnv_append(FNV_OFFSET, request_ordinal)
    stream = []
    layers = profile["target"]["routed_layers"]
    if not isinstance(document["events"], list):
        raise Phase10Error("events must be an array")
    for token, event in enumerate(document["events"]):
        require_fields(event, {"token", "layers"}, "event")
        if require_uint(event["token"], "event token") != token or not isinstance(event["layers"], list):
            raise Phase10Error("noncanonical event")
        event_layers = []
        for record in event["layers"]:
            require_fields(record, {"layer", "experts"}, "layer event")
            event_layers.append(require_uint(record["layer"], "event layer"))
        if event_layers != layers:
            raise Phase10Error("noncanonical event")
        routed = []
        for index, record in enumerate(event["layers"]):
            if not isinstance(record["experts"], list):
                raise Phase10Error("selected experts must be an array")
            raw_experts = record["experts"]
            if any(isinstance(expert, bool) or not isinstance(expert, int) for expert in raw_experts):
                raise Phase10Error("selected experts must be integers")
            experts = sorted(raw_experts)
            if len(experts) == 0 or len(experts) > profile["target"]["experts_per_token"] or \
                    len(experts) != len(set(experts)) or any(isinstance(expert, bool) or not isinstance(expert, int) or
                    expert < 0 or expert >= profile["target"]["experts_per_layer"] for expert in experts):
                raise Phase10Error("invalid selected experts")
            routed.append(experts)
            if policy == "CROSS_LAYER_TRANSITION" and index + 1 < len(layers):
                for rank, (expert, score) in enumerate(cross_candidates(profile, layers[index], experts, layers[index + 1], candidate_count)):
                    stream.append({"trigger_token": token, "trigger": "ROUTER_RESULT", "source_layer": layers[index],
                        "target_layer": layers[index + 1], "expert": expert, "rank": rank, "score": score})
        rolling = fnv_append(rolling, token)
        for experts in routed:
            rolling = fnv_append(rolling, len(experts))
            for expert in experts:
                rolling = fnv_append(rolling, expert)
        window = temporal_window if policy == "TEMPORAL_FREQUENCY" else 1
        if len(history) == window:
            history.pop(0)
        history.append(routed)
        if policy not in {"OFF", "CROSS_LAYER_TRANSITION"}:
            for layer in layers:
                for rank, (expert, score) in enumerate(predictor_candidates(
                        profile, policy, history, layer, candidate_count, digest, request_ordinal, token)):
                    stream.append({"trigger_token": token, "trigger": "TOKEN_END", "source_layer": -1,
                        "target_layer": layer, "expert": expert, "rank": rank, "score": score})
    compact_events = json.dumps(document["events"], ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if document["completion_order"] and (len(document["completion_order"]) != len(stream) or
            sorted(document["completion_order"]) != list(range(len(stream)))):
        raise Phase10Error("completion order is not a candidate permutation")
    state = simulate(profile, document["events"], stream, document["transport"], document["readiness"],
        limits, seed_mode, demand_mode)
    if policy == "OFF" and seed_mode == "OFF":
        state["state_digest"] = FNV_OFFSET
    return {"schema_version": "phase10-prefetch-replay-output-v1",
        "profile_sha256": profile_sha256, "policy": policy, "transport": document["transport"], "seed_mode": seed_mode,
        "demand_mode": demand_mode,
        "candidate_stream": stream, "predictor_state_digest": FNV_OFFSET if policy == "OFF" else rolling,
        **state,
        "phase9_passthrough_sha256": hashlib.sha256(compact_events.encode("utf-8")).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = replay(load_json(args.input))
        if args.output:
            write_json(args.output, result)
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

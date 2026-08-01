#!/usr/bin/env python3
"""Independent Phase 9 cache-policy simulator and canonical replay engine.

This module is intentionally implemented from the issue semantics.  It imports
no C++ binding and no Phase 2 policy implementation; the Phase 2 adapter uses
only the stable binary trace parser.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


U64_MAX = (1 << 64) - 1
U32_MAX = (1 << 32) - 1
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
EVENT_NAMES = (
    "REQUEST_BEGIN", "PHASE_TRANSITION", "DEMAND", "HIT", "VICTIM_SELECTED",
    "EVICT", "LOAD_BEGIN", "LOAD_COMPLETE", "LOAD_FAILED", "PIN", "UNPIN",
    "OPTIONAL_ADMISSION", "REQUEST_END", "RESET", "SURRENDER",
)
POLICIES = ("LRU", "LFRU", "SLRU", "LFU_AGING")
SCOPES = ("GLOBAL", "PER_LAYER")
ADMISSIONS = ("ALWAYS", "FREQUENCY_WINDOW")
PHASES = ("PREFILL", "DECODE")
HOT_ADMISSIONS = ("MANDATORY_CURRENT_OUTPUT", "OPTIONAL_CPU_SERVED", "OPTIONAL_BACKGROUND")


class ReplayError(ValueError):
    """A replay input or state transition is noncanonical or inconsistent."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _hash_append(state: int, value: int) -> int:
    value &= U64_MAX
    for byte in range(8):
        state ^= (value >> (byte * 8)) & 0xFF
        state = (state * FNV_PRIME) & U64_MAX
    return state


def _hash_values(*values: int) -> int:
    state = FNV_OFFSET
    for value in values:
        state = _hash_append(state, value)
    return state


def _u32(value: int) -> int:
    return value & U32_MAX


def _power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _require_fields(value: dict[str, Any], fields: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise ReplayError(f"{name} fields do not match version 1")


@dataclass(frozen=True, order=True)
class Key:
    layer: int
    expert: int


@dataclass
class KeyState:
    window_frequency: int = 0
    last_touch_demand: int = 0
    last_demand_sequence: int = 0


@dataclass
class Slot:
    key: Key = Key(-1, -1)
    generation: int = 0
    last_touch_sequence: int = 0
    last_touch_demand: int = 0
    logical_bytes: int = 0
    physical_bytes: int = 0
    resident_frequency: int = 0
    aging_epoch: int = 0
    origin_operation_ordinal: int = 0
    domain: int = U32_MAX
    pin_count: int = 0
    segment: int = 0  # NONE=0, PROBATIONARY=1, PROTECTED=2
    loading: bool = False
    resident: bool = False
    terminal_pending: bool = False
    terminal_success: bool = False


@dataclass
class Domain:
    domain: int
    layer: int
    slot_count: int = 0
    quota_bytes: int = 0
    occupancy_bytes: int = 0
    protected_capacity_bytes: int = 0
    protected_occupancy_bytes: int = 0


def validate_config(value: dict[str, Any], tier: str) -> dict[str, Any]:
    _require_fields(value, {
        "schema_version", "policy", "scope", "slru_protected_ratio_bps",
        "admission", "admission_window_events", "lfu_aging_interval_events",
    }, "cache policy config")
    if value["schema_version"] != "cache-policy-config-v1" or tier not in ("HOT", "COLD"):
        raise ReplayError("unsupported cache policy config version or tier")
    policy = value["policy"]
    scope = value["scope"]
    admission = value["admission"]
    ratio = value["slru_protected_ratio_bps"]
    window = value["admission_window_events"]
    aging = value["lfu_aging_interval_events"]
    if policy not in POLICIES or scope not in SCOPES or admission not in ADMISSIONS:
        raise ReplayError("unknown cache policy enum")
    if policy == "SLRU":
        if not isinstance(ratio, int) or not 1000 <= ratio <= 9000:
            raise ReplayError("SLRU ratio is outside version 1")
    elif ratio != 0:
        raise ReplayError("non-SLRU ratio must be zero")
    if admission == "FREQUENCY_WINDOW":
        if tier != "HOT" or policy != "SLRU" or not isinstance(window, int) or not _power_of_two(window) or not 64 <= window <= 1048576:
            raise ReplayError("frequency-window admission is invalid")
    elif window != 0:
        raise ReplayError("ALWAYS admission window must be zero")
    if policy == "LFU_AGING":
        if not isinstance(aging, int) or not _power_of_two(aging) or not 64 <= aging <= 1048576:
            raise ReplayError("LFU aging interval is invalid")
    elif aging != 0:
        raise ReplayError("non-LFU aging interval must be zero")
    enum_policy = POLICIES.index(policy)
    enum_scope = SCOPES.index(scope)
    enum_admission = ADMISSIONS.index(admission)
    digest = _hash_values(1, 64, enum_policy, enum_scope, ratio, enum_admission, window, aging)
    return {**value, "digest": digest}


class Policy:
    """Issue-defined single-tier policy state, independent from cache mechanism."""

    def __init__(self, config: dict[str, Any], tier: str, topology: dict[str, Any], slots: int,
                 transcript_capacity: int, capture_events: bool = True):
        self.config = validate_config(config, tier)
        self.tier = tier
        self.layers = list(topology["routed_layers"])
        self.experts_per_layer = topology["experts_per_layer"]
        self.slot_footprint = topology["physical_slot_footprint_bytes"]
        if not self.layers or self.layers != sorted(set(self.layers)) or self.experts_per_layer <= 0 or slots <= 0:
            raise ReplayError("invalid replay topology")
        if self.config["scope"] == "PER_LAYER" and slots < len(self.layers):
            raise ReplayError("infeasible per-layer replay capacity")
        self.key_states = [KeyState() for _ in range(len(self.layers) * self.experts_per_layer)]
        domain_count = 1 if self.config["scope"] == "GLOBAL" else len(self.layers)
        self.domains = [Domain(index, -1 if domain_count == 1 else self.layers[index]) for index in range(domain_count)]
        self.slots = [Slot() for _ in range(slots)]
        base, remainder = divmod(slots, len(self.layers))
        boundary = 0
        for domain in range(domain_count):
            count = slots if domain_count == 1 else base + (1 if domain < remainder else 0)
            for slot in range(boundary, boundary + count):
                self.slots[slot].domain = domain
            boundary += count
            self.domains[domain].slot_count = count
            self.domains[domain].quota_bytes = count * self.slot_footprint
            scaled = self.domains[domain].quota_bytes * self.config["slru_protected_ratio_bps"]
            self.domains[domain].protected_capacity_bytes = (scaled // 10000 // self.slot_footprint) * self.slot_footprint
        self.window = [U64_MAX] * self.config["admission_window_events"]
        self.window_write = 0
        self.window_size = 0
        self.window_digest = 0
        for index, key in enumerate(self.window):
            self.window_digest ^= _hash_values(index, key)
        self.transcript_capacity = transcript_capacity
        self.capture_events = capture_events
        self.events: list[dict[str, Any]] = []
        self.event_types: Counter[str] = Counter()
        self.request_active = False
        self.phase = "PREFILL"
        self.request_ordinal = 0
        self.ubatch_ordinal = 0
        self.event_sequence = 0
        self.demand_ordinal = 0
        self.operation_ordinal = 0
        self.reserved_terminals = 0
        self.terminal_operation_ordinal = 0
        self.counters: Counter[str] = Counter()
        self.initial_digest = self.state_digest()

    def key_index(self, key: Key) -> int:
        try:
            layer_index = self.layers.index(key.layer)
        except ValueError as error:
            raise ReplayError(f"unregistered layer {key.layer}") from error
        if not 0 <= key.expert < self.experts_per_layer:
            raise ReplayError("expert is outside topology")
        return layer_index * self.experts_per_layer + key.expert

    def key_domain(self, key: Key) -> int:
        self.key_index(key)
        return 0 if self.config["scope"] == "GLOBAL" else self.layers.index(key.layer)

    def state_digest(self) -> int:
        state = FNV_OFFSET
        values = (
            self.config["digest"], self.request_ordinal, self.ubatch_ordinal,
            self.event_sequence, self.demand_ordinal, self.operation_ordinal,
            self.reserved_terminals, self.terminal_operation_ordinal,
            self.window_write, self.window_size, self.window_digest,
            PHASES.index(self.phase),
        )
        for value in values:
            state = _hash_append(state, value)
        for key in self.key_states:
            for value in (key.window_frequency, key.last_touch_demand, key.last_demand_sequence):
                state = _hash_append(state, value)
        for slot in self.slots:
            for value in (
                _u32(slot.key.layer), _u32(slot.key.expert),
                slot.generation if slot.resident or slot.loading else 0,
                slot.last_touch_sequence, slot.last_touch_demand, slot.logical_bytes,
                slot.physical_bytes, slot.resident_frequency, slot.aging_epoch,
                slot.origin_operation_ordinal, slot.domain, slot.pin_count,
                slot.segment, int(slot.loading), int(slot.resident),
            ):
                state = _hash_append(state, value)
        for domain in self.domains:
            for value in (
                domain.domain, _u32(domain.layer), domain.slot_count, domain.quota_bytes,
                domain.occupancy_bytes, domain.protected_capacity_bytes,
                domain.protected_occupancy_bytes,
            ):
                state = _hash_append(state, value)
        return state

    def _append(self, event_type: str, key: Key = Key(-1, -1), occurrence: int = 0,
                logical: int = 0, physical: int = 0, slot: int = -1,
                generation: int = 0, decision: int = 0, origin: int = 0,
                eligible: bool = False, reason: int = 0) -> None:
        if len(self.events) >= self.transcript_capacity:
            raise ReplayError("transcript_full")
        if self.event_sequence == U64_MAX:
            raise ReplayError("event sequence exhausted")
        self.event_sequence += 1
        self.event_types[event_type] += 1
        if not self.capture_events:
            return
        digest = self.state_digest()
        domain = -1
        if key.layer >= 0:
            domain = self.key_domain(key)
        self.events.append({
            "schema_version": 1,
            "request_ordinal": self.request_ordinal,
            "ubatch_ordinal": self.ubatch_ordinal,
            "tier": self.tier,
            "type": event_type,
            "event_sequence": self.event_sequence,
            "demand_ordinal": self.demand_ordinal,
            "origin_operation_ordinal": origin,
            "phase": self.phase,
            "layer": key.layer,
            "expert": key.expert,
            "occurrence_count": occurrence,
            "logical_payload_bytes": logical,
            "physical_slot_footprint_bytes": physical,
            "slot": slot,
            "generation": generation,
            "domain": domain,
            "eligible": eligible,
            "decision": decision,
            "reason": reason,
            "state_digest": digest,
        })

    def request_begin(self) -> None:
        if self.request_active or self.request_ordinal == U64_MAX:
            raise ReplayError("noncanonical request begin")
        self.request_ordinal += 1
        self.ubatch_ordinal = 0
        self.request_active = True
        self.phase = "PREFILL"
        self._append("REQUEST_BEGIN")

    def set_ubatch(self, ordinal: int) -> None:
        if not self.request_active or ordinal <= self.ubatch_ordinal:
            raise ReplayError("ubatch ordinal is not strictly increasing")
        self.ubatch_ordinal = ordinal

    def set_phase(self, phase: str) -> None:
        if phase not in PHASES:
            raise ReplayError("unknown phase")
        if phase != self.phase:
            self.phase = phase
            self._append("PHASE_TRANSITION")

    def demand(self, key: Key, occurrence: int, logical: int, physical: int) -> None:
        if not self.request_active or occurrence <= 0 or logical <= 0 or physical <= 0:
            raise ReplayError("invalid demand")
        index = self.key_index(key)
        if self.demand_ordinal == U64_MAX:
            raise ReplayError("demand ordinal exhausted")
        self.demand_ordinal += 1
        state = self.key_states[index]
        if self.window:
            if self.window_size == len(self.window):
                outgoing = self.window[self.window_write]
                if outgoing >= len(self.key_states) or self.key_states[outgoing].window_frequency == 0:
                    raise ReplayError("frequency window metadata mismatch")
                self.key_states[outgoing].window_frequency -= 1
            else:
                self.window_size += 1
            self.window_digest ^= _hash_values(self.window_write, self.window[self.window_write])
            self.window[self.window_write] = index
            self.window_digest ^= _hash_values(self.window_write, index)
            self.window_write = (self.window_write + 1) & (len(self.window) - 1)
            state.window_frequency = min(U64_MAX, state.window_frequency + 1)
        state.last_touch_demand = self.demand_ordinal
        state.last_demand_sequence = self.event_sequence + 1
        self.counters["demands"] += 1
        self._append("DEMAND", key, occurrence, logical, physical)

    def _touch(self, slot: Slot) -> None:
        key = self.key_states[self.key_index(slot.key)]
        slot.last_touch_sequence = key.last_demand_sequence
        slot.last_touch_demand = key.last_touch_demand

    def _enforce_protected(self, domain_index: int) -> None:
        domain = self.domains[domain_index]
        while domain.protected_occupancy_bytes > domain.protected_capacity_bytes:
            eligible = [
                (slot.last_touch_sequence, index)
                for index, slot in enumerate(self.slots)
                if slot.resident and slot.domain == domain_index and slot.segment == 2 and slot.pin_count == 0
            ]
            if not eligible:
                if any(slot.resident and slot.domain == domain_index and slot.segment == 2 and slot.pin_count for slot in self.slots):
                    self.counters["pinned_blocked_demotions"] += 1
                return
            _, index = min(eligible)
            slot = self.slots[index]
            slot.segment = 1
            domain.protected_occupancy_bytes -= slot.physical_bytes
            slot.last_touch_sequence = self.event_sequence + 1

    def hit(self, slot_index: int) -> None:
        slot = self.slots[slot_index]
        if not slot.resident:
            raise ReplayError("hit references nonresident slot")
        if self.config["policy"] == "LFU_AGING":
            epoch = self.key_states[self.key_index(slot.key)].last_touch_demand // self.config["lfu_aging_interval_events"]
            if epoch < slot.aging_epoch:
                raise ReplayError("LFU epoch regressed")
            elapsed = min(63, epoch - slot.aging_epoch)
            if elapsed and slot.resident_frequency:
                slot.resident_frequency = max(1, slot.resident_frequency >> elapsed)
            slot.aging_epoch = epoch
        slot.resident_frequency = min(U64_MAX, slot.resident_frequency + 1)
        if self.config["policy"] == "SLRU":
            if self.phase == "DECODE" and slot.segment == 1:
                slot.segment = 2
                self.domains[slot.domain].protected_occupancy_bytes += slot.physical_bytes
                self._touch(slot)
                self._enforce_protected(slot.domain)
            elif self.phase == "DECODE" or slot.segment == 1:
                self._touch(slot)
        else:
            self._touch(slot)
        self.counters["hits"] += 1
        self._append("HIT", slot.key, logical=slot.logical_bytes, physical=slot.physical_bytes,
                     slot=slot_index, generation=slot.generation)

    def _effective_frequency(self, slot: Slot) -> int:
        epoch = self.demand_ordinal // self.config["lfu_aging_interval_events"]
        elapsed = min(63, epoch - slot.aging_epoch)
        return slot.resident_frequency if not elapsed or not slot.resident_frequency else max(1, slot.resident_frequency >> elapsed)

    def victim_rank(self, index: int) -> tuple[Any, ...]:
        slot = self.slots[index]
        policy = self.config["policy"]
        if policy == "LRU":
            return (slot.last_touch_sequence, index)
        if policy == "LFRU":
            age = self.demand_ordinal - slot.last_touch_demand + 1
            return (slot.resident_frequency / age, slot.last_touch_sequence, index)
        if policy == "SLRU":
            return (0 if slot.segment == 1 else 1, slot.last_touch_sequence, index)
        return (self._effective_frequency(slot), slot.last_touch_sequence, index)

    def select(self, key: Key, logical: int, eligible: set[int] | None = None,
               excluded: set[int] | None = None) -> tuple[int, bool]:
        domain = self.key_domain(key)
        free = [index for index, slot in enumerate(self.slots)
                if slot.domain == domain and not slot.resident and not slot.loading and
                (excluded is None or index not in excluded)]
        if free:
            selected = min(free)
            self.counters["free_selections"] += 1
            self._append("VICTIM_SELECTED", key, logical=logical, physical=self.slot_footprint,
                         slot=selected, generation=self.slots[selected].generation,
                         decision=1, eligible=True, reason=1)
            return selected, True
        choices = [
            index for index, slot in enumerate(self.slots)
            if slot.domain == domain and slot.resident and slot.pin_count == 0 and
               (eligible is None or index in eligible) and (excluded is None or index not in excluded)
        ]
        if not choices:
            raise ReplayError("no eligible victim")
        if self.config["policy"] == "LFRU":
            def precedes(left: int, right: int) -> bool:
                lhs, rhs = self.slots[left], self.slots[right]
                lhs_age = self.demand_ordinal - lhs.last_touch_demand + 1
                rhs_age = self.demand_ordinal - rhs.last_touch_demand + 1
                left_product = lhs.resident_frequency * rhs_age
                right_product = rhs.resident_frequency * lhs_age
                return (left_product, lhs.last_touch_sequence, left) < (right_product, rhs.last_touch_sequence, right)
            selected = choices[0]
            for choice in choices[1:]:
                if precedes(choice, selected):
                    selected = choice
        else:
            selected = min(choices, key=self.victim_rank)
        if self.config["policy"] == "SLRU" and self.slots[selected].segment == 2:
            self.counters["protected_forced_victims"] += 1
        self.counters["victim_selections"] += 1
        self._append("VICTIM_SELECTED", key, logical=self.slots[selected].logical_bytes, physical=self.slot_footprint,
                     slot=selected, generation=self.slots[selected].generation,
                     decision=2, eligible=True, reason=2)
        return selected, False

    def optional_select(self, key: Key, logical: int, admission: str,
                        excluded: set[int] | None = None) -> tuple[int | None, bool]:
        if admission not in HOT_ADMISSIONS:
            raise ReplayError("invalid hot admission class")
        if admission == "MANDATORY_CURRENT_OUTPUT" or self.config["admission"] != "FREQUENCY_WINDOW":
            slot, free = self.select(key, logical, excluded=excluded)
            self.counters["mandatory_admissions" if admission == "MANDATORY_CURRENT_OUTPUT" else "optional_admission_accepts"] += 1
            selected_logical = logical if free else self.slots[slot].logical_bytes
            self._append("OPTIONAL_ADMISSION", key, logical=selected_logical, physical=self.slot_footprint,
                         slot=slot, generation=self.slots[slot].generation,
                         decision=2 if admission == "MANDATORY_CURRENT_OUTPUT" else 1,
                         eligible=True, reason=5 if admission == "MANDATORY_CURRENT_OUTPUT" else (1 if free else 2))
            return slot, free
        domain = self.key_domain(key)
        free = [index for index, slot in enumerate(self.slots)
                if slot.domain == domain and not slot.resident and not slot.loading and
                (excluded is None or index not in excluded)]
        if free:
            slot = min(free)
            self.counters["free_selections"] += 1
            self._append("VICTIM_SELECTED", key, logical=logical, physical=self.slot_footprint,
                         slot=slot, generation=self.slots[slot].generation, decision=1, eligible=True, reason=1)
            self.counters["optional_admission_accepts"] += 1
            self._append("OPTIONAL_ADMISSION", key, logical=logical, physical=self.slot_footprint,
                         slot=slot, generation=self.slots[slot].generation, decision=1, eligible=True, reason=1)
            return slot, True
        probationary = [index for index, slot in enumerate(self.slots)
                        if slot.domain == domain and slot.resident and slot.segment == 1 and
                        slot.pin_count == 0 and (excluded is None or index not in excluded)]
        if probationary:
            incumbent = min(probationary, key=lambda index: (
                self.key_states[self.key_index(self.slots[index].key)].window_frequency,
                self.slots[index].last_touch_sequence, index,
            ))
            candidate_frequency = self.key_states[self.key_index(key)].window_frequency
            incumbent_frequency = self.key_states[self.key_index(self.slots[incumbent].key)].window_frequency
            if candidate_frequency > incumbent_frequency:
                self.counters["victim_selections"] += 1
                selected_logical = self.slots[incumbent].logical_bytes
                self._append("VICTIM_SELECTED", key, logical=selected_logical, physical=self.slot_footprint,
                             slot=incumbent, generation=self.slots[incumbent].generation,
                             decision=2, eligible=True, reason=2)
                self.counters["optional_admission_accepts"] += 1
                self._append("OPTIONAL_ADMISSION", key, logical=selected_logical, physical=self.slot_footprint,
                             slot=incumbent, generation=self.slots[incumbent].generation,
                             decision=1, eligible=True, reason=2)
                return incumbent, False
        self.counters["optional_admission_rejects"] += 1
        self._append("OPTIONAL_ADMISSION", key, decision=0, eligible=False, reason=4 if not probationary else 3)
        return None, False

    def evict(self, index: int) -> Key:
        slot = self.slots[index]
        if not slot.resident or slot.pin_count:
            raise ReplayError("eviction references ineligible slot")
        old = Slot(**slot.__dict__)
        if old.segment == 2:
            self.domains[old.domain].protected_occupancy_bytes -= old.physical_bytes
        self.domains[old.domain].occupancy_bytes -= old.physical_bytes
        self.slots[index] = Slot(domain=old.domain, generation=old.generation)
        self._append("EVICT", old.key, logical=old.logical_bytes, physical=old.physical_bytes,
                     slot=index, generation=old.generation)
        return old.key

    def load_begin(self, index: int, generation: int, key: Key, logical: int,
                   demand_caused: bool = True) -> None:
        slot = self.slots[index]
        if slot.resident or slot.loading:
            raise ReplayError("load targets occupied slot")
        if generation <= 0 or generation > U64_MAX:
            raise ReplayError("generation exhausted")
        self.operation_ordinal += 1
        key_state = self.key_states[self.key_index(key)]
        self.slots[index] = Slot(
            key=key, generation=generation,
            last_touch_sequence=key_state.last_demand_sequence,
            last_touch_demand=key_state.last_touch_demand,
            logical_bytes=logical, physical_bytes=self.slot_footprint,
            resident_frequency=1 if demand_caused else 0,
            aging_epoch=(key_state.last_touch_demand // self.config["lfu_aging_interval_events"] if self.config["policy"] == "LFU_AGING" else 0),
            origin_operation_ordinal=self.operation_ordinal,
            domain=slot.domain, segment=0, loading=True,
        )
        self.domains[slot.domain].occupancy_bytes += self.slot_footprint
        self.reserved_terminals += 1
        self._append("LOAD_BEGIN", key, logical=logical, physical=self.slot_footprint,
                     slot=index, generation=generation, origin=self.operation_ordinal)

    def _flush_terminals(self) -> None:
        while self.terminal_operation_ordinal < self.operation_ordinal:
            expected = self.terminal_operation_ordinal + 1
            selected = next((index for index, slot in enumerate(self.slots)
                             if slot.loading and slot.origin_operation_ordinal == expected), None)
            if selected is None:
                raise ReplayError("terminal operation metadata mismatch")
            pending = self.slots[selected]
            if not pending.terminal_pending:
                return
            saved = Slot(**pending.__dict__)
            self.terminal_operation_ordinal = expected
            self.reserved_terminals -= 1
            if saved.terminal_success:
                pending.terminal_pending = False
                pending.terminal_success = False
                pending.loading = False
                pending.resident = True
                pending.segment = 1
                self._append("LOAD_COMPLETE", saved.key, logical=saved.logical_bytes,
                             physical=saved.physical_bytes, slot=selected,
                             generation=saved.generation, origin=saved.origin_operation_ordinal)
            else:
                self.slots[selected] = Slot(domain=saved.domain, generation=saved.generation)
                self.domains[saved.domain].occupancy_bytes -= saved.physical_bytes
                self._append("LOAD_FAILED", saved.key, logical=saved.logical_bytes,
                             physical=saved.physical_bytes, slot=selected,
                             generation=saved.generation, origin=saved.origin_operation_ordinal)

    def load_complete(self, index: int, generation: int) -> None:
        slot = self.slots[index]
        if not slot.loading or slot.terminal_pending or slot.generation != generation:
            raise ReplayError("load complete metadata mismatch")
        slot.terminal_pending = True
        slot.terminal_success = True
        self._flush_terminals()

    def load_failed(self, index: int, generation: int) -> None:
        slot = self.slots[index]
        if not slot.loading or slot.terminal_pending or slot.generation != generation:
            raise ReplayError("load failure metadata mismatch")
        slot.terminal_pending = True
        slot.terminal_success = False
        self._flush_terminals()

    def pin(self, index: int, generation: int) -> None:
        slot = self.slots[index]
        if not slot.resident or slot.generation != generation or slot.pin_count >= U32_MAX:
            raise ReplayError("pin metadata mismatch")
        slot.pin_count += 1
        self._append("PIN", slot.key, logical=slot.logical_bytes, physical=slot.physical_bytes,
                     slot=index, generation=generation)

    def unpin(self, index: int, generation: int) -> None:
        slot = self.slots[index]
        if not slot.resident or slot.generation != generation or slot.pin_count == 0:
            raise ReplayError("unpin metadata mismatch")
        slot.pin_count -= 1
        if self.config["policy"] == "SLRU":
            self._enforce_protected(slot.domain)
        self._append("UNPIN", slot.key, logical=slot.logical_bytes, physical=slot.physical_bytes,
                     slot=index, generation=generation)

    def request_end(self, outcome: str) -> None:
        if not self.request_active or outcome not in ("SUCCESS", "FAILURE", "CANCELLED"):
            raise ReplayError("invalid request end")
        self._append("REQUEST_END", decision=2 if outcome == "CANCELLED" else 1 if outcome == "SUCCESS" else 0)
        self.request_active = False


class TierMechanism:
    def __init__(self, policy: Policy):
        self.policy = policy
        self.mapping: dict[Key, int] = {}
        self.metrics: Counter[str] = Counter()

    def contains(self, key: Key) -> bool:
        return key in self.mapping

    def hit(self, key: Key) -> None:
        self.policy.hit(self.mapping[key])
        self.metrics["hits"] += 1

    def remove(self, key: Key) -> bool:
        index = self.mapping.pop(key, None)
        if index is None:
            return False
        self.policy.evict(index)
        self.metrics["evictions"] += 1
        return True

    def admit(self, key: Key, logical: int, admission: str = "MANDATORY_CURRENT_OUTPUT") -> tuple[bool, Key | None]:
        if admission == "MANDATORY_CURRENT_OUTPUT":
            index, free = self.policy.select(key, logical)
        else:
            chosen = self.policy.optional_select(key, logical, admission)
            if chosen[0] is None:
                self.metrics["optional_rejections"] += 1
                return False, None
            index, free = chosen
        victim = None
        if not free:
            victim = self.policy.evict(index)
            del self.mapping[victim]
            self.metrics["evictions"] += 1
        generation = self.policy.slots[index].generation + 1
        self.policy.load_begin(index, generation, key, logical, demand_caused=admission != "OPTIONAL_BACKGROUND")
        self.policy.load_complete(index, generation)
        self.mapping[key] = index
        self.metrics["admissions"] += 1
        return True, victim


def validate_replay_input(value: dict[str, Any]) -> dict[str, Any]:
    _require_fields(value, {"schema_version", "topology", "hot", "cold", "requests"}, "replay input")
    if value["schema_version"] != "cache-policy-replay-input-v1":
        raise ReplayError("unsupported replay input")
    topology = value["topology"]
    _require_fields(topology, {"routed_layers", "experts_per_layer", "physical_slot_footprint_bytes"}, "topology")
    layers = topology["routed_layers"]
    if not isinstance(layers, list) or not layers or layers != sorted(set(layers)):
        raise ReplayError("routed layers are not canonical")
    for tier in ("hot", "cold"):
        _require_fields(value[tier], {"slots", "config"}, tier)
        if not isinstance(value[tier]["slots"], int) or value[tier]["slots"] <= 0:
            raise ReplayError("tier capacity is invalid")
        validate_config(value[tier]["config"], tier.upper())
    requests = value["requests"]
    if not isinstance(requests, list) or not requests:
        raise ReplayError("replay has no requests")
    previous_request = 0
    for request in requests:
        _require_fields(request, {"request_ordinal", "checkpoints", "outcome"}, "request")
        if request["request_ordinal"] != previous_request + 1:
            raise ReplayError("request ordinals are not contiguous")
        previous_request += 1
        previous_ubatch = 0
        previous_checkpoint = 0
        for checkpoint in request["checkpoints"]:
            _require_fields(checkpoint, {"checkpoint_ordinal", "ubatch_ordinal", "phase", "demands"}, "checkpoint")
            if checkpoint["checkpoint_ordinal"] != previous_checkpoint + 1:
                raise ReplayError("checkpoint ordinals are not contiguous")
            previous_checkpoint += 1
            if checkpoint["ubatch_ordinal"] < previous_ubatch or checkpoint["phase"] not in PHASES:
                raise ReplayError("checkpoint order is noncanonical")
            previous_ubatch = checkpoint["ubatch_ordinal"]
            keys = []
            for demand in checkpoint["demands"]:
                _require_fields(demand, {"layer", "expert", "occurrence_count", "logical_payload_bytes", "hot_admission"}, "demand")
                key = Key(demand["layer"], demand["expert"])
                keys.append(key)
                if demand["occurrence_count"] <= 0 or demand["logical_payload_bytes"] <= 0 or demand["hot_admission"] not in HOT_ADMISSIONS:
                    raise ReplayError("invalid demand fields")
            if keys != sorted(keys) or len(keys) != len(set(keys)):
                raise ReplayError("checkpoint demands are duplicate or noncanonical")
    return value


def replay(value: dict[str, Any], transcript_capacity: int = 1_000_000,
           capture_events: bool = True, include_analysis: bool = False) -> dict[str, Any]:
    value = validate_replay_input(value)
    topology = value["topology"]
    hot = TierMechanism(Policy(value["hot"]["config"], "HOT", topology, value["hot"]["slots"], transcript_capacity, capture_events))
    cold = TierMechanism(Policy(value["cold"]["config"], "COLD", topology, value["cold"]["slots"], transcript_capacity, capture_events))
    summary: Counter[str] = Counter()
    by_phase: dict[str, Counter[str]] = {phase: Counter() for phase in PHASES}
    by_layer: dict[int, Counter[str]] = defaultdict(Counter)
    demand_history: list[Key] = []
    for request in value["requests"]:
        hot.policy.request_begin()
        cold.policy.request_begin()
        current_ubatch = 0
        for checkpoint in request["checkpoints"]:
            if checkpoint["ubatch_ordinal"] != current_ubatch:
                hot.policy.set_ubatch(checkpoint["ubatch_ordinal"])
                cold.policy.set_ubatch(checkpoint["ubatch_ordinal"])
                current_ubatch = checkpoint["ubatch_ordinal"]
            hot.policy.set_phase(checkpoint["phase"])
            cold.policy.set_phase(checkpoint["phase"])
            for item in checkpoint["demands"]:
                key = Key(item["layer"], item["expert"])
                logical = item["logical_payload_bytes"]
                demand_history.append(key)
                hot.policy.demand(key, item["occurrence_count"], logical, topology["physical_slot_footprint_bytes"])
                summary["logical_requests"] += 1
                by_phase[checkpoint["phase"]]["logical_requests"] += 1
                if hot.contains(key):
                    hot.hit(key)
                    # Phase 2's inclusive LRU contract refreshes the backing
                    # cold resident without issuing a lower-tier data request.
                    cold.policy.demand(key, item["occurrence_count"], logical,
                                       topology["physical_slot_footprint_bytes"])
                    cold.hit(key)
                    source = "hot"
                else:
                    cold.policy.demand(key, item["occurrence_count"], logical, topology["physical_slot_footprint_bytes"])
                    if cold.contains(key):
                        cold.hit(key)
                        source = "cold"
                    else:
                        admitted, cold_victim = cold.admit(key, logical)
                        if not admitted:
                            raise ReplayError("mandatory cold admission rejected")
                        if cold_victim is not None:
                            hot.remove(cold_victim)
                        source = "backing_store"
                    admitted, _ = hot.admit(key, logical, item["hot_admission"])
                    if not admitted and item["hot_admission"] == "MANDATORY_CURRENT_OUTPUT":
                        raise ReplayError("mandatory hot admission rejected")
                summary[f"{source}_hits"] += 1
                summary[f"{source}_bytes"] += logical
                by_phase[checkpoint["phase"]][f"{source}_hits"] += 1
                by_phase[checkpoint["phase"]][f"{source}_bytes"] += logical
                by_layer[key.layer]["logical_requests"] += 1
                by_layer[key.layer][f"{source}_hits"] += 1
                by_layer[key.layer][f"{source}_bytes"] += logical
        hot.policy.request_end(request["outcome"])
        cold.policy.request_end(request["outcome"])

    def tier_output(mechanism: TierMechanism) -> dict[str, Any]:
        policy = mechanism.policy
        event_counts = policy.event_types
        return {
            "config_digest": policy.config["digest"],
            "initial_digest": policy.initial_digest,
            "final_digest": policy.state_digest(),
            "events": policy.events,
            "counters": {
                "events": policy.event_sequence,
                "demands": event_counts["DEMAND"],
                "hits": event_counts["HIT"],
                "victims": event_counts["VICTIM_SELECTED"],
                "admissions": mechanism.metrics["admissions"],
                "evictions": mechanism.metrics["evictions"],
                "optional_accepts": policy.counters["optional_admission_accepts"],
                "optional_rejections": mechanism.metrics["optional_rejections"],
            },
            "domains": [domain.__dict__ for domain in policy.domains],
            "resident": [
                {"slot": index, "layer": slot.key.layer, "expert": slot.key.expert,
                 "generation": slot.generation}
                for index, slot in enumerate(policy.slots) if slot.resident
            ],
        }

    result = {
        "schema_version": "cache-policy-replay-v1",
        "status": "pass",
        "input_sha256": canonical_sha256(value),
        "tiers": {"hot": tier_output(hot), "cold": tier_output(cold)},
        "summary": {
            "logical_requests": summary["logical_requests"],
            "hot_hits": summary["hot_hits"],
            "cold_hits": summary["cold_hits"],
            "backing_store_hits": summary["backing_store_hits"],
            "hot_bytes": summary["hot_bytes"],
            "cold_bytes": summary["cold_bytes"],
            "backing_store_bytes": summary["backing_store_bytes"],
        },
    }
    if include_analysis:
        distances = reuse_distances(demand_history)
        def analysis_tier(mechanism: TierMechanism) -> dict[str, Any]:
            segment_bytes: dict[str, dict[str, int]] = {}
            for domain in mechanism.policy.domains:
                slots = [slot for slot in mechanism.policy.slots if slot.resident and slot.domain == domain.domain]
                segment_bytes[str(domain.domain)] = {
                    "probationary_bytes": sum(slot.physical_bytes for slot in slots if slot.segment == 1),
                    "protected_bytes": sum(slot.physical_bytes for slot in slots if slot.segment == 2),
                }
            return {
                "segment_occupancy": segment_bytes,
                "policy_work": dict(sorted(mechanism.policy.counters.items())),
            }
        result["analysis"] = {
            "by_phase": {
                phase: {name: by_phase[phase][name] for name in (
                    "logical_requests", "hot_hits", "cold_hits", "backing_store_hits",
                    "hot_bytes", "cold_bytes", "backing_store_bytes",
                )}
                for phase in PHASES
            },
            "by_layer": {
                str(layer): {name: by_layer[layer][name] for name in (
                    "logical_requests", "hot_hits", "cold_hits", "backing_store_hits",
                    "hot_bytes", "cold_bytes", "backing_store_bytes",
                )}
                for layer in sorted(by_layer)
            },
            "reuse_distance": percentile_summary(distance for distance in distances if distance is not None),
            "cold_references": sum(distance is None for distance in distances),
            "per_layer_skew": per_layer_skew(demand_history),
            "tiers": {"hot": analysis_tier(hot), "cold": analysis_tier(cold)},
        }
    return result


def reuse_distances(keys: Iterable[Key]) -> list[int | None]:
    result: list[int | None] = []
    last: dict[Key, int] = {}
    sequence = list(keys)
    for index, key in enumerate(sequence):
        if key not in last:
            result.append(None)
        else:
            result.append(len(set(sequence[last[key] + 1:index])))
        last[key] = index
    return result


def percentile_summary(values: Iterable[int | float]) -> dict[str, int | float | None]:
    ordered = sorted(values)
    def rank(p: float) -> int | float | None:
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)] if ordered else None
    return {"count": len(ordered), "p50": rank(0.50), "p95": rank(0.95), "p99": rank(0.99)}


def per_layer_skew(keys: Iterable[Key]) -> dict[str, Any]:
    counts: dict[int, Counter[int]] = defaultdict(Counter)
    for key in keys:
        counts[key.layer][key.expert] += 1
    return {
        str(layer): {
            "requests": sum(experts.values()),
            "experts": {str(expert): experts[expert] for expert in sorted(experts)},
        }
        for layer, experts in sorted(counts.items())
    }


def verify_event_stream(capture: dict[str, Any], tier: str) -> dict[str, Any]:
    """Independently replay one captured runtime policy transcript exactly."""
    if capture.get("schema_version") != "phase9-online-policy-capture-v1" or tier not in ("hot", "cold"):
        raise ReplayError("unsupported online capture")
    tier_value = capture[tier]
    config = dict(tier_value["config"])
    supplied_digest = config.pop("digest", None)
    topology_value = capture["topology"]
    footprint = topology_value[f"{tier}_physical_slot_footprint_bytes"]
    slots = capture["capacities"][f"{tier}_effective_slots"]
    expected = tier_value["events"]
    if tier == "cold" and capture["mode"] != "cold":
        if expected:
            raise ReplayError("inactive cold tier emitted events")
        return {"status": "pass", "tier": tier, "events": 0, "inactive": True}
    if not isinstance(footprint, int) or footprint <= 0 or not isinstance(slots, int) or slots <= 0:
        raise ReplayError("capture omits active tier topology")
    topology = {
        "routed_layers": topology_value["routed_layers"],
        "experts_per_layer": topology_value["experts_per_layer"],
        "physical_slot_footprint_bytes": footprint,
    }
    policy = Policy(config, tier.upper(), topology, slots, max(1, len(expected) + 16), True)
    if policy.config["digest"] != supplied_digest:
        raise ReplayError("captured config digest mismatch")

    index = 0
    pending_load_demand_caused: dict[int, bool] = {}
    reserved_slots: set[int] = set()
    live_loads: set[tuple[int, int]] = set()

    def accepted_slot_will_load(start: int, slot: int) -> bool:
        for future in expected[start:]:
            if future["slot"] != slot:
                continue
            if future["type"] == "LOAD_BEGIN":
                return True
            if future["type"] == "VICTIM_SELECTED":
                return False
        return False

    while index < len(expected):
        event = expected[index]
        _require_fields(event, {
            "schema_version", "request_ordinal", "ubatch_ordinal", "tier", "type",
            "event_sequence", "demand_ordinal", "origin_operation_ordinal", "phase",
            "layer", "expert", "occurrence_count", "logical_payload_bytes",
            "physical_slot_footprint_bytes", "slot", "generation", "domain",
            "eligible", "decision", "reason", "state_digest",
        }, "captured event")
        if event["tier"] != tier.upper() or event["schema_version"] != 1:
            raise ReplayError("captured event tier/version mismatch")
        before = len(policy.events)
        event_type = event["type"]
        key = Key(event["layer"], event["expert"])
        if event_type != "REQUEST_BEGIN" and event["ubatch_ordinal"] > policy.ubatch_ordinal:
            policy.set_ubatch(event["ubatch_ordinal"])
            reserved_slots.clear()
        if event_type == "REQUEST_BEGIN":
            policy.request_begin()
        elif event_type == "PHASE_TRANSITION":
            policy.set_phase(event["phase"])
        elif event_type == "DEMAND":
            policy.demand(key, event["occurrence_count"], event["logical_payload_bytes"],
                          event["physical_slot_footprint_bytes"])
        elif event_type == "HIT":
            policy.hit(event["slot"])
        elif event_type == "VICTIM_SELECTED":
            next_is_optional = index + 1 < len(expected) and expected[index + 1]["type"] == "OPTIONAL_ADMISSION"
            if next_is_optional:
                admission = ("MANDATORY_CURRENT_OUTPUT"
                             if expected[index + 1]["decision"] == 2 else "OPTIONAL_BACKGROUND")
                selected, _ = policy.optional_select(
                    key, event["logical_payload_bytes"], admission, excluded=reserved_slots)
                if selected is not None:
                    pending_load_demand_caused[selected] = admission == "MANDATORY_CURRENT_OUTPUT"
                    if accepted_slot_will_load(index + 2, selected):
                        reserved_slots.add(selected)
            else:
                selected, _ = policy.select(key, event["logical_payload_bytes"], excluded=reserved_slots)
                pending_load_demand_caused[selected] = True
                if accepted_slot_will_load(index + 1, selected):
                    reserved_slots.add(selected)
        elif event_type == "OPTIONAL_ADMISSION":
            admission = "MANDATORY_CURRENT_OUTPUT" if event["decision"] == 2 else "OPTIONAL_BACKGROUND"
            selected, _ = policy.optional_select(
                key, event["logical_payload_bytes"], admission, excluded=reserved_slots)
            if selected is not None:
                pending_load_demand_caused[selected] = admission == "MANDATORY_CURRENT_OUTPUT"
                if accepted_slot_will_load(index + 1, selected):
                    reserved_slots.add(selected)
        elif event_type == "EVICT":
            policy.evict(event["slot"])
        elif event_type == "LOAD_BEGIN":
            demand_caused = pending_load_demand_caused.pop(event["slot"], tier != "hot")
            reserved_slots.discard(event["slot"])
            policy.load_begin(event["slot"], event["generation"], key,
                              event["logical_payload_bytes"], demand_caused)
        elif event_type == "LOAD_COMPLETE":
            policy.load_complete(event["slot"], event["generation"])
        elif event_type == "LOAD_FAILED":
            policy.load_failed(event["slot"], event["generation"])
        elif event_type == "PIN":
            policy.pin(event["slot"], event["generation"])
        elif event_type == "UNPIN":
            policy.unpin(event["slot"], event["generation"])
        elif event_type == "REQUEST_END":
            policy.request_end("CANCELLED" if event["decision"] == 2 else
                               "SUCCESS" if event["decision"] == 1 else "FAILURE")
        else:
            raise ReplayError(f"event replay does not support {event_type}")
        produced = policy.events[before:]
        count = len(produced)
        if produced != expected[index:index + count]:
            mismatch = next((offset for offset, pair in enumerate(zip(produced, expected[index:index + count]))
                             if pair[0] != pair[1]), 0)
            raise ReplayError(f"{tier} event mismatch at {index + mismatch}: produced={produced[mismatch] if produced else None} expected={expected[index + mismatch]}")
        for emitted in produced:
            identity = (emitted["slot"], emitted["generation"])
            if emitted["type"] == "LOAD_BEGIN":
                if identity in live_loads:
                    raise ReplayError(f"{tier} duplicate live load")
                live_loads.add(identity)
            elif emitted["type"] in ("LOAD_COMPLETE", "LOAD_FAILED"):
                if identity not in live_loads:
                    raise ReplayError(f"{tier} terminal without live load")
                live_loads.remove(identity)
        index += count
    if not expected or expected[-1]["type"] != "REQUEST_END" or live_loads:
        raise ReplayError(f"{tier} capture is a partial request transcript")
    diagnostics = tier_value["diagnostics"]
    if policy.state_digest() != diagnostics["state_digest"] or policy.event_sequence != diagnostics["events"]:
        raise ReplayError("captured final diagnostics mismatch")
    return {
        "status": "pass", "tier": tier, "events": len(expected),
        "config_digest": policy.config["digest"], "final_digest": policy.state_digest(),
        "event_json_sha256": canonical_sha256(expected),
    }


def verify_online_capture(capture: dict[str, Any]) -> dict[str, Any]:
    hot = verify_event_stream(capture, "hot")
    cold = verify_event_stream(capture, "cold")
    return {
        "schema_version": "phase9-online-policy-verification-v1",
        "status": "pass", "hot": hot, "cold": cold,
        "output_identity": {
            "prompt_ids": capture["prompt_ids"], "generated_ids": capture["generated_ids"],
            "logits_fnv64": capture["logits_fnv64"],
        },
    }


def waste_victims(sequence: list[Key], slots: int, policy: str) -> dict[str, Any]:
    """Independent project-side reproduction of WASTE's pinned sample policy.

    Semantics are attributed to sqliteai/waste@c4d45c5914d1d15643d201855128938e8fb1698a
    (Apache-2.0); no WASTE source is imported.
    """
    if policy not in ("waste_sampled_lru", "waste_sampled_lfru") or slots <= 0:
        raise ReplayError("invalid WASTE baseline configuration")
    resident: list[Key | None] = [None] * slots
    recency = [0] * slots
    frequency = [0] * slots
    rng = 0x9E3779B9
    hits = misses = 0
    victims: list[int] = []
    for ordinal, key in enumerate(sequence, 1):
        if key in resident:
            index = resident.index(key)
            hits += 1
            frequency[index] = min(U64_MAX, frequency[index] + 1)
            recency[index] = ordinal
            continue
        misses += 1
        if None in resident:
            selected = resident.index(None)
        else:
            sampled = []
            for _ in range(16):
                rng = (rng * 1664525 + 1013904223) & U32_MAX
                sampled.append(rng % slots)
            if policy == "waste_sampled_lru":
                selected = min(sampled, key=lambda index: (recency[index], index))
            else:
                selected = min(sampled, key=lambda index: (frequency[index], recency[index], index))
            victims.append(selected)
        resident[selected] = key
        recency[selected] = ordinal
        frequency[selected] = 1
    return {"policy": policy, "hits": hits, "misses": misses, "evictions": len(victims), "victims": victims}


class _WasteTier:
    """Independent deterministic sampled cache used only by offline baselines."""

    def __init__(self, slots: int, policy: str):
        if slots <= 0 or policy not in ("waste_sampled_lru", "waste_sampled_lfru"):
            raise ReplayError("invalid WASTE tier")
        self.items: list[Key | None] = [None] * slots
        self.recency = [0] * slots
        self.frequency = [0] * slots
        self.policy = policy
        self.rng = 0x9E3779B9
        self.ordinal = 0
        self.evictions = 0

    def contains(self, key: Key) -> bool:
        return key in self.items

    def touch(self, key: Key) -> None:
        index = self.items.index(key)
        self.ordinal += 1
        self.recency[index] = self.ordinal
        self.frequency[index] = min(U64_MAX, self.frequency[index] + 1)

    def remove(self, key: Key) -> bool:
        if key not in self.items:
            return False
        index = self.items.index(key)
        self.items[index] = None
        self.recency[index] = 0
        self.frequency[index] = 0
        self.evictions += 1
        return True

    def admit(self, key: Key) -> Key | None:
        self.ordinal += 1
        victim = None
        if None in self.items:
            selected = self.items.index(None)
        else:
            sampled = []
            for _ in range(16):
                self.rng = (self.rng * 1664525 + 1013904223) & U32_MAX
                sampled.append(self.rng % len(self.items))
            if self.policy == "waste_sampled_lru":
                selected = min(sampled, key=lambda index: (self.recency[index], index))
            else:
                selected = min(sampled, key=lambda index: (
                    self.frequency[index], self.recency[index], index,
                ))
            victim = self.items[selected]
            self.evictions += 1
        self.items[selected] = key
        self.recency[selected] = self.ordinal
        self.frequency[selected] = 1
        return victim


def waste_hierarchy(sequence: list[tuple[Key, int, str]], hot_slots: int,
                    cold_slots: int, policy: str) -> dict[str, Any]:
    """Replay pinned WASTE sampling semantics over an inclusive hierarchy."""
    if not 0 < hot_slots <= cold_slots:
        raise ReplayError("invalid WASTE hierarchy capacity")
    hot = _WasteTier(hot_slots, policy)
    cold = _WasteTier(cold_slots, policy)
    counts = Counter()
    bytes_by_source = Counter()
    for key, logical_bytes, _phase in sequence:
        counts["logical_requests"] += 1
        if hot.contains(key):
            source = "hot"
            hot.touch(key)
        elif cold.contains(key):
            source = "cold"
            cold.touch(key)
            hot.admit(key)
        else:
            source = "backing_store"
            cold_victim = cold.admit(key)
            if cold_victim is not None:
                hot.remove(cold_victim)
            hot.admit(key)
        counts[f"{source}_hits"] += 1
        bytes_by_source[f"{source}_bytes"] += logical_bytes
    return {
        "policy": policy,
        "summary": {
            "logical_requests": counts["logical_requests"],
            "hot_hits": counts["hot_hits"],
            "cold_hits": counts["cold_hits"],
            "backing_store_hits": counts["backing_store_hits"],
            "hot_bytes": bytes_by_source["hot_bytes"],
            "cold_bytes": bytes_by_source["cold_bytes"],
            "backing_store_bytes": bytes_by_source["backing_store_bytes"],
        },
        "hot_evictions": hot.evictions,
        "cold_evictions": cold.evictions,
    }


def run_file(input_path: Path, output_path: Path) -> None:
    value = json.loads(input_path.read_text())
    result = replay(value)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(canonical_json(result))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run_file(arguments.input, arguments.output)

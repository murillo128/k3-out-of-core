#!/usr/bin/env python3
"""Bind Phase 10 disabled controls to the accepted Phase 9 replay contract."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from adapt_phase2_events import lru_config  # noqa: E402
from cache_policy_simulator import canonical_json, replay as phase9_python_replay  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "phase10"))
from prefetch_common import Phase10Error  # noqa: E402


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_phase9_input(
        profile: dict[str, Any],
        events: list[dict[str, Any]],
        limits: dict[str, int]) -> dict[str, Any]:
    sizes = {(item["layer"], item["expert"]): item["physical_bytes"]
        for item in profile["target"]["expert_bytes"]}
    footprints = set(sizes.values())
    if len(footprints) != 1:
        raise Phase10Error("Phase 9 equivalence requires one physical expert footprint")
    footprint = footprints.pop()
    if limits["cold_capacity_bytes"] % footprint:
        raise Phase10Error("Phase 9 equivalence requires an integral cold-slot capacity")
    cold_slots = limits["cold_capacity_bytes"] // footprint
    hot_slots = limits["hot_capacity_slots"]
    if not 0 < hot_slots <= cold_slots:
        raise Phase10Error("Phase 9 equivalence requires inclusive hot/cold capacities")
    checkpoints = []
    for event in events:
        for layer_record in event["layers"]:
            demands = []
            for expert in layer_record["experts"]:
                key = (layer_record["layer"], expert)
                if key not in sizes:
                    raise Phase10Error("Phase 10 event references a key absent from the profile")
                demands.append({"layer": key[0], "expert": key[1], "occurrence_count": 1,
                    "logical_payload_bytes": sizes[key], "hot_admission": "MANDATORY_CURRENT_OUTPUT"})
            checkpoints.append({"checkpoint_ordinal": len(checkpoints) + 1,
                "ubatch_ordinal": len(checkpoints) + 1, "phase": "DECODE", "demands": demands})
    if not checkpoints:
        raise Phase10Error("Phase 9 equivalence requires at least one decode checkpoint")
    return {"schema_version": "cache-policy-replay-input-v1",
        "topology": {"routed_layers": profile["target"]["routed_layers"],
            "experts_per_layer": profile["target"]["experts_per_layer"],
            "physical_slot_footprint_bytes": footprint},
        "hot": {"slots": hot_slots, "config": lru_config()},
        "cold": {"slots": cold_slots, "config": lru_config()},
        "requests": [{"request_ordinal": 1, "checkpoints": checkpoints, "outcome": "SUCCESS"}]}


def run_phase9_native(executable: Path, value: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="phase10-phase9-equivalence-") as directory:
        request = Path(directory) / "request.json"
        output = Path(directory) / "output.json"
        request.write_text(canonical_json(value), encoding="utf-8")
        completed = subprocess.run([str(executable), "--input", str(request), "--output", str(output)],
            check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise Phase10Error(f"Phase 9 native replay failed with exit {completed.returncode}: "
                f"{completed.stderr.strip()}")
        try:
            return json.loads(output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise Phase10Error(f"Phase 9 native replay emitted invalid output: {error}") from error


def _demand_groups(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for event in events:
        if event["type"] == "DEMAND":
            groups.append([event])
        elif groups and event["type"] not in {"REQUEST_END"}:
            groups[-1].append(event)
    return groups


def normalize_phase9(output: dict[str, Any]) -> dict[str, Any]:
    hot_groups = _demand_groups(output["tiers"]["hot"]["events"])
    cold_groups = _demand_groups(output["tiers"]["cold"]["events"])
    if len(hot_groups) != len(cold_groups):
        raise Phase10Error("Phase 9 tiers emitted different demand counts")
    actions = []
    for ordinal, (hot, cold) in enumerate(zip(hot_groups, cold_groups), 1):
        hot_demand, cold_demand = hot[0], cold[0]
        key = (hot_demand["layer"], hot_demand["expert"])
        if key != (cold_demand["layer"], cold_demand["expert"]):
            raise Phase10Error("Phase 9 tier demand order diverged")
        hot_hit = next((item for item in hot if item["type"] == "HIT"), None)
        cold_hit = next((item for item in cold if item["type"] == "HIT"), None)
        source = "HOT" if hot_hit else "COLD" if cold_hit else "BACKING_STORE"
        hot_terminal = hot_hit or next((item for item in hot if item["type"] == "LOAD_COMPLETE"), None)
        cold_terminal = cold_hit or next((item for item in cold if item["type"] == "LOAD_COMPLETE"), None)
        if hot_terminal is None or cold_terminal is None:
            raise Phase10Error("Phase 9 demand has no terminal resident state")
        actions.append({"ordinal": ordinal, "layer": key[0], "expert": key[1], "source": source,
            "cold_slot": cold_terminal["slot"], "hot_slot": hot_terminal["slot"],
            "cold_evictions": [{"layer": item["layer"], "expert": item["expert"], "slot": item["slot"]}
                for item in cold if item["type"] == "EVICT"],
            "hot_evictions": [{"layer": item["layer"], "expert": item["expert"], "slot": item["slot"]}
                for item in hot if item["type"] == "EVICT"]})
    hot_resident = {(item["layer"], item["expert"]): item["slot"]
        for item in output["tiers"]["hot"]["resident"]}
    resident = [{"layer": item["layer"], "expert": item["expert"], "cold_slot": item["slot"],
        "hot_slot": hot_resident.get((item["layer"], item["expert"]), -1)}
        for item in output["tiers"]["cold"]["resident"]]
    resident.sort(key=lambda item: (item["layer"], item["expert"]))
    summary = {"demands": len(actions), "hot": sum(item["source"] == "HOT" for item in actions),
        "cold": sum(item["source"] == "COLD" for item in actions),
        "backing_store": sum(item["source"] == "BACKING_STORE" for item in actions)}
    final_state = {"actions": actions, "resident": resident, "summary": summary}
    return {**final_state, "final_state_sha256": canonical_sha256(final_state)}


def normalize_phase10(output: dict[str, Any]) -> dict[str, Any]:
    actions = []
    cold_evictions: list[dict[str, int]] = []
    hot_evictions: list[dict[str, int]] = []
    for item in output["action_stream"]:
        if item["type"] == "DEMAND_COLD_EVICT":
            cold_evictions.append({"layer": item["layer"], "expert": item["expert"], "slot": item["cold_slot"]})
        elif item["type"] == "DEMAND_HOT_EVICT":
            hot_evictions.append({"layer": item["layer"], "expert": item["expert"], "slot": item["hot_slot"]})
        elif item["type"] in {"DEMAND_HIT", "DEMAND_LOAD"}:
            actions.append({"ordinal": len(actions) + 1, "layer": item["layer"], "expert": item["expert"],
                "source": item.get("source_tier", "BACKING_STORE"), "cold_slot": item["cold_slot"],
                "hot_slot": item["hot_slot"], "cold_evictions": cold_evictions,
                "hot_evictions": hot_evictions})
            cold_evictions = []
            hot_evictions = []
    if cold_evictions or hot_evictions:
        raise Phase10Error("Phase 10 replay left demand evictions without a terminal action")
    resident = [{"layer": item["layer"], "expert": item["expert"], "cold_slot": item["cold_slot"],
        "hot_slot": item["hot_slot"]} for item in output["resident"]]
    resident.sort(key=lambda item: (item["layer"], item["expert"]))
    summary = {"demands": len(actions), "hot": sum(item["source"] == "HOT" for item in actions),
        "cold": sum(item["source"] == "COLD" for item in actions),
        "backing_store": sum(item["source"] == "BACKING_STORE" for item in actions)}
    final_state = {"actions": actions, "resident": resident, "summary": summary}
    return {**final_state, "final_state_sha256": canonical_sha256(final_state)}


def verify_disabled_equivalence(
        profile: dict[str, Any],
        events: list[dict[str, Any]],
        limits: dict[str, int],
        phase10_output: dict[str, Any],
        phase9_native: Path) -> dict[str, Any]:
    phase9_input = build_phase9_input(profile, events, limits)
    python_output = phase9_python_replay(phase9_input)
    native_output = run_phase9_native(phase9_native, phase9_input)
    if native_output != python_output:
        raise Phase10Error("accepted Phase 9 native and independent Python replay outputs diverged")
    phase9_state = normalize_phase9(python_output)
    phase10_state = normalize_phase10(phase10_output)
    if phase10_state != phase9_state:
        raise Phase10Error(f"disabled Phase 10 hierarchy diverged from Phase 9: "
            f"phase9={phase9_state['final_state_sha256']} phase10={phase10_state['final_state_sha256']}")
    return {"schema_version": "phase10-phase9-disabled-equivalence-v1", "status": "pass",
        "phase9_input_sha256": canonical_sha256(phase9_input),
        "phase9_python_output_sha256": canonical_sha256(python_output),
        "phase9_native_output_sha256": canonical_sha256(native_output),
        "phase9_native_python_exact": True,
        "phase10_actions_residency_exact": True,
        "normalized_actions_sha256": canonical_sha256(phase9_state["actions"]),
        "normalized_resident_sha256": canonical_sha256(phase9_state["resident"]),
        "normalized_summary_sha256": canonical_sha256(phase9_state["summary"]),
        "normalized_final_state_sha256": phase9_state["final_state_sha256"],
        "demand_actions": len(phase9_state["actions"]),
        "phase9_final_digests": {tier: python_output["tiers"][tier]["final_digest"]
            for tier in ("hot", "cold")},
        "phase10_state_digest": phase10_output["state_digest"]}

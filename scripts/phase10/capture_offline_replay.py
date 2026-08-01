#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build_prefetch_profile import MATRIX, build
from prefetch_common import (PHASE2_ARCHIVE_SHA256, PHASE9_MANIFEST_SHA256, Phase10Error, break_even,
    build_fingerprint, canonical_bytes, fold_membership, load_json, read_phase2_corpus, token_events,
    validate_profile, write_json)
from replay_prefetch import replay


POLICY_GRID = [
    ("STATIC_LAYER", 0),
    ("PREVIOUS_TOKEN", 0),
    ("RANDOM_BASELINE", 0),
    ("CROSS_LAYER_TRANSITION", 0),
] + [("TEMPORAL_FREQUENCY", window) for window in MATRIX["temporal_windows"]]

CONTROLS = [
    ("DEMAND_BASELINE", "OFF", "OFF", "ISSUE_AHEAD"),
    ("SERIAL_CONTROL", "OFF", "OFF", "SERIAL"),
    ("BLOCKING_HOT", "OFF", "BLOCKING_HOT", "ISSUE_AHEAD"),
]


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def evidence_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def run_native(executable: Path, request_path: Path) -> tuple[dict[str, Any], bytes]:
    completed = subprocess.run([str(executable), str(request_path)], check=False, capture_output=True)
    if completed.returncode != 0:
        raise Phase10Error(f"native replay failed with exit {completed.returncode}: "
            f"{completed.stderr.decode(errors='replace').strip()}")
    try:
        return json.loads(completed.stdout), completed.stdout
    except json.JSONDecodeError as error:
        raise Phase10Error(f"native replay emitted invalid JSON: {error}") from error


def expect_native_rejection(executable: Path, request_path: Path) -> bool:
    return subprocess.run([str(executable), str(request_path)], check=False, capture_output=True).returncode != 0


def byte_map(profile: dict[str, Any]) -> dict[tuple[int, int], int]:
    return {(record["layer"], record["expert"]): record["physical_bytes"]
        for record in profile["target"]["expert_bytes"]}


def deadline_demands(events: list[dict[str, Any]], trigger: str) -> tuple[dict[tuple[int, int], set[int]], int]:
    result: dict[tuple[int, int], set[int]] = {}
    if trigger == "TOKEN_END":
        selected_events = enumerate(events[1:], 1)
    else:
        selected_events = enumerate(events)
    demand_count = 0
    for token, event in selected_events:
        layers = event["layers"] if trigger == "TOKEN_END" else event["layers"][1:]
        for layer in layers:
            selected = set(layer["experts"])
            result[(token, layer["layer"])] = selected
            demand_count += len(selected)
    return result, demand_count


def score_replay(output: dict[str, Any], score_from_token: int, scoring_events: list[dict[str, Any]]) -> dict[str, int]:
    candidates = {item["flight_ordinal"]: item for item in output["candidate_stream"]}

    def scored(flight: int) -> bool:
        candidate = candidates[flight]
        deadline = candidate["trigger_token"] if candidate["trigger"] == "ROUTER_RESULT" else \
            candidate["trigger_token"] + 1
        return deadline >= score_from_token

    enqueues = [item for item in output["action_stream"] if item["type"] == "ENQUEUE" and
        scored(item["flight_ordinal"])]
    rejections = [item for item in output["action_stream"] if item["type"] == "REJECTED" and
        scored(item["flight_ordinal"])]
    timely = [item for item in output["outcome_stream"] if item["type"] == "TIMELY_USEFUL" and
        item["token"] >= score_from_token]
    wasted = [item for item in output["outcome_stream"] if item["type"] in
        {"WASTED_UNUSED", "CANCELLED_BEFORE_IO", "CANCELLED_DRAINED"} and
        item["token"] >= score_from_token]
    demand_count = sum(len(layer["experts"]) for event in scoring_events for layer in event["layers"])
    accepted = len(enqueues)
    successes = len(timely)
    precision_bps = (successes*10000)//accepted if accepted else 0
    recall_bps = (successes*10000)//demand_count if demand_count else 0
    enqueue_bytes = {item["flight_ordinal"]: (item["submitted_storage_bytes"], item["submitted_h2d_bytes"])
        for item in enqueues}
    wasted_storage = sum(enqueue_bytes.get(item["flight_ordinal"], (0, 0))[0] for item in wasted)
    wasted_h2d = sum(enqueue_bytes.get(item["flight_ordinal"], (0, 0))[1] for item in wasted)
    scored_actions = [item for item in output["action_stream"] if item.get("token", 0) >= score_from_token]
    return {"candidate_proposals": sum(scored(flight) for flight in candidates), "predictions": accepted,
        "timely_successes": successes, "actual_demands": demand_count, "precision_bps": precision_bps,
        "recall_bps": recall_bps, "rejections": len(rejections),
        "budget_rejections": sum("budget" in item["reason"] for item in rejections),
        "admission_rejections": sum(item["reason"] == "demand_state_protected" for item in rejections),
        "predicted_physical_bytes": sum(item[0] for item in enqueue_bytes.values()),
        "predicted_h2d_bytes": sum(item[1] for item in enqueue_bytes.values()),
        "wasted_physical_bytes": wasted_storage, "wasted_h2d_bytes": wasted_h2d,
        "seed_first_use_hits": sum(item["type"] == "DEMAND_HIT" and
            item.get("source_origin") == "STATIC_SEED" for item in scored_actions),
        "demand_hits": sum(item["type"] == "DEMAND_HIT" for item in scored_actions),
        "demand_loads": sum(item["type"] == "DEMAND_LOAD" for item in scored_actions),
        "prevented_demand_evictions": output["summary"]["prevented_demand_evictions"]}


def renumber_events(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for group in groups:
        for event in group:
            result.append({"token": len(result), "layers": event["layers"]})
    return result


def sequence_inputs(
        corpus: dict[str, Any],
        membership: dict[str, Any],
        prompt: str) -> dict[str, tuple[list[dict[str, Any]], int]]:
    evaluation = token_events(corpus["traces"][prompt], decode_only=True)
    if not evaluation:
        return {}
    warm_prefix = evaluation
    domain_prefix = token_events(corpus["traces"][membership["training"][-1]], decode_only=True)
    return {
        "cold": (renumber_events(evaluation), 0),
        "warm_decode": (renumber_events(warm_prefix, evaluation), len(warm_prefix)),
        "domain_shift": (renumber_events(domain_prefix, evaluation), len(domain_prefix)),
    }


def phase9_capacity_points(manifest_path: Path, artifact: str, physical_bytes: int) -> tuple[dict[str, dict[str, int]], str]:
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != PHASE9_MANIFEST_SHA256:
        raise Phase10Error("Phase 9 manifest identity mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    format_name = f"tiny-k3-{artifact}"
    recommendations = [item for item in manifest["selection"]["budget_recommendations"]
        if item["model_format"] == format_name]
    if len(recommendations) != 1:
        raise Phase10Error("Phase 9 recommendation is unavailable")
    recommendation = recommendations[0]
    selected_cold = recommendation["recommended_cold_bytes"]
    selected_slots = recommendation["selected_cell"]["slots"]
    if selected_cold != selected_slots*physical_bytes or selected_slots < 2:
        raise Phase10Error("Phase 9 capacity is incompatible with the exact expert footprint")
    working_record = manifest["evidence"]["working_sets"]
    working_path = Path(working_record["path"]).resolve()
    if hashlib.sha256(working_path.read_bytes()).hexdigest() != working_record["sha256"]:
        raise Phase10Error("Phase 9 working-set identity mismatch")
    working = json.loads(working_path.read_text(encoding="utf-8"))
    cases = [item for item in working["cases"] if item["name"].startswith(f"tiny-{artifact}-original-cold-")]
    hot_slots = {item["observed_capacities"]["hot_effective_slots"] for item in cases}
    if len(hot_slots) != 1:
        raise Phase10Error("Phase 9 hot capacity is ambiguous")
    selected_hot = hot_slots.pop()
    if selected_hot < 2:
        raise Phase10Error("Phase 9 hot capacity has no immediate lower neighbor")
    points = {
        "selected_minus_one_slot": {"cold_capacity_bytes": selected_cold - physical_bytes,
            "cold_slots": selected_slots - 1, "hot_capacity_slots": selected_hot - 1},
        "selected": {"cold_capacity_bytes": selected_cold, "cold_slots": selected_slots,
            "hot_capacity_slots": selected_hot},
        "selected_plus_one_slot": {"cold_capacity_bytes": selected_cold + physical_bytes,
            "cold_slots": selected_slots + 1, "hot_capacity_slots": selected_hot + 1},
    }
    return points, working_record["sha256"]


def replay_limits(capacity: dict[str, int], budget: int, active: bool) -> dict[str, int]:
    result = {"cold_capacity_bytes": capacity["cold_capacity_bytes"],
        "hot_capacity_slots": capacity["hot_capacity_slots"], "max_speculative_flights": capacity["hot_capacity_slots"],
        "max_speculative_storage_bytes_in_flight": budget, "max_speculative_h2d_bytes_in_flight": budget,
        "max_speculative_storage_bytes_per_token": budget, "max_speculative_h2d_bytes_per_token": budget,
        "max_speculative_cold_slots": capacity["hot_capacity_slots"],
        "max_speculative_hot_slots": capacity["hot_capacity_slots"]}
    if not active:
        for name in list(result):
            if name.startswith("max_speculative"):
                result[name] = 0
    return result


def observed_working_set(profile: dict[str, Any], events: list[dict[str, Any]]) -> int:
    sizes = byte_map(profile)
    maximum = 0
    for event in events:
        total = sum(sizes[(layer["layer"], expert)] for layer in event["layers"] for expert in layer["experts"])
        maximum = max(maximum, total)
    if maximum == 0:
        sizes = byte_map(profile)
        maximum = sum(sum(sorted(sizes[(layer, expert)] for expert in range(profile["target"]["experts_per_layer"]))[
            :profile["target"]["experts_per_token"]]) for layer in profile["target"]["routed_layers"])
    return maximum


def build_calibration_profile(
        archive: Path,
        storage_map: Path,
        artifact: str,
        fold: int,
        costs: Path) -> dict[str, Any]:
    arguments = SimpleNamespace(archive=str(archive), storage_map=str(storage_map), artifact=artifact, fold=fold,
        costs=str(costs), transport="BUFFERED", readiness="DEVICE_READY", policy="TEMPORAL_FREQUENCY",
        candidates=2, temporal_window=4, seed_slots=14)
    first = build(arguments)
    second = build(arguments)
    if canonical_bytes(first) != canonical_bytes(second):
        raise Phase10Error("profile builder is nondeterministic")
    return first


def replay_cell(
        native: Path,
        profile_path: Path,
        policy: str,
        transport: str,
        window: int,
        candidates: int,
        events: list[dict[str, Any]],
        limits: dict[str, int],
        readiness: str,
        seed_mode: str,
        demand_mode: str,
        directory: Path) -> dict[str, Any]:
    request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": policy, "transport": transport, "readiness": readiness, "temporal_window_tokens": window,
        "candidates_per_target": candidates, "request_ordinal": 1, "events": events, "completion_order": [],
        "limits": limits, "seed_mode": seed_mode, "demand_mode": demand_mode}
    request_path = directory / "request.json"
    write_json(request_path, request)
    native_first, native_bytes_first = run_native(native, request_path)
    native_second, native_bytes_second = run_native(native, request_path)
    python_first = replay(load_json(request_path))
    python_second = replay(load_json(request_path))
    if native_bytes_first != native_bytes_second or canonical_bytes(python_first) != canonical_bytes(python_second):
        raise Phase10Error("replay repeatability failure")
    if native_first != python_first:
        raise Phase10Error(f"native/Python replay mismatch for {policy}/W{window}/C{candidates}: "
            f"native={sha256(native_first)} python={sha256(python_first)}")
    shuffled = copy.deepcopy(request)
    shuffled["completion_order"] = list(reversed(range(len(native_first["candidate_stream"]))))
    write_json(request_path, shuffled)
    native_shuffled, _ = run_native(native, request_path)
    python_shuffled = replay(load_json(request_path))
    if native_shuffled != native_first or python_shuffled != python_first:
        raise Phase10Error("completion-order permutation changed canonical replay")
    return native_first


def python_replay_cell(
        profile_path: Path,
        policy: str,
        transport: str,
        window: int,
        candidates: int,
        events: list[dict[str, Any]],
        limits: dict[str, int],
        readiness: str,
        seed_mode: str,
        demand_mode: str) -> dict[str, Any]:
    request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": policy, "transport": transport, "readiness": readiness, "temporal_window_tokens": window,
        "candidates_per_target": candidates, "request_ordinal": 1, "events": events,
        "completion_order": [], "limits": limits, "seed_mode": seed_mode, "demand_mode": demand_mode}
    return replay(request)


def malformed_cases(native: Path, profile_path: Path, events: list[dict[str, Any]], directory: Path) -> dict[str, bool]:
    active_limits = {"cold_capacity_bytes": 1 << 30, "hot_capacity_slots": 32,
        "max_speculative_flights": 32, "max_speculative_storage_bytes_in_flight": 1 << 30,
        "max_speculative_h2d_bytes_in_flight": 1 << 30, "max_speculative_storage_bytes_per_token": 1 << 30,
        "max_speculative_h2d_bytes_per_token": 1 << 30, "max_speculative_cold_slots": 32,
        "max_speculative_hot_slots": 32}
    base = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": "PREVIOUS_TOKEN", "transport": "BUFFERED", "readiness": "DEVICE_READY", "temporal_window_tokens": 0,
        "candidates_per_target": 2, "request_ordinal": 1, "events": events, "completion_order": [],
        "limits": active_limits, "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}
    cases = {}
    variants = {}
    out_of_order = copy.deepcopy(base)
    out_of_order["events"][0]["token"] = 1
    variants["out_of_order_token"] = out_of_order
    duplicate = copy.deepcopy(base)
    duplicate["events"][0]["layers"][0]["experts"][1] = duplicate["events"][0]["layers"][0]["experts"][0]
    variants["duplicate_selected_expert"] = duplicate
    future = copy.deepcopy(base)
    future["future_events"] = []
    variants["future_field"] = future
    for name, request in variants.items():
        request_path = directory / f"malformed-{name}.json"
        write_json(request_path, request)
        native_rejected = expect_native_rejection(native, request_path)
        try:
            replay(request)
            python_rejected = False
        except (Phase10Error, KeyError, TypeError, ValueError):
            python_rejected = True
        cases[name] = native_rejected and python_rejected
    duplicate_json_path = directory / "malformed-duplicate-json-key.json"
    duplicate_json_path.write_text('{"schema_version":"phase10-prefetch-replay-v1","schema_version":"duplicate"}\n',
        encoding="utf-8")
    cases["duplicate_json_key"] = expect_native_rejection(native, duplicate_json_path)
    return cases


def capture(args: argparse.Namespace) -> dict[str, Any]:
    native = Path(args.native_replay).resolve()
    if not native.is_file():
        raise Phase10Error("native replay executable is unavailable")
    archive = Path(args.archive).resolve()
    for name in ("project_head", "nested_head"):
        value = getattr(args, name)
        if len(value) != 40 or any(byte not in "0123456789abcdef" for byte in value):
            raise Phase10Error(f"{name} must be a lowercase commit SHA")
    archive_sha256 = hashlib.sha256()
    with archive.open("rb") as handle:
        while chunk := handle.read(1024*1024):
            archive_sha256.update(chunk)
    archive_sha256 = archive_sha256.hexdigest()
    if archive_sha256 != PHASE2_ARCHIVE_SHA256:
        raise Phase10Error("Phase 2 archive identity mismatch")
    artifacts = {
        "f16": (Path(args.f16_storage_map).resolve(), Path(args.f16_costs).resolve()),
        "mxfp4": (Path(args.mxfp4_storage_map).resolve(), Path(args.mxfp4_costs).resolve()),
    }
    phase9_manifest = Path(args.phase9_manifest).resolve()
    folds = []
    raw_folds = []
    shortlist = []
    malformed = None
    agreement_checks = 0
    working_set_digests = set()
    with tempfile.TemporaryDirectory(prefix="phase10-offline-") as temporary:
        root = Path(temporary)
        for artifact, (storage_map, costs_path) in artifacts.items():
            corpus = read_phase2_corpus(archive, artifact)
            cost_document = load_json(costs_path)
            if cost_document.get("project_head") != args.project_head or \
                    cost_document.get("nested_head") != args.nested_head:
                raise Phase10Error("cost evidence revision mismatch")
            physical_sizes = set(byte_map({"target": build_fingerprint(load_json(storage_map))}).values())
            if len(physical_sizes) != 1:
                raise Phase10Error("Phase 9 capacity neighbors require one exact physical expert footprint")
            capacities, working_set_digest = phase9_capacity_points(phase9_manifest, artifact, physical_sizes.pop())
            working_set_digests.add(working_set_digest)
            eligible_costs = [item["profile_record"] for item in cost_document["envelopes"] if item["eligible"]]
            if not eligible_costs:
                raise Phase10Error("artifact has no eligible exact cost envelope")
            readiness_values = sorted({item["readiness"] for item in eligible_costs})
            for fold_index in range(6):
                profile = build_calibration_profile(archive, storage_map, artifact, fold_index, costs_path)
                validate_profile(profile)
                profile_path = root / f"{artifact}-fold{fold_index}.json"
                write_json(profile_path, profile)
                membership = fold_membership(fold_index)
                validation_events = token_events(corpus["traces"][membership["validation"]], decode_only=True)
                test_events = token_events(corpus["traces"][membership["test"]], decode_only=True)
                sequences = sequence_inputs(corpus, membership, membership["validation"])
                test_sequences = sequence_inputs(corpus, membership, membership["test"])
                working_set = observed_working_set(profile, validation_events)
                maximum_key = max(byte_map(profile).values())
                budgets = {"below_working_set": max(maximum_key, working_set//2),
                    "at_working_set": working_set, "above_working_set": working_set*2}
                cells = []
                replay_digests = []
                configurations = [{"policy": policy, "runtime_policy": policy, "seed_mode": "OFF",
                    "demand_mode": "ISSUE_AHEAD", "window": window,
                    "candidate_grid": sorted(set([1, profile["target"]["experts_per_token"],
                        2*profile["target"]["experts_per_token"], profile["target"]["experts_per_layer"]]))}
                    for policy, window in POLICY_GRID]
                configurations.extend({"policy": name, "runtime_policy": runtime_policy, "seed_mode": seed_mode,
                    "demand_mode": demand_mode, "window": 0, "candidate_grid": [0]}
                    for name, runtime_policy, seed_mode, demand_mode in CONTROLS)
                for configuration in configurations:
                    active = configuration["runtime_policy"] != "OFF"
                    for candidates in configuration["candidate_grid"]:
                        for sequence_name, (events, score_from_token) in sequences.items():
                            for readiness in readiness_values:
                                if configuration["seed_mode"] == "BLOCKING_HOT" and readiness != "DEVICE_READY":
                                    continue
                                for cost in (item for item in eligible_costs if item["readiness"] == readiness):
                                    native_limits = replay_limits(
                                        capacities["selected"], budgets["at_working_set"], active)
                                    native_output = replay_cell(native, profile_path, configuration["runtime_policy"],
                                        cost["transport"], configuration["window"], candidates, events,
                                        native_limits, readiness, configuration["seed_mode"],
                                        configuration["demand_mode"], root)
                                    agreement_checks += 1
                                    replay_digests.append({"policy": configuration["policy"],
                                        "runtime_policy": configuration["runtime_policy"],
                                        "seed_mode": configuration["seed_mode"],
                                        "demand_mode": configuration["demand_mode"], "window": configuration["window"],
                                        "candidates": candidates, "sequence": sequence_name,
                                        "transport": cost["transport"], "readiness": readiness,
                                        "candidate_stream_sha256": sha256(native_output["candidate_stream"]),
                                        "action_stream_sha256": sha256(native_output["action_stream"]),
                                        "outcome_stream_sha256": sha256(native_output["outcome_stream"]),
                                        "state_digest": native_output["state_digest"],
                                        "predictor_state_digest": native_output["predictor_state_digest"],
                                        "phase9_passthrough_sha256": native_output["phase9_passthrough_sha256"]})
                                    for capacity_name, capacity in capacities.items():
                                        for budget_name, budget in budgets.items():
                                            selected_point = capacity_name == "selected" and \
                                                budget_name == "at_working_set"
                                            output = native_output if selected_point else python_replay_cell(profile_path,
                                                configuration["runtime_policy"], cost["transport"],
                                                configuration["window"], candidates, events,
                                                replay_limits(capacity, budget, active), readiness,
                                                configuration["seed_mode"], configuration["demand_mode"])
                                            metrics = score_replay(output, score_from_token, validation_events)
                                            utility = break_even(cost)
                                            net_utility = metrics["timely_successes"]*utility["hidden_benefit_ns"] - \
                                                (metrics["predictions"] - metrics["timely_successes"])*utility["waste_cost_ns"]
                                            cells.append({"stage": "validation", "policy": configuration["policy"],
                                                "runtime_policy": configuration["runtime_policy"],
                                                "seed_mode": configuration["seed_mode"],
                                                "demand_mode": configuration["demand_mode"],
                                                "temporal_window_tokens": configuration["window"],
                                                "candidates_per_target": candidates, "sequence": sequence_name,
                                                "capacity_point": capacity_name, **capacity,
                                                "budget_point": budget_name, "budget_bytes_per_token": budget,
                                                "transport": cost["transport"], "readiness": cost["readiness"],
                                                "break_even_bps": cost["break_even_bps"], **metrics,
                                                "validation_net_utility_ns": net_utility,
                                                "passes_point_break_even": metrics["predictions"] > 0 and
                                                    metrics["precision_bps"] >= cost["break_even_bps"]})
                fold_shortlist = []
                for envelope in {(cell["transport"], cell["readiness"]) for cell in cells}:
                    for policy in ("STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY",
                            "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"):
                        eligible = [cell for cell in cells if (cell["transport"], cell["readiness"]) == envelope and
                            cell["policy"] == policy and cell["budget_point"] == "at_working_set" and
                            cell["capacity_point"] == "selected"]
                        if not eligible:
                            continue
                        tuning_keys = {(item["temporal_window_tokens"], item["candidates_per_target"])
                            for item in eligible}
                        aggregates = []
                        for window, candidates in tuning_keys:
                            group = [item for item in eligible if item["temporal_window_tokens"] == window and
                                item["candidates_per_target"] == candidates]
                            predictions = sum(item["predictions"] for item in group)
                            successes = sum(item["timely_successes"] for item in group)
                            aggregates.append({"policy": policy, "runtime_policy": policy, "seed_mode": "OFF",
                                "demand_mode": "ISSUE_AHEAD", "temporal_window_tokens": window,
                                "candidates_per_target": candidates, "transport": envelope[0],
                                "readiness": envelope[1], "budget_point": "at_working_set",
                                "capacity_point": "selected", "sequence": "aggregate_validation",
                                "predictions": predictions, "timely_successes": successes,
                                "precision_bps": (successes*10000)//predictions if predictions else 0,
                                "wasted_physical_bytes": sum(item["wasted_physical_bytes"] for item in group),
                                "validation_net_utility_ns": sum(item["validation_net_utility_ns"] for item in group),
                                "break_even_bps": group[0]["break_even_bps"]})
                        selected = sorted(aggregates, key=lambda cell: (-cell["validation_net_utility_ns"],
                            cell["wasted_physical_bytes"], cell["temporal_window_tokens"],
                            cell["candidates_per_target"]))[0]
                        selected["passes_point_break_even"] = selected["predictions"] > 0 and \
                            selected["precision_bps"] >= selected["break_even_bps"]
                        if policy == "RANDOM_BASELINE":
                            disposition = "control_only"
                        elif policy in {"STATIC_LAYER", "PREVIOUS_TOKEN"}:
                            disposition = "retained_comparator" if selected["passes_point_break_even"] else \
                                "comparator_only_below_break_even"
                        else:
                            disposition = "retained" if selected["passes_point_break_even"] else \
                                "rejected_below_break_even"
                        fold_shortlist.append({**selected, "disposition": disposition})
                    for control, disposition in (("DEMAND_BASELINE", "demand_baseline_control"),
                            ("SERIAL_CONTROL", "serial_control_only")):
                        control_cells = [cell for cell in cells if
                            (cell["transport"], cell["readiness"]) == envelope and cell["policy"] == control and
                            cell["budget_point"] == "at_working_set" and cell["capacity_point"] == "selected"]
                        if control_cells:
                            fold_shortlist.append({"policy": control, "runtime_policy": "OFF", "seed_mode": "OFF",
                                "demand_mode": "SERIAL" if control == "SERIAL_CONTROL" else "ISSUE_AHEAD",
                                "temporal_window_tokens": 0, "candidates_per_target": 0,
                                "transport": envelope[0], "readiness": envelope[1],
                                "budget_point": "at_working_set", "capacity_point": "selected",
                                "sequence": "aggregate_validation",
                                "demand_hits": sum(item["demand_hits"] for item in control_cells),
                                "demand_loads": sum(item["demand_loads"] for item in control_cells),
                                "disposition": disposition})
                    seed_cells = [cell for cell in cells if (cell["transport"], cell["readiness"]) == envelope and
                        cell["policy"] == "BLOCKING_HOT" and cell["budget_point"] == "at_working_set" and
                        cell["capacity_point"] == "selected"]
                    if seed_cells:
                        fold_shortlist.append({"policy": "BLOCKING_HOT", "runtime_policy": "OFF",
                            "seed_mode": "BLOCKING_HOT", "demand_mode": "ISSUE_AHEAD",
                            "temporal_window_tokens": 0, "candidates_per_target": 0,
                            "transport": envelope[0], "readiness": envelope[1],
                            "budget_point": "at_working_set", "capacity_point": "selected",
                            "sequence": "aggregate_validation",
                            "seed_first_use_hits": sum(item["seed_first_use_hits"] for item in seed_cells),
                            "demand_loads": sum(item["demand_loads"] for item in seed_cells),
                            "disposition": "retained_for_online_seed_evaluation"})
                cost_by_envelope = {(item["transport"], item["readiness"]): item for item in eligible_costs}
                for frozen in fold_shortlist:
                    held_out_cells = []
                    active = frozen["runtime_policy"] != "OFF"
                    for sequence_name, (events, score_from_token) in test_sequences.items():
                        for capacity_name, capacity in capacities.items():
                            for budget_name, budget in budgets.items():
                                output = python_replay_cell(profile_path, frozen["runtime_policy"],
                                    frozen["transport"], frozen["temporal_window_tokens"],
                                    frozen["candidates_per_target"], events,
                                    replay_limits(capacity, budget, active), frozen["readiness"],
                                    frozen["seed_mode"], frozen["demand_mode"])
                                metrics = score_replay(output, score_from_token, test_events)
                                cost = cost_by_envelope[(frozen["transport"], frozen["readiness"])]
                                utility = break_even(cost)
                                net_utility = metrics["timely_successes"]*utility["hidden_benefit_ns"] - \
                                    (metrics["predictions"] - metrics["timely_successes"])*utility["waste_cost_ns"]
                                held_out_cells.append({"stage": "held_out_test", "policy": frozen["policy"],
                                    "runtime_policy": frozen["runtime_policy"], "seed_mode": frozen["seed_mode"],
                                    "demand_mode": frozen["demand_mode"],
                                    "temporal_window_tokens": frozen["temporal_window_tokens"],
                                    "candidates_per_target": frozen["candidates_per_target"],
                                    "sequence": sequence_name, "capacity_point": capacity_name, **capacity,
                                    "budget_point": budget_name, "budget_bytes_per_token": budget,
                                    "transport": frozen["transport"], "readiness": frozen["readiness"],
                                    "break_even_bps": cost["break_even_bps"], **metrics,
                                    "test_net_utility_ns": net_utility,
                                    "passes_point_break_even": metrics["predictions"] > 0 and
                                        metrics["precision_bps"] >= cost["break_even_bps"]})
                    cells.extend(held_out_cells)
                    selected_test = [cell for cell in held_out_cells if cell["capacity_point"] == "selected" and
                        cell["budget_point"] == "at_working_set"]
                    predictions = sum(item["predictions"] for item in selected_test)
                    successes = sum(item["timely_successes"] for item in selected_test)
                    frozen["held_out_test"] = {"status": "evaluated" if selected_test else "no_decode_events",
                        "sequences": len(selected_test), "predictions": predictions,
                        "timely_successes": successes,
                        "precision_bps": (successes*10000)//predictions if predictions else 0,
                        "test_net_utility_ns": sum(item["test_net_utility_ns"] for item in selected_test),
                        "selection_frozen_before_test": True}
                if validation_events:
                    shortlist.extend({"artifact": artifact, "fold": fold_index, **item} for item in fold_shortlist)
                else:
                    fold_shortlist = []
                cells_sha256 = sha256(cells)
                raw_folds.append({"artifact": artifact, "fold": fold_index, "cells": cells})
                folds.append({"artifact": artifact, "fold": fold_index, "membership": membership,
                    "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                    "profile_repeatability": "byte_identical", "validation_events": len(validation_events),
                    "validation_status": "evaluated" if validation_events else "no_decode_events",
                    "observed_token_working_set_bytes": working_set, "budgets": budgets,
                    "phase9_capacity_points": capacities,
                    "sequence_events": {name: {"total": len(value[0]), "score_from_token": value[1]}
                        for name, value in sequences.items()},
                    "test_sequence_events": {name: {"total": len(value[0]), "score_from_token": value[1]}
                        for name, value in test_sequences.items()},
                    "replay": replay_digests, "cell_count": len(cells), "cells_sha256": cells_sha256,
                    "shortlist": fold_shortlist})
                if malformed is None and validation_events:
                    malformed = malformed_cases(native, profile_path, validation_events, root)
    if malformed is None or not all(malformed.values()):
        raise Phase10Error("one or more malformed replay cases were accepted")
    if len(working_set_digests) != 1:
        raise Phase10Error("Phase 9 working-set evidence identity changed across artifacts")
    cells_document = {"schema_version": "phase10-offline-replay-cells-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "matrix_sha256": sha256(MATRIX), "folds": raw_folds}
    cells_bytes = canonical_bytes(cells_document)
    compressed = gzip.compress(cells_bytes, compresslevel=9, mtime=0)
    cells_path = Path(args.cells_output).resolve()
    cells_path.parent.mkdir(parents=True, exist_ok=True)
    cells_path.write_bytes(compressed)
    cells_artifact = {"path": evidence_path(cells_path), "size": len(compressed),
        "sha256": hashlib.sha256(compressed).hexdigest(), "compression": "gzip-mtime-zero",
        "uncompressed_size": len(cells_bytes), "uncompressed_sha256": hashlib.sha256(cells_bytes).hexdigest(),
        "cell_count": sum(item["cell_count"] for item in folds)}
    phase9_hashes = {item["phase9_passthrough_sha256"] for fold in folds for item in fold["replay"]}
    return {"schema_version": "phase10-offline-replay-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "phase2_archive_sha256": archive_sha256,
        "phase9_manifest_sha256": PHASE9_MANIFEST_SHA256,
        "phase9_working_set_sha256": next(iter(working_set_digests)),
        "matrix": MATRIX, "matrix_sha256": sha256(MATRIX), "folds": folds, "shortlist": shortlist,
        "cells_artifact": cells_artifact,
        "malformed_rejections": malformed, "native_python_event_agreement": True,
        "native_python_event_agreement_checks": agreement_checks,
        "repeatability": "byte_identical", "shuffled_completion_invariant": True,
        "phase9_passthrough_unique_digests": len(phase9_hashes),
        "cache_admission_lifecycle_scope":
            "inclusive_global_lru_always_demand_protected_admission_with_cost_bound_ready_late_cancel_deadlines_and_seed"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--f16-storage-map", required=True)
    parser.add_argument("--mxfp4-storage-map", required=True)
    parser.add_argument("--f16-costs", required=True)
    parser.add_argument("--mxfp4-costs", required=True)
    parser.add_argument("--native-replay", required=True)
    parser.add_argument("--phase9-manifest", required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cells-output", required=True)
    args = parser.parse_args()
    try:
        write_json(args.output, capture(args))
        print(Path(args.output))
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

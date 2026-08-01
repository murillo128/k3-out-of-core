#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from build_prefetch_profile import MATRIX, build
from prefetch_common import (PHASE2_ARCHIVE_SHA256, Phase10Error, break_even, canonical_bytes, fold_membership,
    load_json, read_phase2_corpus, token_events, validate_profile, write_json)
from replay_prefetch import replay


POLICY_GRID = [
    ("OFF", 0),
    ("STATIC_LAYER", 0),
    ("PREVIOUS_TOKEN", 0),
    ("RANDOM_BASELINE", 0),
    ("CROSS_LAYER_TRANSITION", 0),
] + [("TEMPORAL_FREQUENCY", window) for window in MATRIX["temporal_windows"]]


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


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


def score_candidates(
        profile: dict[str, Any],
        output: dict[str, Any],
        budget_bytes: int) -> dict[str, int]:
    stream = output["candidate_stream"]
    trigger = "ROUTER_RESULT" if output["policy"] == "CROSS_LAYER_TRANSITION" else "TOKEN_END"
    demands, demand_count = deadline_demands(profile["_events"], trigger)
    sizes = byte_map(profile)
    charged: dict[int, int] = {}
    predictions = 0
    successes = 0
    rejected = 0
    predicted_bytes = 0
    wasted_bytes = 0
    for candidate in stream:
        deadline = candidate["trigger_token"] if trigger == "ROUTER_RESULT" else candidate["trigger_token"] + 1
        size = sizes[(candidate["target_layer"], candidate["expert"])]
        if charged.get(deadline, 0) + size > budget_bytes:
            rejected += 1
            continue
        charged[deadline] = charged.get(deadline, 0) + size
        predictions += 1
        predicted_bytes += size
        useful = candidate["expert"] in demands.get((deadline, candidate["target_layer"]), set())
        successes += int(useful)
        if not useful:
            wasted_bytes += size
    precision_bps = (successes*10000)//predictions if predictions else 0
    recall_bps = (successes*10000)//demand_count if demand_count else 0
    return {"predictions": predictions, "timely_successes": successes, "actual_demands": demand_count,
        "precision_bps": precision_bps, "recall_bps": recall_bps, "budget_rejections": rejected,
        "predicted_physical_bytes": predicted_bytes, "wasted_physical_bytes": wasted_bytes}


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
        window: int,
        candidates: int,
        events: list[dict[str, Any]],
        directory: Path) -> dict[str, Any]:
    request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": policy, "readiness": "DEVICE_READY", "temporal_window_tokens": window,
        "candidates_per_target": candidates, "request_ordinal": 1, "events": events, "completion_order": []}
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
    shuffled["completion_order"] = [3, 0, 2, 1]
    write_json(request_path, shuffled)
    native_shuffled, _ = run_native(native, request_path)
    python_shuffled = replay(load_json(request_path))
    if native_shuffled != native_first or python_shuffled != python_first:
        raise Phase10Error("completion-order permutation changed canonical replay")
    return native_first


def malformed_cases(native: Path, profile_path: Path, events: list[dict[str, Any]], directory: Path) -> dict[str, bool]:
    base = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": "PREVIOUS_TOKEN", "readiness": "DEVICE_READY", "temporal_window_tokens": 0,
        "candidates_per_target": 2, "request_ordinal": 1, "events": events, "completion_order": []}
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
    folds = []
    shortlist = []
    malformed = None
    with tempfile.TemporaryDirectory(prefix="phase10-offline-") as temporary:
        root = Path(temporary)
        for artifact, (storage_map, costs_path) in artifacts.items():
            corpus = read_phase2_corpus(archive, artifact)
            cost_document = load_json(costs_path)
            if cost_document.get("project_head") != args.project_head or \
                    cost_document.get("nested_head") != args.nested_head:
                raise Phase10Error("cost evidence revision mismatch")
            for fold_index in range(6):
                profile = build_calibration_profile(archive, storage_map, artifact, fold_index, costs_path)
                validate_profile(profile)
                profile_path = root / f"{artifact}-fold{fold_index}.json"
                write_json(profile_path, profile)
                membership = fold_membership(fold_index)
                events = token_events(corpus["traces"][membership["validation"]], decode_only=True)
                profile["_events"] = events
                working_set = observed_working_set(profile, events)
                maximum_key = max(byte_map(profile).values())
                budgets = {"below_working_set": max(maximum_key, working_set//2),
                    "at_working_set": working_set, "above_working_set": working_set*2}
                cells = []
                replay_digests = []
                for policy, window in POLICY_GRID:
                    candidate_grid = [2] if policy == "OFF" else sorted(set([1, profile["target"]["experts_per_token"],
                        2*profile["target"]["experts_per_token"], profile["target"]["experts_per_layer"]]))
                    for candidates in candidate_grid:
                        output = replay_cell(native, profile_path, policy, window, candidates, events, root)
                        replay_digests.append({"policy": policy, "window": window, "candidates": candidates,
                            "candidate_stream_sha256": sha256(output["candidate_stream"]),
                            "state_digest": output["state_digest"],
                            "phase9_passthrough_sha256": output["phase9_passthrough_sha256"]})
                        if policy == "OFF":
                            continue
                        for budget_name, budget in budgets.items():
                            metrics = score_candidates(profile, output, budget)
                            for envelope in cost_document["envelopes"]:
                                if not envelope["eligible"]:
                                    continue
                                cost = envelope["profile_record"]
                                utility = break_even(cost)
                                net_utility = metrics["timely_successes"]*utility["hidden_benefit_ns"] - \
                                    (metrics["predictions"] - metrics["timely_successes"])*utility["waste_cost_ns"]
                                cells.append({"policy": policy, "temporal_window_tokens": window,
                                    "candidates_per_target": candidates, "budget_point": budget_name,
                                    "budget_bytes_per_token": budget, "transport": cost["transport"],
                                    "readiness": cost["readiness"], "break_even_bps": cost["break_even_bps"],
                                    **metrics, "validation_net_utility_ns": net_utility,
                                    "passes_point_break_even": metrics["precision_bps"] >= cost["break_even_bps"]})
                fold_shortlist = []
                for envelope in {(cell["transport"], cell["readiness"]) for cell in cells}:
                    for policy in ("STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY",
                            "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"):
                        eligible = [cell for cell in cells if (cell["transport"], cell["readiness"]) == envelope and
                            cell["policy"] == policy and cell["budget_point"] == "at_working_set"]
                        if not eligible:
                            continue
                        selected = sorted(eligible, key=lambda cell: (-cell["validation_net_utility_ns"],
                            cell["wasted_physical_bytes"], cell["temporal_window_tokens"],
                            cell["candidates_per_target"]))[0]
                        disposition = "control_only" if policy == "RANDOM_BASELINE" else \
                            ("retained" if selected["passes_point_break_even"] else "rejected_below_break_even")
                        fold_shortlist.append({**selected, "disposition": disposition})
                if events:
                    shortlist.extend({"artifact": artifact, "fold": fold_index, **item} for item in fold_shortlist)
                else:
                    fold_shortlist = []
                folds.append({"artifact": artifact, "fold": fold_index, "membership": membership,
                    "profile_sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
                    "profile_repeatability": "byte_identical", "validation_events": len(events),
                    "validation_status": "evaluated" if events else "no_decode_events",
                    "observed_token_working_set_bytes": working_set, "budgets": budgets,
                    "replay": replay_digests, "cells": cells, "shortlist": fold_shortlist})
                if malformed is None and events:
                    malformed = malformed_cases(native, profile_path, events, root)
    if malformed is None or not all(malformed.values()):
        raise Phase10Error("one or more malformed replay cases were accepted")
    phase9_hashes = {item["phase9_passthrough_sha256"] for fold in folds for item in fold["replay"]}
    return {"schema_version": "phase10-offline-replay-v1", "project_head": args.project_head,
        "nested_head": args.nested_head, "phase2_archive_sha256": archive_sha256,
        "matrix": MATRIX, "matrix_sha256": sha256(MATRIX), "folds": folds, "shortlist": shortlist,
        "malformed_rejections": malformed, "native_python_event_agreement": True,
        "repeatability": "byte_identical", "shuffled_completion_invariant": True,
        "phase9_passthrough_unique_digests": len(phase9_hashes),
        "cache_admission_lifecycle_scope": "deferred_to_phase10.2"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--f16-storage-map", required=True)
    parser.add_argument("--mxfp4-storage-map", required=True)
    parser.add_argument("--f16-costs", required=True)
    parser.add_argument("--mxfp4-costs", required=True)
    parser.add_argument("--native-replay", required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        write_json(args.output, capture(args))
        print(Path(args.output))
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

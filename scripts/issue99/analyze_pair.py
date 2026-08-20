#!/usr/bin/env python3
"""Immediately scalarize one paired issue-99 trace and preserve bounded route data."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterator

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from protocol import (
    BRIDGE_CHECKPOINTS, BROAD_CHECKPOINTS, ROUTED_LAYERS, SELECTED_EXPERTS, atomic_json,
)


MAGIC = b"P13QTR1\n"
SIZE = struct.Struct("<I")
RECORD_HEADER = struct.Struct("<B3xIiiIQ")
KINDS = {1: "moe_output", 2: "hidden_state", 3: "logits"}


class PairError(ValueError):
    pass


@dataclass(frozen=True)
class Record:
    kind: str
    position: int
    layer: int
    target: int
    n_tokens: int
    values: np.ndarray

    @property
    def structural_key(self) -> tuple[str, int, int, int, int]:
        return self.kind, self.position, self.layer, self.n_tokens, int(self.values.size)


class TraceReader:
    def __init__(self, path: Path):
        self.path = path
        self.source: BinaryIO = path.open("rb")
        if self.source.read(len(MAGIC)) != MAGIC:
            raise PairError(f"{path}: invalid trace magic")
        encoded = self.source.read(SIZE.size)
        if len(encoded) != SIZE.size:
            raise PairError(f"{path}: truncated metadata size")
        length, = SIZE.unpack(encoded)
        try:
            self.metadata = json.loads(self.source.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PairError(f"{path}: invalid metadata") from exc
        if self.metadata.get("issue99_trace_contract") != "issue99-ephemeral-paired-tensor-trace-v1":
            raise PairError(f"{path}: not an issue-99 trace")

    def records(self) -> Iterator[Record]:
        while True:
            header = self.source.read(RECORD_HEADER.size)
            if not header:
                return
            if len(header) != RECORD_HEADER.size:
                raise PairError(f"{self.path}: truncated record header")
            kind, position, layer, target, n_tokens, count = RECORD_HEADER.unpack(header)
            if kind not in KINDS or count == 0 or count > 2**32:
                raise PairError(f"{self.path}: invalid record header")
            payload = self.source.read(count * 4)
            if len(payload) != count * 4:
                raise PairError(f"{self.path}: truncated record payload")
            values = np.frombuffer(payload, dtype="<f4")
            if not np.isfinite(values).all():
                raise PairError(f"{self.path}: non-finite tensor")
            yield Record(KINDS[kind], position, layer, target, n_tokens, values)

    def close(self) -> None:
        self.source.close()


def finite(value: float, label: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise PairError(f"non-finite {label}")
    return result


def vector_metrics(exact: np.ndarray, changed: np.ndarray) -> dict[str, float]:
    left = exact.astype(np.float64)
    right = changed.astype(np.float64)
    left_norm = math.sqrt(float(left @ left))
    right_norm = math.sqrt(float(right @ right))
    delta_norm = math.sqrt(float((right - left) @ (right - left)))
    if left_norm == 0:
        relative_l2 = 0.0 if delta_norm == 0 else math.inf
        norm_ratio = 1.0 if right_norm == 0 else math.inf
    else:
        relative_l2 = delta_norm / left_norm
        norm_ratio = right_norm / left_norm
    cosine = 1.0 if left_norm == right_norm == 0 else 0.0
    if left_norm and right_norm:
        cosine = min(1.0, max(-1.0, float(left @ right) / (left_norm * right_norm)))
    return {
        "relative_l2": finite(relative_l2, "relative L2"),
        "cosine_similarity": finite(cosine, "cosine"),
        "norm_ratio": finite(norm_ratio, "norm ratio"),
    }


def log_distribution(logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    values = logits.astype(np.float64)
    maximum = float(np.max(values))
    log_z = maximum + math.log(float(np.exp(values - maximum).sum()))
    log_p = values - log_z
    return log_p, np.exp(log_p)


def predictive_metrics(exact: Record, changed: Record, direct: bool, top_k: int = 5) -> dict[str, Any]:
    exact_log_p, exact_p = log_distribution(exact.values)
    changed_log_p, changed_p = log_distribution(changed.values)
    mixture = np.logaddexp(exact_log_p, changed_log_p) - math.log(2.0)
    kl = float(np.sum(exact_p * (exact_log_p - changed_log_p)))
    js = 0.5 * float(np.sum(exact_p * (exact_log_p - mixture))) + \
        0.5 * float(np.sum(changed_p * (changed_log_p - mixture)))
    k = min(top_k, exact.values.size)
    exact_top = np.argpartition(exact.values, -k)[-k:]
    changed_top = np.argpartition(changed.values, -k)[-k:]
    exact_target = exact.target
    if not 0 <= exact_target < exact.values.size:
        raise PairError("exact trace has invalid reference token")
    if direct and changed.target != exact_target:
        raise PairError("fixed-context traces do not share the reference token")
    exact_nll = -float(exact_log_p[exact_target])
    changed_exact_token_nll = -float(changed_log_p[exact_target])
    return {
        "kl_exact_to_changed": finite(kl, "KL"),
        "js_divergence": finite(js, "JS"),
        "exact_top1": int(np.argmax(exact.values)),
        "changed_top1": int(np.argmax(changed.values)),
        "top1_agreement": int(np.argmax(exact.values)) == int(np.argmax(changed.values)),
        "top5_overlap": len(set(map(int, exact_top)) & set(map(int, changed_top))) / k,
        "exact_reference_token": exact_target,
        "changed_accepted_token": changed.target,
        "exact_reference_nll": exact_nll if direct else None,
        "changed_reference_nll": changed_exact_token_nll if direct else None,
        "delta_reference_nll": changed_exact_token_nll - exact_nll if direct else None,
        "trajectory_exact_token_delta_nll": changed_exact_token_nll - exact_nll,
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def load_routes(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata: dict[str, Any] | None = None
    routes: list[dict[str, Any]] = []
    with path.open() as source:
        for line_number, line in enumerate(source, 1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PairError(f"{path}:{line_number}: invalid JSON") from exc
            if line_number == 1 and value.get("record_type") == "metadata":
                metadata = value["metadata"]
            elif value.get("record_type") == "route":
                routes.append(value)
            else:
                raise PairError(f"{path}:{line_number}: invalid record type")
    if metadata is None or not routes:
        raise PairError(f"{path}: missing route metadata or records")
    return metadata, routes


def route_key(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["sequence_position"]), int(row["layer"])


def validate_route_stream(
    rows: list[dict[str, Any]],
    *,
    exact: bool,
) -> tuple[dict[tuple[int, int], dict[str, Any]], int, list[int]]:
    by_key = {route_key(row): row for row in rows}
    if len(by_key) != len(rows):
        raise PairError("route stream contains duplicate keys")
    positions = sorted({position for position, _ in by_key})
    if not positions or positions != list(range(1, positions[-1] + 1)):
        raise PairError("route stream positions are empty, skipped, or non-contiguous")
    layers = sorted({layer for _, layer in by_key})
    if len(layers) != ROUTED_LAYERS:
        raise PairError(f"expected {ROUTED_LAYERS} routed layers, observed {len(layers)}")
    layer_set = set(layers)
    for position in positions:
        if {layer for observed_position, layer in by_key if observed_position == position} != layer_set:
            raise PairError(f"position {position} has incomplete route-layer coverage")
    for row in rows:
        selected = row.get("selected_experts", [])
        candidates = row.get("candidate_experts", [])
        scores = row.get("candidate_selection_scores", [])
        probabilities = row.get("candidate_probabilities", [])
        if len(selected) != SELECTED_EXPERTS or len(candidates) != 32 or \
                len(scores) != 32 or len(probabilities) != 32:
            raise PairError("route has the wrong selected/candidate width")
        if len(set(map(int, candidates))) != len(candidates):
            raise PairError("route candidate list contains duplicates")
        if len(set(map(int, selected))) != len(selected):
            raise PairError("route selection contains duplicates")
        if not all(math.isfinite(float(value)) for value in (*scores, *probabilities)):
            raise PairError("route contains non-finite scores")
        intrinsic = candidates[:SELECTED_EXPERTS]
        if exact and selected != intrinsic:
            raise PairError("EXACT route contains a substitution")
        if not exact and any(selected_expert not in candidates for selected_expert in selected):
            raise PairError("replacement is outside retained top-M")
        if not exact:
            for rank, selected_expert in enumerate(selected):
                if selected_expert == intrinsic[rank]:
                    continue
                replacement_rank = candidates.index(selected_expert)
                corrected = float(scores[rank]) - float(scores[replacement_rank])
                raw = float(probabilities[rank]) - float(probabilities[replacement_rank])
                if corrected < 0 or not math.isfinite(corrected) or not math.isfinite(raw):
                    raise PairError("invalid substitution regret")
    return by_key, positions[-1], layers


def load_core_membership(path: Path) -> dict[str, dict[int, set[int]]]:
    value = load_json(path)
    if value.get("schema_version") != "issue99-frozen-core-membership-v1" or value.get("status") != "pass":
        raise PairError("invalid frozen core membership")
    result = {}
    for gamma in ("1.0", "0.8"):
        result[gamma] = {
            int(layer): set(map(int, experts))
            for layer, experts in value["definitions"][gamma]["layers"].items()
        }
    return result


def pair_routes(
    exact_path: Path,
    changed_path: Path,
    identity: dict[str, Any],
    core: dict[str, dict[int, set[int]]],
    direct: bool = True,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, float]], dict[str, int]
]:
    exact_meta, exact_rows = load_routes(exact_path)
    changed_meta, changed_rows = load_routes(changed_path)
    for field in ("case_id", "capacity_bytes", "candidate_count", "selected_count"):
        if exact_meta.get(field) != changed_meta.get(field):
            raise PairError(f"route metadata mismatch: {field}")
    exact_by_key, exact_horizon, layers = validate_route_stream(exact_rows, exact=True)
    changed_by_key, changed_horizon, changed_layers = validate_route_stream(changed_rows, exact=False)
    if changed_layers != layers:
        raise PairError("paired route streams use different routed layers")
    if direct and set(exact_by_key) != set(changed_by_key):
        raise PairError("fixed-context paired route keys differ")
    common_horizon = min(exact_horizon, changed_horizon)
    common_keys = {
        (position, layer)
        for position in range(1, common_horizon + 1)
        for layer in layers
    }
    if not common_keys.issubset(exact_by_key) or not common_keys.issubset(changed_by_key):
        raise PairError("paired route common prefix is incomplete")
    ordinal = {layer: index for index, layer in enumerate(layers)}
    route_rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    token: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for key in sorted(common_keys):
        position, layer = key
        exact = exact_by_key[key]
        changed = changed_by_key[key]
        exact_intrinsic = exact["candidate_experts"][:SELECTED_EXPERTS]
        changed_intrinsic = changed["candidate_experts"][:SELECTED_EXPERTS]
        swaps = []
        for rank, selected in enumerate(changed["selected_experts"]):
            if selected == changed_intrinsic[rank]:
                continue
            try:
                replacement_rank = changed["candidate_experts"].index(selected)
            except ValueError as exc:
                raise PairError("replacement is outside retained top-M") from exc
            corrected = float(changed["candidate_selection_scores"][rank]) - \
                float(changed["candidate_selection_scores"][replacement_rank])
            raw = float(changed["candidate_probabilities"][rank]) - \
                float(changed["candidate_probabilities"][replacement_rank])
            if corrected < 0 or not math.isfinite(corrected) or not math.isfinite(raw):
                raise PairError("invalid substitution regret")
            event_id = (f"{identity['case_id']}|{identity['cache_regime']}|"
                        f"{identity['changed_intervention']}|{identity['policy']}|"
                        f"p{position:04d}|l{ordinal[layer]:02d}|s{rank:02d}")
            event = {
                **identity,
                "event_id": event_id,
                "sequence_position": position,
                "routed_layer": layer,
                "routed_layer_ordinal": ordinal[layer],
                "normalized_depth": ordinal[layer] / (ROUTED_LAYERS - 1),
                "depth_third": ("early", "middle", "late")[min(2, 3 * ordinal[layer] // ROUTED_LAYERS)],
                "selected_slot": rank,
                "original_expert": int(changed_intrinsic[rank]),
                "replacement_expert": int(selected),
                "replacement_candidate_rank": replacement_rank,
                "corrected_regret": corrected,
                "raw_probability_regret_signed": raw,
                "raw_probability_regret_absolute": abs(raw),
            }
            for gamma in ("1.0", "0.8"):
                original_class = "core" if int(changed_intrinsic[rank]) in core[gamma][layer] else "peripheral"
                replacement_class = "core" if int(selected) in core[gamma][layer] else "peripheral"
                suffix = gamma.replace(".", "_")
                event[f"original_class_gamma_{suffix}"] = original_class
                event[f"replacement_class_gamma_{suffix}"] = replacement_class
                event[f"transition_gamma_{suffix}"] = f"{original_class}_to_{replacement_class}"
            events.append(event)
            swaps.append(event)
        induced = sum(a != b for a, b in zip(exact_intrinsic, changed_intrinsic))
        final = sum(a != b for a, b in zip(exact["selected_experts"], changed["selected_experts"]))
        route_rows.append({
            **identity,
            "route_id": (f"{identity['case_id']}|{identity['cache_regime']}|"
                         f"{identity['changed_intervention']}|{identity['policy']}|"
                         f"p{position:04d}|l{ordinal[layer]:02d}"),
            "sequence_position": position,
            "routed_layer": layer,
            "routed_layer_ordinal": ordinal[layer],
            "normalized_depth": ordinal[layer] / (ROUTED_LAYERS - 1),
            "exact_selected_experts": exact["selected_experts"],
            "exact_selected_weights": exact["selected_weights"],
            "changed_selected_experts": changed["selected_experts"],
            "changed_selected_weights": changed["selected_weights"],
            "changed_candidate_experts": changed["candidate_experts"],
            "changed_candidate_selection_scores": changed["candidate_selection_scores"],
            "changed_candidate_probabilities": changed["candidate_probabilities"],
            "intentional_swaps": len(swaps),
            "induced_changed_slots": induced,
            "final_changed_slots": final,
            "corrected_regret_sum": sum(item["corrected_regret"] for item in swaps),
            "raw_regret_signed_sum": sum(item["raw_probability_regret_signed"] for item in swaps),
        })
        aggregate = token[position]
        aggregate["routed_layers"] += 1
        aggregate["perturbed_layers"] += bool(swaps)
        aggregate["intentional_swaps"] += len(swaps)
        aggregate["induced_changed_slots"] += induced
        aggregate["final_changed_slots"] += final
        aggregate["corrected_regret"] += sum(item["corrected_regret"] for item in swaps)
        aggregate["raw_regret_signed"] += sum(item["raw_probability_regret_signed"] for item in swaps)
        aggregate["raw_regret_absolute"] += sum(item["raw_probability_regret_absolute"] for item in swaps)
        aggregate["max_corrected_regret"] = max(
            aggregate["max_corrected_regret"], *(item["corrected_regret"] for item in swaps), 0.0)
    return route_rows, events, token, {
        "exact": exact_horizon,
        "changed": changed_horizon,
        "common": common_horizon,
    }


def trace_groups(reader: TraceReader) -> Iterator[tuple[int, list[Record]]]:
    expected_position = 1
    for position, rows in itertools.groupby(reader.records(), key=lambda row: row.position):
        records = list(rows)
        if position != expected_position:
            raise PairError("trace positions are skipped, repeated, or non-contiguous")
        expected_position += 1
        structural_keys = [row.structural_key for row in records]
        if len(set(structural_keys)) != len(structural_keys):
            raise PairError(f"position {position} contains duplicate trace records")
        moe = [row for row in records if row.kind == "moe_output"]
        hidden = [row for row in records if row.kind == "hidden_state"]
        moe_layers = {row.layer for row in moe}
        hidden_layers = {row.layer for row in hidden}
        logits = [row for row in records if row.kind == "logits"]
        if len(moe) != ROUTED_LAYERS or len(hidden) != ROUTED_LAYERS or \
                len(moe_layers) != ROUTED_LAYERS or moe_layers != hidden_layers or len(logits) != 1:
            raise PairError(f"position {position} has incomplete trace coverage")
        if any(row.n_tokens != 1 for row in records) or logits[0].layer != -1:
            raise PairError(f"position {position} has an invalid trace record shape")
        yield position, records


def next_group(groups: Iterator[tuple[int, list[Record]]]) -> tuple[int, list[Record]] | None:
    return next(groups, None)


def paired_trace_metrics(
    exact_path: Path,
    changed_path: Path,
    direct: bool,
) -> tuple[
    dict[int, dict[str, Any]], dict[tuple[int, int], dict[str, Any]], dict[str, int]
]:
    exact = TraceReader(exact_path)
    changed = TraceReader(changed_path)
    token: dict[int, dict[str, Any]] = defaultdict(lambda: {
        "moe_relative_l2": [], "moe_cosine": [], "hidden_relative_l2": [], "hidden_cosine": [],
    })
    layer: dict[tuple[int, int], dict[str, Any]] = {}
    try:
        if exact.metadata.get("case_id") != changed.metadata.get("case_id"):
            raise PairError("trace case identity mismatch")
        exact_groups = trace_groups(exact)
        changed_groups = trace_groups(changed)
        left_group = next_group(exact_groups)
        right_group = next_group(changed_groups)
        exact_horizon = changed_horizon = 0
        while left_group is not None and right_group is not None:
            left_position, left_rows = left_group
            right_position, right_rows = right_group
            if left_position != right_position:
                raise PairError("trace common-prefix positions differ")
            if len(left_rows) != len(right_rows):
                raise PairError("trace record sequence mismatch")
            for left, right in zip(left_rows, right_rows):
                if left.structural_key != right.structural_key:
                    raise PairError("trace record sequence mismatch")
                if left.kind == "logits":
                    token[left.position].update(predictive_metrics(left, right, direct))
                    continue
                metric = vector_metrics(left.values, right.values)
                prefix = "moe" if left.kind == "moe_output" else "hidden"
                token[left.position][f"{prefix}_relative_l2"].append(metric["relative_l2"])
                token[left.position][f"{prefix}_cosine"].append(metric["cosine_similarity"])
                row = layer.setdefault((left.position, left.layer), {})
                row.update({f"{prefix}_{name}": value for name, value in metric.items()})
            exact_horizon = left_position
            changed_horizon = right_position
            left_group = next_group(exact_groups)
            right_group = next_group(changed_groups)
        while left_group is not None:
            exact_horizon = left_group[0]
            left_group = next_group(exact_groups)
        while right_group is not None:
            changed_horizon = right_group[0]
            right_group = next_group(changed_groups)
        if direct and exact_horizon != changed_horizon:
            raise PairError("fixed-context trace horizons differ")
    finally:
        exact.close()
        changed.close()
    for position, values in token.items():
        for prefix in ("moe", "hidden"):
            distances = values.pop(f"{prefix}_relative_l2")
            cosines = values.pop(f"{prefix}_cosine")
            if len(distances) != ROUTED_LAYERS or len(cosines) != ROUTED_LAYERS:
                raise PairError(f"position {position} has incomplete {prefix} coverage")
            values[f"{prefix}_relative_l2_mean"] = float(np.mean(distances))
            values[f"{prefix}_relative_l2_max"] = float(np.max(distances))
            values[f"{prefix}_cosine_mean"] = float(np.mean(cosines))
    return token, layer, {
        "exact": exact_horizon,
        "changed": changed_horizon,
        "common": min(exact_horizon, changed_horizon),
    }


def write_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise PairError(f"refusing empty Parquet dataset: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path, compression="zstd", version="2.6")
    check = pq.read_table(path)
    if check.num_rows != len(rows):
        raise PairError(f"Parquet validation failed: {path}")


def validate_result_stream(result: dict[str, Any], label: str) -> tuple[int, int, dict[int, dict[str, Any]]]:
    reference = result.get("reference", {})
    achieved = int(reference.get("achieved_horizon", 0))
    horizon_limit = int(reference.get("horizon_limit", 0))
    targets = reference.get("target_ids", [])
    telemetry_rows = result.get("measured", {}).get("token_telemetry", [])
    telemetry = {int(row["sequence_position"]): row for row in telemetry_rows}
    expected_positions = list(range(1, achieved + 1))
    if achieved <= 0 or achieved > horizon_limit or len(targets) != achieved:
        raise PairError(f"{label} result has invalid achieved horizon")
    if len(telemetry) != len(telemetry_rows) or sorted(telemetry) != expected_positions:
        raise PairError(f"{label} result has invalid token telemetry coverage")
    if int(result.get("measured", {}).get("decode_forwards", -1)) != achieved:
        raise PairError(f"{label} result decode count differs from achieved horizon")
    return achieved, horizon_limit, telemetry


def horizon_evidence(
    exact_result: dict[str, Any],
    changed_result: dict[str, Any],
    exact_horizon: int,
    changed_horizon: int,
    horizon_limit: int,
) -> dict[str, Any]:
    checkpoints = BRIDGE_CHECKPOINTS if horizon_limit > BROAD_CHECKPOINTS[-1] else BROAD_CHECKPOINTS
    common_horizon = min(exact_horizon, changed_horizon)
    unavailable = lambda achieved: [checkpoint for checkpoint in checkpoints if checkpoint > achieved]
    return {
        "horizon_limit": horizon_limit,
        "exact_achieved_horizon": exact_horizon,
        "changed_achieved_horizon": changed_horizon,
        "paired_achieved_horizon": common_horizon,
        "exact_eog_position": int(exact_result.get("generation_phase", {}).get("eog_position", -1)),
        "changed_eog_position": int(changed_result.get("generation_phase", {}).get("eog_position", -1)),
        "unavailable_tail_checkpoints": {
            "exact": unavailable(exact_horizon),
            "changed": unavailable(changed_horizon),
            "paired": unavailable(common_horizon),
        },
    }


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    exact_result = load_json(args.exact_result)
    changed_result = load_json(args.changed_result)
    if exact_result.get("status") != "pass" or changed_result.get("status") != "pass":
        raise PairError("cell result did not pass")
    case_id = exact_result["case"]["id"]
    if case_id != changed_result["case"]["id"]:
        raise PairError("cell cases differ")
    direct = args.evidence_class in ("DIRECT_FIXED_CONTEXT", "CAPACITY_FIXED_CONTEXT")
    exact_horizon, exact_limit, exact_telemetry = validate_result_stream(exact_result, "EXACT")
    changed_horizon, changed_limit, changed_telemetry = validate_result_stream(changed_result, "changed")
    if exact_limit != changed_limit:
        raise PairError("paired horizon limits differ")
    if direct and exact_horizon != changed_horizon:
        raise PairError("fixed-context achieved horizons differ")
    if direct and exact_result["reference"]["target_ids"] != changed_result["reference"]["target_ids"]:
        raise PairError("fixed-context target sequence differs")
    horizon = horizon_evidence(
        exact_result, changed_result, exact_horizon, changed_horizon, exact_limit)
    identity = {
        "case_id": case_id,
        "semantic_family": exact_result["case"]["semantic_family"],
        "policy": changed_result["policy"],
        "evidence_class": args.evidence_class,
        "changed_intervention": changed_result["intervention"],
        "cache_regime": args.cache_regime,
        "capacity_bytes": int(changed_result["preflight"]["initial_cold"]["actual_bytes"]),
        "reference_identity": args.reference_identity,
        "exact_achieved_horizon": exact_horizon,
        "changed_achieved_horizon": changed_horizon,
        "paired_achieved_horizon": horizon["paired_achieved_horizon"],
    }
    core = load_core_membership(args.core_membership)
    route_rows, events, route_token, route_coverage = pair_routes(
        args.exact_routes, args.changed_routes, identity, core, direct=direct)
    trace_token, layer_metrics, trace_coverage = paired_trace_metrics(
        args.exact_trace, args.changed_trace, direct)
    expected_coverage = {
        "exact": exact_horizon,
        "changed": changed_horizon,
        "common": horizon["paired_achieved_horizon"],
    }
    if route_coverage != expected_coverage or trace_coverage != expected_coverage:
        raise PairError("result, route, and trace horizons differ")
    if sorted(exact_telemetry) != list(range(1, exact_horizon + 1)):
        raise PairError("EXACT telemetry is incomplete")
    changed_telemetry = {
        position: changed_telemetry[position]
        for position in range(1, horizon["paired_achieved_horizon"] + 1)
    }
    positions = sorted(trace_token)
    if positions != sorted(route_token) or positions != sorted(changed_telemetry):
        raise PairError("trace, route, and telemetry positions differ")
    event_by_position: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        event_by_position[event["sequence_position"]].append(event)
    cumulative_corrected = cumulative_raw = cumulative_swaps = cumulative_perturbed = 0.0
    cumulative_max_corrected = 0.0
    cumulative_first_depth: float | None = None
    cumulative_last_depth: float | None = None
    cumulative_depth_weight = 0.0
    cumulative_third_counts = {"early": 0, "middle": 0, "late": 0}
    cumulative_third_corrected = {"early": 0.0, "middle": 0.0, "late": 0.0}
    cumulative_third_raw = {"early": 0.0, "middle": 0.0, "late": 0.0}
    token_rows: list[dict[str, Any]] = []
    for position in positions:
        routing = route_token[position]
        position_events = event_by_position[position]
        cumulative_corrected += routing["corrected_regret"]
        cumulative_raw += routing["raw_regret_signed"]
        cumulative_swaps += routing["intentional_swaps"]
        cumulative_perturbed += routing["perturbed_layers"]
        cumulative_max_corrected = max(cumulative_max_corrected, routing["max_corrected_regret"])
        depths = [row["normalized_depth"] for row in position_events]
        regret_total = sum(row["corrected_regret"] for row in position_events)
        weighted_depth = sum(row["corrected_regret"] * row["normalized_depth"] for row in position_events)
        cumulative_depth_weight += weighted_depth
        if depths:
            cumulative_first_depth = min(depths) if cumulative_first_depth is None else min(cumulative_first_depth, min(depths))
            cumulative_last_depth = max(depths) if cumulative_last_depth is None else max(cumulative_last_depth, max(depths))
        for event in position_events:
            third = event["depth_third"]
            cumulative_third_counts[third] += 1
            cumulative_third_corrected[third] += event["corrected_regret"]
            cumulative_third_raw[third] += event["raw_probability_regret_signed"]
        telemetry = changed_telemetry[position]
        token_rows.append({
            **identity,
            "sequence_position": position,
            "generation_phase": telemetry["target_generation_phase"],
            **trace_token[position],
            "token_corrected_regret_sum": routing["corrected_regret"],
            "token_corrected_regret_mean": routing["corrected_regret"] / routing["intentional_swaps"]
                if routing["intentional_swaps"] else 0.0,
            "token_corrected_regret_max": routing["max_corrected_regret"],
            "token_raw_regret_signed_sum": routing["raw_regret_signed"],
            "token_raw_regret_absolute_sum": routing["raw_regret_absolute"],
            "cumulative_corrected_regret": cumulative_corrected,
            "cumulative_mean_corrected_regret_per_swap": cumulative_corrected / cumulative_swaps
                if cumulative_swaps else 0.0,
            "cumulative_max_corrected_regret_per_swap": cumulative_max_corrected,
            "cumulative_raw_regret_signed": cumulative_raw,
            "cumulative_intentional_swaps": int(cumulative_swaps),
            "changed_slot_fraction": cumulative_swaps / (position * ROUTED_LAYERS * SELECTED_EXPERTS),
            "perturbed_layer_fraction": cumulative_perturbed / (position * ROUTED_LAYERS),
            "token_first_perturbed_normalized_depth": min(depths) if depths else None,
            "token_last_perturbed_normalized_depth": max(depths) if depths else None,
            "token_mean_perturbed_normalized_depth": float(np.mean(depths)) if depths else None,
            "token_regret_weighted_mean_normalized_depth": weighted_depth / regret_total if regret_total else None,
            "cumulative_first_perturbed_normalized_depth": cumulative_first_depth,
            "cumulative_last_perturbed_normalized_depth": cumulative_last_depth,
            "cumulative_regret_weighted_mean_normalized_depth":
                cumulative_depth_weight / cumulative_corrected if cumulative_corrected else None,
            **{
                f"cumulative_substitution_fraction_{third}":
                    cumulative_third_counts[third] / cumulative_swaps if cumulative_swaps else 0.0
                for third in ("early", "middle", "late")
            },
            **{
                f"cumulative_corrected_regret_fraction_{third}":
                    cumulative_third_corrected[third] / cumulative_corrected if cumulative_corrected else 0.0
                for third in ("early", "middle", "late")
            },
            **{
                f"cumulative_raw_regret_fraction_{third}":
                    cumulative_third_raw[third] / cumulative_raw if cumulative_raw else 0.0
                for third in ("early", "middle", "late")
            },
            "cumulative_cache_hits": telemetry["cold_delta"]["hits"],
            "cumulative_cache_misses": telemetry["cold_delta"]["misses"],
            "cumulative_backing_loads": telemetry["storage_delta"]["backing_loads"],
            "cumulative_backing_bytes": telemetry["storage_delta"]["backing_bytes"],
        })
    route_by_key = {(row["sequence_position"], row["routed_layer"]): row for row in route_rows}
    layer_rows = []
    for key, metrics in sorted(layer_metrics.items()):
        route = route_by_key[key]
        layer_rows.append({
            **identity,
            "sequence_position": key[0],
            "routed_layer": key[1],
            "routed_layer_ordinal": route["routed_layer_ordinal"],
            "normalized_depth": route["normalized_depth"],
            "intentional_swaps": route["intentional_swaps"],
            "induced_changed_slots": route["induced_changed_slots"],
            "final_changed_slots": route["final_changed_slots"],
            "corrected_regret_sum": route["corrected_regret_sum"],
            "raw_regret_signed_sum": route["raw_regret_signed_sum"],
            **metrics,
        })
    stem = args.output_dir / args.pair_id
    outputs = {
        "tokens": stem.with_suffix(".tokens.parquet"),
        "layers": stem.with_suffix(".layers.parquet"),
        "routes": stem.with_suffix(".routes.parquet"),
        "events": stem.with_suffix(".events.parquet"),
    }
    write_parquet(outputs["tokens"], token_rows)
    write_parquet(outputs["layers"], layer_rows)
    write_parquet(outputs["routes"], route_rows)
    # An exact/no-change cell can legitimately have no substitution events; changed-policy pairs cannot.
    if events:
        write_parquet(outputs["events"], events)
    summary = {
        "schema_version": "issue99-pair-summary-v1",
        "status": "pass",
        "pair_id": args.pair_id,
        "identity": identity,
        "rows": {
            "tokens": len(token_rows), "layers": len(layer_rows),
            "routes": len(route_rows), "events": len(events),
        },
        "outputs": {name: str(path) for name, path in outputs.items() if path.exists()},
        "direct_fixed_context": direct,
        "horizons": horizon,
        "common_prefix_tokens": next((position - 1 for position in positions
                                      if trace_token[position]["exact_reference_token"] !=
                                      trace_token[position]["changed_accepted_token"]), len(positions)),
        "terminal": token_rows[-1],
    }
    atomic_json(stem.with_suffix(".summary.json"), summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-id", required=True)
    parser.add_argument("--exact-result", type=Path, required=True)
    parser.add_argument("--changed-result", type=Path, required=True)
    parser.add_argument("--exact-trace", type=Path, required=True)
    parser.add_argument("--changed-trace", type=Path, required=True)
    parser.add_argument("--exact-routes", type=Path, required=True)
    parser.add_argument("--changed-routes", type=Path, required=True)
    parser.add_argument("--evidence-class", choices=(
        "DIRECT_FIXED_CONTEXT", "FREE_TRAJECTORY", "CAPACITY_FIXED_CONTEXT"), required=True)
    parser.add_argument("--cache-regime", choices=("high-cache", "96-gib-bridge"), required=True)
    parser.add_argument("--reference-identity", required=True)
    parser.add_argument("--core-membership", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    summary = analyze(args)
    print(f"ISSUE99_PAIR status=pass pair={summary['pair_id']} events={summary['rows']['events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

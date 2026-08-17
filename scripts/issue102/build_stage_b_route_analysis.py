#!/usr/bin/env python3
"""Build deterministic issue-102 Stage-B/B2 route fingerprints and committee audit."""

from __future__ import annotations

import argparse
from array import array
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any, Iterable


ROUTED_LAYERS = 92
EXPERTS_PER_LAYER = 896
SELECTED_EXPERTS = 16
DECODE_FORWARDS = 64
PHASES = ("PREFILL", "DECODE")
TOP_SET_SIZES = (16, 32, 64, 128, 242)
MASS_LEVELS = (0.50, 0.75, 0.90, 0.95)
GAMMAS = (0.50, 0.75, 0.80, 0.90, 1.00)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--expected-progress-sha256", required=True)
    parser.add_argument("--selection", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    result: dict[str, Any] = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if resolved.suffix == ".json":
        with resolved.open() as stream:
            document = json.load(stream)
        if "schema_version" in document:
            result["schema_version"] = document["schema_version"]
    return result


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)
    return identity(path)


def quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile of empty sequence")
    position = probability * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    fraction = position - low
    return float(ordered[low] * (1.0 - fraction) + ordered[high] * fraction)


def distribution(values: Iterable[float]) -> dict[str, float | int]:
    materialized = list(values)
    if not materialized:
        return {"count": 0}
    return {
        "count": len(materialized),
        "min": min(materialized),
        "p10": quantile(materialized, 0.10),
        "median": statistics.median(materialized),
        "mean": statistics.fmean(materialized),
        "p90": quantile(materialized, 0.90),
        "max": max(materialized),
    }


def empty_matrix() -> list[array]:
    return [array("I", [0]) * EXPERTS_PER_LAYER for _ in range(ROUTED_LAYERS)]


def entropy_bits(row: array) -> float:
    total = sum(row)
    if total == 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in row if count)


def support(row: array) -> set[int]:
    return {expert for expert, count in enumerate(row) if count}


def ranks(row: array) -> list[int]:
    order = sorted(range(EXPERTS_PER_LAYER), key=lambda expert: (-row[expert], expert))
    result = [0] * EXPERTS_PER_LAYER
    for rank, expert in enumerate(order, 1):
        result[expert] = rank
    return result


def top_set(row: array, count: int) -> set[int] | None:
    nonzero = [expert for expert, value in enumerate(row) if value]
    if len(nonzero) < count:
        return None
    nonzero.sort(key=lambda expert: (-row[expert], expert))
    return set(nonzero[:count])


def cumulative_mass_set_size(row: array, fraction: float) -> int:
    total = sum(row)
    if total == 0:
        return 0
    target = total * fraction
    accumulated = 0
    for index, count in enumerate(sorted((value for value in row if value), reverse=True), 1):
        accumulated += count
        if accumulated >= target:
            return index
    raise AssertionError("cumulative mass target was not reached")


def cosine(left: list[array], right: list[array]) -> float:
    dot = 0
    left_norm = 0
    right_norm = 0
    for left_row, right_row in zip(left, right):
        for lhs, rhs in zip(left_row, right_row):
            dot += lhs * rhs
            left_norm += lhs * lhs
            right_norm += rhs * rhs
    denominator = math.sqrt(left_norm * right_norm)
    return dot / denominator if denominator else 0.0


def weighted_js_bits(left: list[array], right: list[array]) -> float:
    left_total = sum(sum(row) for row in left)
    right_total = sum(sum(row) for row in right)
    if left_total == 0 or right_total == 0:
        return 0.0
    divergence = 0.0
    for left_row, right_row in zip(left, right):
        for lhs, rhs in zip(left_row, right_row):
            p = lhs / left_total
            q = rhs / right_total
            midpoint = (p + q) / 2.0
            if p:
                divergence += 0.5 * p * math.log2(p / midpoint)
            if q:
                divergence += 0.5 * q * math.log2(q / midpoint)
    return divergence


def layerwise_jaccard(left: list[array], right: list[array]) -> list[float]:
    values = []
    for left_row, right_row in zip(left, right):
        lhs = support(left_row)
        rhs = support(right_row)
        union = lhs | rhs
        values.append(len(lhs & rhs) / len(union) if union else 1.0)
    return values


def top_set_grid(left: list[array], right: list[array]) -> list[dict[str, Any]]:
    rows = []
    for count in TOP_SET_SIZES:
        overlaps = []
        for left_row, right_row in zip(left, right):
            lhs = top_set(left_row, count)
            rhs = top_set(right_row, count)
            if lhs is not None and rhs is not None:
                overlaps.append(len(lhs & rhs) / count)
        rows.append({
            "top_n": count,
            "eligible_layer_count": len(overlaps),
            "random_independent_set_reference": count / EXPERTS_PER_LAYER,
            "overlap_fraction": distribution(overlaps),
            "observed_over_reference_median": (
                statistics.median(overlaps) / (count / EXPERTS_PER_LAYER) if overlaps else None
            ),
        })
    return rows


def comparison(left: dict[str, Any], right: dict[str, Any], phase: str) -> dict[str, Any]:
    left_matrix = left["matrices"][phase]
    right_matrix = right["matrices"][phase]
    jaccards = layerwise_jaccard(left_matrix, right_matrix)
    return {
        "left_case_id": left["case_id"],
        "right_case_id": right["case_id"],
        "left_family": left["semantic_family"],
        "right_family": right["semantic_family"],
        "phase": phase,
        "cosine_similarity": cosine(left_matrix, right_matrix),
        "weighted_jensen_shannon_divergence_bits": weighted_js_bits(left_matrix, right_matrix),
        "layerwise_support_jaccard": distribution(jaccards),
        "layerwise_support_jaccard_values": jaccards,
        "top_set_overlap_sensitivity": top_set_grid(left_matrix, right_matrix),
    }


def profile_summary(matrix: list[array]) -> dict[str, Any]:
    layer_rows = []
    for layer, counts in enumerate(matrix, 1):
        entropy = entropy_bits(counts)
        ordered = sorted(
            ((expert, count) for expert, count in enumerate(counts) if count),
            key=lambda item: (-item[1], item[0]),
        )
        layer_rows.append({
            "layer": layer,
            "selected_occurrences": sum(counts),
            "distinct_experts": len(ordered),
            "entropy_bits": entropy,
            "effective_expert_count": 2.0**entropy,
            "top_16_by_frequency": [
                {"expert": expert, "count": count} for expert, count in ordered[:16]
            ],
            "cumulative_mass_set_sizes": {
                str(level): cumulative_mass_set_size(counts, level) for level in MASS_LEVELS
            },
        })
    return {
        "selected_occurrences": sum(row["selected_occurrences"] for row in layer_rows),
        "whole_run_distinct_expert_keys": sum(row["distinct_experts"] for row in layer_rows),
        "per_layer_distinct_experts": distribution(row["distinct_experts"] for row in layer_rows),
        "per_layer_entropy_bits": distribution(row["entropy_bits"] for row in layer_rows),
        "per_layer_effective_expert_count": distribution(
            row["effective_expert_count"] for row in layer_rows
        ),
        "layers": layer_rows,
    }


def load_profiles(progress: dict[str, Any], selection: dict[str, Any]) -> list[dict[str, Any]]:
    selection_rows = {
        row["case_id"]: row
        for row in selection["stage_b"]["representatives"] + selection["stage_b2"]["endpoints"]
    }
    profiles = []
    for capture in progress["captures"]:
        result_identity = capture.get("result") or capture.get("artifacts", {}).get("result")
        if not result_identity:
            raise ValueError(f"capture lacks result identity: {capture['case_id']}")
        result_path = pathlib.Path(result_identity["path"]).resolve(strict=True)
        if result_path.stat().st_size != result_identity["bytes"]:
            raise ValueError(f"result size changed: {capture['case_id']}")
        with result_path.open() as stream:
            result = json.load(stream)
        if (
            result["status"] != "pass"
            or result["observer"]["provenance"] != "MEASURED_OBSERVER"
            or result["observer"]["performance_evidence"] is not False
            or result["case"]["id"] != capture["case_id"]
        ):
            raise ValueError(f"accepted observer result identity changed: {capture['case_id']}")
        matrices = {phase: empty_matrix() for phase in PHASES}
        phase_records = {phase: 0 for phase in PHASES}
        for record in result["observer"]["records"]:
            phase = record["phase"]
            layer = record["layer"] - 1
            if phase not in matrices or not 0 <= layer < ROUTED_LAYERS:
                raise ValueError(f"invalid observer phase/layer: {capture['case_id']}")
            phase_records[phase] += 1
            row = matrices[phase][layer]
            for expert in record["selected_experts"]:
                row[expert] += 1
        expected_prefill = capture["prompt_tokens"] * ROUTED_LAYERS
        expected_decode = DECODE_FORWARDS * ROUTED_LAYERS
        if phase_records != {"PREFILL": expected_prefill, "DECODE": expected_decode}:
            raise ValueError(f"observer record phase counts changed: {capture['case_id']}")
        selected = selection_rows[capture["case_id"]]
        profiles.append({
            "ordinal": capture["ordinal"],
            "case_id": capture["case_id"],
            "semantic_family": selected["semantic_family"],
            "length_level": selected["length_level"],
            "prompt_tokens": capture["prompt_tokens"],
            "selection_role": capture["selection_role"],
            "result": result_identity,
            "selected_route_reference": {
                "json_pointer": "/observer/records",
                "decode_filter": "phase == DECODE",
                "selected_field": "selected_experts",
            },
            "matrices": matrices,
        })
    profiles.sort(key=lambda row: row["ordinal"])
    if len(profiles) != 44 or len({row["case_id"] for row in profiles}) != 44:
        raise ValueError("observer profile set is not the frozen 44-case set")
    return profiles


def build_fingerprints(profiles: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for profile in profiles:
        row = {key: value for key, value in profile.items() if key != "matrices"}
        row["phases"] = {}
        for phase in PHASES:
            summary = profile_summary(profile["matrices"][phase])
            summary["selected_frequency_matrix_92x896"] = [
                list(layer) for layer in profile["matrices"][phase]
            ]
            row["phases"][phase] = summary
        rows.append(row)
    return {
        "schema_version": "phase13-6pg-family-route-fingerprints-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "MEASURED_OBSERVER"],
        "performance_evidence": False,
        "inputs": inputs,
        "normalization": (
            "Each prompt/phase is retained separately. Family comparisons use one frozen representative "
            "per family and never weight a family by prompt length."
        ),
        "matrix_semantics": {
            "shape": [ROUTED_LAYERS, EXPERTS_PER_LAYER],
            "cell": "selected top-16 occurrence count",
            "decode_expected_row_sum": DECODE_FORWARDS * SELECTED_EXPERTS,
            "expert_key": "(routed_layer_1_based, expert_id_0_based)",
        },
        "profiles": rows,
        "disposition": "STAGE_B_B2_ROUTE_FINGERPRINTS_COMPLETE",
    }


def build_overlaps(
    profiles: list[dict[str, Any]], selection: dict[str, Any], inputs: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_id = {row["case_id"]: row for row in profiles}
    representative_ids = sorted(row["case_id"] for row in selection["stage_b"]["representatives"])
    representatives = [by_id[case_id] for case_id in representative_ids]
    representative_pairs = []
    for left_index, left in enumerate(representatives):
        for right in representatives[left_index + 1:]:
            representative_pairs.append(comparison(left, right, "DECODE"))
    endpoints_by_family: dict[str, list[dict[str, Any]]] = {}
    for row in selection["stage_b2"]["endpoints"]:
        endpoints_by_family.setdefault(row["semantic_family"], []).append(by_id[row["case_id"]])
    within = []
    b1 = []
    b8 = []
    for family, rows in sorted(endpoints_by_family.items()):
        ordered = sorted(rows, key=lambda row: row["length_level"])
        if [row["length_level"] for row in ordered] != [1, 8]:
            raise ValueError(f"family endpoint set changed: {family}")
        within.append(comparison(ordered[0], ordered[1], "DECODE"))
        b1.append(ordered[0])
        b8.append(ordered[1])
    between_b1 = []
    between_b8 = []
    for rows, output in ((b1, between_b1), (b8, between_b8)):
        for left_index, left in enumerate(rows):
            for right in rows[left_index + 1:]:
                output.append(comparison(left, right, "DECODE"))

    def comparison_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(rows),
            "cosine_similarity": distribution(row["cosine_similarity"] for row in rows),
            "weighted_jensen_shannon_divergence_bits": distribution(
                row["weighted_jensen_shannon_divergence_bits"] for row in rows
            ),
            "mean_layerwise_support_jaccard": distribution(
                row["layerwise_support_jaccard"]["mean"] for row in rows
            ),
        }

    overlap = {
        "schema_version": "phase13-6pg-family-overlap-matrix-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "MEASURED_OBSERVER"],
        "performance_evidence": False,
        "inputs": inputs,
        "representative_pair_count": len(representative_pairs),
        "representative_decode_pairs": representative_pairs,
        "representative_summary": comparison_summary(representative_pairs),
        "disposition": "STAGE_B_ROUTE_OVERLAP_COMPLETE",
    }
    endpoint = {
        "schema_version": "phase13-6pg-stage-b2-family-length-route-endpoints-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "MEASURED_OBSERVER"],
        "performance_evidence": False,
        "inputs": inputs,
        "within_family_b1_b8": within,
        "between_family_b1": between_b1,
        "between_family_b8": between_b8,
        "comparison_summary": {
            "within_family_b1_b8": comparison_summary(within),
            "between_family_b1": comparison_summary(between_b1),
            "between_family_b8": comparison_summary(between_b8),
        },
        "endpoint_limit": (
            "Two prompts are endpoint sensitivity evidence and do not fully characterize a family."
        ),
        "disposition": "STAGE_B2_ENDPOINT_ROUTE_ANALYSIS_COMPLETE",
    }
    return overlap, endpoint


def build_committee(
    profiles: list[dict[str, Any]], selection: dict[str, Any], inputs: dict[str, Any]
) -> dict[str, Any]:
    by_id = {row["case_id"]: row for row in profiles}
    representatives = [
        by_id[row["case_id"]] for row in sorted(
            selection["stage_b"]["representatives"], key=lambda row: row["case_id"]
        )
    ]
    phase_outputs: dict[str, Any] = {}
    for phase in PHASES:
        rank_matrices = [
            [ranks(layer) for layer in profile["matrices"][phase]] for profile in representatives
        ]
        gamma_rows = []
        for gamma in GAMMAS:
            threshold = math.ceil(gamma * len(representatives))
            layers = []
            total_mass = 0
            core_mass = 0
            all_core_keys = []
            for layer in range(ROUTED_LAYERS):
                prevalence = [
                    sum(profile["matrices"][phase][layer][expert] > 0 for profile in representatives)
                    for expert in range(EXPERTS_PER_LAYER)
                ]
                core = [expert for expert, count in enumerate(prevalence) if count >= threshold]
                expert_rows = []
                for expert in core:
                    expert_ranks = [matrix[layer][expert] for matrix in rank_matrices]
                    expert_rows.append({
                        "expert": expert,
                        "family_prevalence_count": prevalence[expert],
                        "family_prevalence_fraction": prevalence[expert] / len(representatives),
                        "mean_rank": statistics.fmean(expert_ranks),
                        "median_rank": statistics.median(expert_ranks),
                        "rank_population_stddev": statistics.pstdev(expert_ranks),
                    })
                layer_total = sum(
                    sum(profile["matrices"][phase][layer]) for profile in representatives
                )
                layer_core_mass = sum(
                    profile["matrices"][phase][layer][expert]
                    for profile in representatives for expert in core
                )
                total_mass += layer_total
                core_mass += layer_core_mass
                all_core_keys.extend((layer + 1) * 1000 + expert for expert in core)
                layers.append({
                    "layer": layer + 1,
                    "core_experts": core,
                    "core_expert_count": len(core),
                    "fraction_of_layer_experts": len(core) / EXPERTS_PER_LAYER,
                    "selected_mass_fraction": layer_core_mass / layer_total if layer_total else 0.0,
                    "rank_stability": expert_rows,
                })
            held_out = []
            for held_index, held_profile in enumerate(representatives):
                training = [
                    profile for index, profile in enumerate(representatives) if index != held_index
                ]
                held_threshold = math.ceil(gamma * len(training))
                held_core_mass = 0
                held_total_mass = 0
                held_core_keys: set[int] = set()
                held_peripheral_keys: set[int] = set()
                core_experts_by_layer = []
                for layer in range(ROUTED_LAYERS):
                    core = [
                        expert for expert in range(EXPERTS_PER_LAYER)
                        if sum(profile["matrices"][phase][layer][expert] > 0 for profile in training)
                        >= held_threshold
                    ]
                    core_set = set(core)
                    core_experts_by_layer.append(core)
                    row = held_profile["matrices"][phase][layer]
                    held_total_mass += sum(row)
                    held_core_mass += sum(row[expert] for expert in core)
                    for expert, count in enumerate(row):
                        if not count:
                            continue
                        key = layer * EXPERTS_PER_LAYER + expert
                        if expert in core_set:
                            held_core_keys.add(key)
                        else:
                            held_peripheral_keys.add(key)
                held_out.append({
                    "held_out_case_id": held_profile["case_id"],
                    "held_out_family": held_profile["semantic_family"],
                    "training_family_count": len(training),
                    "threshold_family_count": held_threshold,
                    "core_mass_fraction": held_core_mass / held_total_mass if held_total_mass else 0.0,
                    "core_distinct_expert_keys": len(held_core_keys),
                    "peripheral_distinct_expert_keys": len(held_peripheral_keys),
                    "core_experts_by_layer": core_experts_by_layer,
                })
            gamma_rows.append({
                "gamma": gamma,
                "threshold_family_count": threshold,
                "core_expert_key_count": len(all_core_keys),
                "fraction_of_all_routed_expert_keys": len(all_core_keys) / (
                    ROUTED_LAYERS * EXPERTS_PER_LAYER
                ),
                "selected_mass_fraction": core_mass / total_mass if total_mass else 0.0,
                "layers": layers,
                "leave_one_family_out": held_out,
                "leave_one_family_out_core_mass_fraction": distribution(
                    row["core_mass_fraction"] for row in held_out
                ),
            })
        phase_outputs[phase] = {
            "audit_class": "TOPK_COMMITTEE_AUDIT",
            "complete_routing_weight_profile_available": False,
            "gamma_sensitivity": gamma_rows,
        }
    return {
        "schema_version": "phase13-6pg-standing-committee-core-periphery-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "MEASURED_OBSERVER"],
        "performance_evidence": False,
        "inputs": inputs,
        "phases": phase_outputs,
        "interpretation_guard": (
            "Selected top-16 frequency cannot assign semantic functions or establish substitutability."
        ),
        "replay_enrichment": "PENDING_EXACT_REPLAY_COUNTERFACTUAL",
        "disposition": "TOPK_COMMITTEE_AUDIT_COMPLETE_PENDING_REPLAY_ENRICHMENT",
    }


def main() -> int:
    args = arguments()
    progress_path = args.progress.resolve(strict=True)
    selection_path = args.selection.resolve(strict=True)
    output_root = args.output_root.resolve()
    if sha256(progress_path) != args.expected_progress_sha256:
        raise ValueError("observer progress identity changed")
    with progress_path.open() as stream:
        progress = json.load(stream)
    with selection_path.open() as stream:
        selection = json.load(stream)
    if (
        progress["status"] != "pass"
        or progress["accepted_capture_count"] != 44
        or progress["expected_capture_count"] != 44
        or progress["disposition"] != "OBSERVER_CAMPAIGN_COMPLETE_READY_FOR_SYNTHESIS"
        or progress["performance_interpretation"] != "FORBIDDEN"
    ):
        raise ValueError("observer campaign is not a complete non-performance pass")
    if selection["status"] != "pass" or selection["disposition"] != "POST_STAGE_A_SELECTIONS_FROZEN":
        raise ValueError("post-Stage-A selection identity is not frozen")
    inputs = {
        "observer_progress": identity(progress_path),
        "post_stage_a_selections": identity(selection_path),
        "analysis_script": identity(pathlib.Path(__file__)),
        "project_sha": progress["execution_project_sha"],
        "nested_llama_cpp": progress["nested_llama_cpp"],
        "helper_binary_sha256": progress["helper_binary_sha256"],
    }
    profiles = load_profiles(progress, selection)
    fingerprints = build_fingerprints(profiles, inputs)
    overlap, endpoints = build_overlaps(profiles, selection, inputs)
    committee = build_committee(profiles, selection, inputs)
    artifacts = {
        "family_route_fingerprints": write_json(
            output_root / "family-route-fingerprints.json", fingerprints
        ),
        "family_overlap_matrix": write_json(
            output_root / "family-overlap-matrix.json", overlap
        ),
        "stage_b2_family_length_route_endpoints": write_json(
            output_root / "stage-b2-family-length-route-endpoints.json", endpoints
        ),
        "standing_committee_core_periphery": write_json(
            output_root / "standing-committee-core-periphery.json", committee
        ),
    }
    index = {
        "schema_version": "phase13-6pg-stage-b-route-analysis-index-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "MEASURED_OBSERVER"],
        "performance_evidence": False,
        "inputs": inputs,
        "capture_count": len(profiles),
        "representative_count": selection["stage_b"]["count"],
        "endpoint_count": selection["stage_b2"]["count"],
        "artifacts": artifacts,
        "disposition": "STAGE_B_B2_MEASURED_OBSERVER_SYNTHESIS_COMPLETE",
    }
    index_identity = write_json(output_root / "stage-b-route-analysis-index.json", index)
    print(json.dumps({
        "status": "pass",
        "capture_count": len(profiles),
        "index": index_identity,
        "artifacts": artifacts,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

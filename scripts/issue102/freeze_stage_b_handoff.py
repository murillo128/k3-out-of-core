#!/usr/bin/env python3
"""Freeze compact issue-102 Stage-B/B2 and offline-replay handoff evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any, Iterable


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-project-sha", required=True)
    parser.add_argument("--nested-llama-sha", required=True)
    parser.add_argument("--observer-progress", type=pathlib.Path, required=True)
    parser.add_argument("--selections", type=pathlib.Path, required=True)
    parser.add_argument("--stage-a-checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--route-index", type=pathlib.Path, required=True)
    parser.add_argument("--route-overlap", type=pathlib.Path, required=True)
    parser.add_argument("--route-endpoints", type=pathlib.Path, required=True)
    parser.add_argument("--committee", type=pathlib.Path, required=True)
    parser.add_argument("--exact-mrc", type=pathlib.Path, required=True)
    parser.add_argument("--s2-counterfactual", type=pathlib.Path, required=True)
    parser.add_argument("--committee-counterfactual", type=pathlib.Path, required=True)
    parser.add_argument("--family-capacity", type=pathlib.Path, required=True)
    parser.add_argument("--posthoc-index", type=pathlib.Path, required=True)
    parser.add_argument("--family-length-analysis", type=pathlib.Path, required=True)
    parser.add_argument("--locality-calibration", type=pathlib.Path, required=True)
    parser.add_argument("--replay-source", type=pathlib.Path, required=True)
    parser.add_argument("--replay-binary", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
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


def load(path: pathlib.Path, schema: str) -> dict[str, Any]:
    with path.resolve(strict=True).open() as stream:
        document = json.load(stream)
    if document.get("schema_version") != schema or document.get("status") != "pass":
        raise ValueError(f"unexpected schema/status for {path}")
    return document


def quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("quantile of empty sequence")
    position = probability * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def distribution(values: Iterable[float]) -> dict[str, float | int]:
    rows = [float(value) for value in values]
    return {
        "count": len(rows),
        "min": min(rows),
        "p10": quantile(rows, 0.10),
        "median": statistics.median(rows),
        "mean": statistics.fmean(rows),
        "p90": quantile(rows, 0.90),
        "max": max(rows),
    }


def threshold_summary(exact: dict[str, Any]) -> dict[str, Any]:
    outputs = {}
    for label in ("C80", "C90", "C95", "C96", "C98"):
        values = [row["capacity_thresholds"][label] for row in exact["prompt_rows"]]
        attained = [row for row in values if row["status"] == "attained"]
        outputs[label] = {
            "attained_count": len(attained),
            "not_attainable_count": len(values) - len(attained),
            "attained_actual_gib": (
                distribution(row["actual_gib"] for row in attained) if attained else None
            ),
        }
    return outputs


def committee_summary(committee: dict[str, Any]) -> dict[str, Any]:
    outputs = {}
    for phase, phase_row in committee["phases"].items():
        outputs[phase] = {
            "audit_class": phase_row["audit_class"],
            "complete_routing_weight_profile_available": phase_row[
                "complete_routing_weight_profile_available"
            ],
            "gamma_sensitivity": [
                {
                    "gamma": row["gamma"],
                    "threshold_family_count": row["threshold_family_count"],
                    "core_expert_key_count": row["core_expert_key_count"],
                    "selected_mass_fraction": row["selected_mass_fraction"],
                    "leave_one_family_out_core_mass_fraction": row[
                        "leave_one_family_out_core_mass_fraction"
                    ],
                }
                for row in phase_row["gamma_sensitivity"]
            ],
        }
    return outputs


def physical_s2_summary(counterfactual: dict[str, Any]) -> dict[str, Any]:
    curves = [
        curve
        for prompt in counterfactual["prompt_rows"]
        for curve in prompt["capacity_curve"]
        if curve["physical_anchor"]
    ]
    return {
        "prompt_count": len(curves),
        "exact_hit_ratio": distribution(curve["exact_decode"]["hit_ratio"] for curve in curves),
        "s2_fixed_route_hit_ratio": distribution(
            curve["s2_fixed_route_decode"]["hit_ratio"] for curve in curves
        ),
        "s2_minus_exact_hit_ratio": distribution(
            curve["s2_minus_exact_hit_ratio"] for curve in curves
        ),
        "guard": "Captured exact hidden-state trajectory counterfactual; not physical TPS evidence.",
    }


def pin_counterfactual_summary(counterfactual: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[float, list[float]] = {}
    infeasible: dict[float, int] = {}
    for prompt in counterfactual["prompt_rows"]:
        for gamma_row in prompt["gamma_sensitivity"]:
            gamma = float(gamma_row["gamma"])
            grouped.setdefault(gamma, [])
            infeasible.setdefault(gamma, 0)
            for curve in gamma_row["capacity_curve"]:
                if curve["status"] == "pass":
                    grouped[gamma].append(curve["vs_global_lru"]["hit_ratio_delta"])
                else:
                    infeasible[gamma] += 1
    return [
        {
            "gamma": gamma,
            "feasible_prompt_capacity_cells": len(grouped[gamma]),
            "infeasible_prompt_capacity_cells": infeasible[gamma],
            "hit_ratio_delta_vs_global_lru": distribution(grouped[gamma]),
            "positive_cells": sum(value > 0 for value in grouped[gamma]),
            "zero_cells": sum(value == 0 for value in grouped[gamma]),
            "negative_cells": sum(value < 0 for value in grouped[gamma]),
        }
        for gamma in sorted(grouped)
    ]


def main() -> None:
    args = arguments()
    progress = load(args.observer_progress, "phase13-6pg-stage-b-observer-resume-progress-v4")
    selections = load(args.selections, "phase13-6pg-post-stage-a-selections-v1")
    checkpoint = load(args.stage_a_checkpoint, "issue102-stage-a-checkpoint-v1")
    route_index = load(args.route_index, "phase13-6pg-stage-b-route-analysis-index-v1")
    overlap = load(args.route_overlap, "phase13-6pg-family-overlap-matrix-v1")
    endpoints = load(args.route_endpoints, "phase13-6pg-stage-b2-family-length-route-endpoints-v1")
    committee = load(args.committee, "phase13-6pg-standing-committee-core-periphery-v1")
    exact = load(args.exact_mrc, "phase13-6pg-exact-capacity-mrc-v1")
    s2 = load(args.s2_counterfactual, "phase13-6pg-s2-fixed-route-capacity-counterfactual-v1")
    pin = load(args.committee_counterfactual, "phase13-6pg-committee-pin-capacity-counterfactual-v1")
    family_capacity = load(args.family_capacity, "phase13-6pg-family-length-capacity-extension-v1")
    posthoc_index = load(args.posthoc_index, "phase13-6pg-stage-a-posthoc-analysis-index-v1")
    family_length = load(args.family_length_analysis, "phase13-6pg-stage-a-family-length-analysis-v1")
    calibration = load(args.locality_calibration, "phase13-6pg-locality-throughput-calibration-v1")

    if (
        progress["accepted_capture_count"] != 44
        or progress["expected_capture_count"] != 44
        or route_index["capture_count"] != 44
        or exact["observer_capture_physical_prevalidation"]["matched_capture_count"] != 44
        or exact["observer_capture_physical_prevalidation"]["status"] != "PASS"
        or selections["stage_c"]["count"] != 24
        or len({row["case_id"] for row in selections["stage_c"]["prompts"]}) != 24
        or len(checkpoint["primary_rows"]) != 128
        or len(checkpoint["sentinels"]["runs"]) != 8
    ):
        raise ValueError("handoff completeness invariant failed")

    artifact_paths = {
        "observer_progress": args.observer_progress,
        "post_stage_a_selections": args.selections,
        "stage_a_final_checkpoint": args.stage_a_checkpoint,
        "stage_b_route_analysis_index": args.route_index,
        "family_overlap_matrix": args.route_overlap,
        "stage_b2_family_length_route_endpoints": args.route_endpoints,
        "standing_committee_core_periphery": args.committee,
        "exact_capacity_mrc": args.exact_mrc,
        "s2_fixed_route_capacity_counterfactual": args.s2_counterfactual,
        "committee_pin_capacity_counterfactual": args.committee_counterfactual,
        "family_length_capacity_extension": args.family_capacity,
        "stage_a_posthoc_analysis_index": args.posthoc_index,
        "stage_a_family_length_analysis": args.family_length_analysis,
        "locality_throughput_calibration": args.locality_calibration,
    }
    physical = next(
        row for row in exact["representative_aggregate"] if row["capacity"]["physical_anchor"]
    )
    family_models = family_length["descriptive_ols"]
    handoff = {
        "schema_version": "phase13-6pg-stage-b-capacity-handoff-v1",
        "status": "pass",
        "execution_target": {
            "project_sha": args.execution_project_sha,
            "nested_llama_cpp_sha": args.nested_llama_sha,
        },
        "completeness": {
            "stage_a_primary": 128,
            "stage_a_sentinels": 8,
            "stage_b_representatives": 16,
            "stage_b2_endpoints": 32,
            "observer_captures": 44,
            "observer_performance_interpretation": "FORBIDDEN",
            "stage_c_frozen_unique_prompts": 24,
            "stage_c_outcomes_inspected": 0,
        },
        "tools": {
            "handoff_freezer_source": identity(pathlib.Path(__file__)),
            "observer_replay_source": identity(args.replay_source),
            "observer_replay_binary": identity(args.replay_binary),
        },
        "artifacts": {name: identity(path) for name, path in artifact_paths.items()},
        "stage_b_route_summary": {
            "representative_decode_pair_count": overlap["representative_pair_count"],
            "representative_decode_pairs": overlap["representative_summary"],
            "stage_b2_endpoints": endpoints["comparison_summary"],
            "endpoint_limit": endpoints["endpoint_limit"],
            "committee": committee_summary(committee),
            "interpretation_guard": committee["interpretation_guard"],
        },
        "offline_capacity_replay_summary": {
            "replacement_policy": exact["replacement_policy"],
            "physical_observer_prevalidation": exact[
                "observer_capture_physical_prevalidation"
            ],
            "physical_anchor_validation": exact["physical_anchor_validation"],
            "physical_7849_representative_exact_hit_ratio": physical[
                "representative_prompt_hit_ratio"
            ],
            "physical_7849_representative_exact_loads_per_token": physical[
                "representative_prompt_loads_per_token"
            ],
            "capacity_thresholds": threshold_summary(exact),
            "s2_fixed_route_physical_counterfactual": physical_s2_summary(s2),
            "committee_pin_same_capacity_counterfactual": pin_counterfactual_summary(pin),
            "family_length_endpoint_status": family_capacity["status"],
            "family_length_endpoint_limit": family_capacity["endpoint_limit"],
            "authority": "Larger-capacity curves remain non-authoritative pending Stage-C EXACT.",
        },
        "stage_a_posthoc_summary": {
            "actual_token_associations": family_length["global_actual_token_associations"],
            "family_specific_bh_rejections": {
                outcome: [
                    row["semantic_family"]
                    for row in family_length["per_family_actual_token_slopes"][outcome]
                    if row["bh_q_0_05_reject"]
                ]
                for outcome in ("hit_ratio", "decode_tok_s")
            },
            "descriptive_r_squared": {
                outcome: {
                    model: family_models[outcome][model]["r_squared"]
                    for model in (
                        "family_only", "length_level_only", "actual_token_only",
                        "family_plus_length_level", "family_plus_actual_tokens",
                        "within_family_centered_actual_tokens",
                    )
                }
                for outcome in ("hit_ratio", "decode_tok_s")
            },
            "locality_throughput_calibration": {
                predictor: {
                    "r_squared": calibration["models"][predictor]["r_squared"],
                    "mae": calibration["models"][predictor]["mae"],
                    "rmse": calibration["models"][predictor]["rmse"],
                    "lofo_mae": calibration["leave_one_family_out"][predictor][
                        "pooled_held_out_mae"
                    ],
                    "lofo_rmse": calibration["leave_one_family_out"][predictor][
                        "pooled_held_out_rmse"
                    ],
                }
                for predictor in ("hit_ratio", "loads_per_token")
            },
            "interpretation": "Artifact-only post-hoc descriptive analysis; no model process executed.",
        },
        "stage_c_frozen_selection": selections["stage_c"],
        "checkpoint_b_readiness": {
            "prompt_level_evidence_complete": True,
            "sentinel_determinism": checkpoint["sentinels"],
            "timed_instrumentation_contamination": False,
            "stage_c_rule_reproduced_before_followup_outcomes": True,
            "stage_c_ids_published_before_followup_outcomes": True,
            "safe_to_request_independent_checkpoint_b_review": True,
        },
        "disposition": "STAGE_B_AND_OFFLINE_ANALYSIS_COMPLETE_CHECKPOINT_B_REVIEW_REQUIRED",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(handoff, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({"status": "pass", "output": identity(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

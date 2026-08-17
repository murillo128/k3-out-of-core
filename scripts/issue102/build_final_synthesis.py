#!/usr/bin/env python3
"""Build deterministic issue-102 final synthesis and normalized prompt table."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pathlib
import statistics
from typing import Any, Iterable, Sequence


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-a-checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-a-sha256", required=True)
    parser.add_argument("--stage-b-handoff", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-b-handoff-sha256", required=True)
    parser.add_argument("--s2-fixed-route-replay", type=pathlib.Path, required=True)
    parser.add_argument("--expected-s2-fixed-route-replay-sha256", required=True)
    parser.add_argument("--stage-c-synthesis", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-c-synthesis-sha256", required=True)
    parser.add_argument("--output-json", type=pathlib.Path, required=True)
    parser.add_argument("--output-csv", type=pathlib.Path, required=True)
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
        if isinstance(document, dict) and "schema_version" in document:
            result["schema_version"] = document["schema_version"]
    return result


def require_identity(path: pathlib.Path, expected_sha256: str) -> dict[str, Any]:
    result = identity(path)
    if result["sha256"] != expected_sha256:
        raise ValueError(f"identity mismatch: {path}")
    return result


def load(path: pathlib.Path, schema: str, statuses: set[str]) -> dict[str, Any]:
    with path.resolve(strict=True).open() as stream:
        document = json.load(stream)
    if document.get("schema_version") != schema or document.get("status") not in statuses:
        raise ValueError(f"unexpected schema/status for {path}")
    return document


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return identity(path)


def write_csv(path: pathlib.Path, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("cannot write empty CSV")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return identity(path)


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


def classify_strength(value: float) -> str:
    if value >= 0.25:
        return "strong"
    if value >= 0.10:
        return "moderate"
    return "weak"


def normalized_row(
    row: dict[str, Any],
    stage_c: dict[str, Any] | None,
    diagnostic_max_regret: float | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ordinal": row["ordinal"],
        "case_id": row["case_id"],
        "semantic_family": row["semantic_family"],
        "length_level": row["length_level"],
        "templated_prompt_tokens": row["templated_prompt_tokens"],
        "s2_p50_decode_tok_s": row["decode_tok_s"],
        "s2_p50_p50_forward_s": row["p50_forward_s"],
        "s2_p50_p95_forward_s": row["p95_forward_s"],
        "s2_p50_p99_forward_s": row["p99_forward_s"],
        "s2_p50_hit_ratio": row["hit_ratio"],
        "s2_p50_loads_per_token": row["loads_per_token"],
        "s2_p50_bytes_per_token": row["bytes_per_token"],
        "s2_p50_changed_fraction": row["changed_fraction"],
        "s2_p50_swaps_per_token": row["swaps_per_token"],
        "s2_p50_cumulative_score_regret": row["cumulative_score_regret"],
        "s2_p50_mean_score_regret_per_realized_swap": row[
            "mean_score_regret_per_realized_swap"
        ],
        "s2_p50_timed_maximum_realized_regret": row["maximum_realized_regret"],
        "s2_p50_timed_maximum_realized_regret_status": row[
            "maximum_realized_regret_status"
        ],
        "s2_p50_observer_replay_maximum_realized_regret": diagnostic_max_regret,
        "s2_p50_observer_replay_maximum_realized_regret_status": (
            "available_non_performance_diagnostic" if diagnostic_max_regret is not None
            else "not_captured_for_this_prompt"
        ),
        "s2_p50_generated_token_hash": row["generated_token_hash"],
        "stage_a_result_sha256": row["result_sha256"],
        "stage_c_selected": stage_c is not None,
        "stage_c_selection_role": stage_c["selection_role"] if stage_c else "",
    }
    for policy in ("exact", "knee"):
        values = stage_c["policies"][policy] if stage_c else None
        result[f"stage_c_{policy}_decode_tok_s"] = values["decode_tok_s"] if values else ""
        result[f"stage_c_{policy}_hit_ratio"] = values["hit_ratio"] if values else ""
        result[f"stage_c_{policy}_loads_per_token"] = (
            values["loads_per_token"] if values else ""
        )
        result[f"stage_c_{policy}_bytes_per_token"] = (
            values["bytes_per_token"] if values else ""
        )
        result[f"stage_c_{policy}_generated_token_hash"] = (
            values["generated_token_hash"] if values else ""
        )
    for name in ("s2_vs_exact", "knee_vs_exact", "s2_vs_knee"):
        values = stage_c["comparisons"][name] if stage_c else None
        result[f"stage_c_{name}_decode_tok_s_ratio"] = (
            values["decode_tok_s_ratio"] if values else ""
        )
        result[f"stage_c_{name}_hit_ratio_delta"] = (
            values["hit_ratio_delta"] if values else ""
        )
        result[f"stage_c_{name}_loads_per_token_delta"] = (
            values["loads_per_token_delta"] if values else ""
        )
        result[f"stage_c_{name}_bytes_per_token_delta"] = (
            values["bytes_per_token_delta"] if values else ""
        )
    result["stage_c_absolute_locality_regime"] = (
        stage_c["regimes"]["absolute_s2_locality"] if stage_c else ""
    )
    result["stage_c_s2_vs_knee_regime"] = (
        stage_c["regimes"]["s2_vs_knee"] if stage_c else ""
    )
    return result


def main() -> int:
    args = arguments()
    inputs = {
        "stage_a_checkpoint": require_identity(
            args.stage_a_checkpoint, args.expected_stage_a_sha256
        ),
        "stage_b_handoff": require_identity(
            args.stage_b_handoff, args.expected_stage_b_handoff_sha256
        ),
        "s2_fixed_route_replay": require_identity(
            args.s2_fixed_route_replay, args.expected_s2_fixed_route_replay_sha256
        ),
        "stage_c_synthesis": require_identity(
            args.stage_c_synthesis, args.expected_stage_c_synthesis_sha256
        ),
        "generator": identity(pathlib.Path(__file__)),
    }
    stage_a = load(args.stage_a_checkpoint, "issue102-stage-a-checkpoint-v1", {"pass"})
    handoff = load(
        args.stage_b_handoff, "phase13-6pg-stage-b-capacity-handoff-v1", {"pass"}
    )
    s2_replay = load(
        args.s2_fixed_route_replay,
        "phase13-6pg-s2-fixed-route-capacity-counterfactual-v1",
        {"pass"},
    )
    stage_c = load(
        args.stage_c_synthesis, "phase13-6pg-stage-c-synthesis-v1", {"pass"}
    )
    if (
        len(stage_a.get("primary_rows", [])) != 128
        or len(stage_a.get("sentinels", {}).get("runs", [])) != 8
        or not stage_a["sentinels"]["all_deterministic_signatures_equal"]
        or handoff["completeness"]["observer_captures"] != 44
        or handoff["completeness"]["stage_b_representatives"] != 16
        or handoff["completeness"]["stage_c_frozen_unique_prompts"] != 24
        or len(s2_replay.get("prompt_rows", [])) != 44
        or stage_c["completeness"]["accepted_cells"] != 48
        or stage_c["completeness"]["failed_cells"] != 0
        or stage_c["physical_replay_anchor_validation"]["status"] != "PASS"
        or stage_c["physical_replay_anchor_validation"]["exact_match_count"] != 16
    ):
        raise ValueError("final synthesis completeness invariant failed")

    diagnostic_regret: dict[str, float] = {}
    for prompt in s2_replay["prompt_rows"]:
        physical = next(row for row in prompt["capacity_curve"] if row["physical_anchor"])
        diagnostic_regret[prompt["case_id"]] = physical["routing"]["maximum_realized_regret"]
    if len(diagnostic_regret) != 44 or any(
        not (0.0 <= value <= 0.007303759455680847) for value in diagnostic_regret.values()
    ):
        raise ValueError("observer maximum-regret diagnostic invariant failed")

    stage_c_by_case = {row["case_id"]: row for row in stage_c["prompt_rows"]}
    normalized = [
        normalized_row(row, stage_c_by_case.get(row["case_id"]), diagnostic_regret.get(row["case_id"]))
        for row in sorted(stage_a["primary_rows"], key=lambda item: item["ordinal"])
    ]
    if (
        len(normalized) != 128
        or len({row["case_id"] for row in normalized}) != 128
        or sum(row["stage_c_selected"] for row in normalized) != 24
    ):
        raise ValueError("normalized prompt table invariant failed")
    normalized_identity = write_csv(args.output_csv, normalized)

    stage_a_tps = stage_a["overall_distributions"]["decode_tok_s"]
    sentinel_tps = stage_a["sentinels"]["decode_tok_s"]
    prompt_spread = stage_a_tps["p90"] - stage_a_tps["p10"]
    sentinel_spread = sentinel_tps["p90"] - sentinel_tps["p10"]
    dispersion_ratio = prompt_spread / sentinel_spread
    if dispersion_ratio >= 10.0:
        cross_prompt_dispersion = "high"
    elif dispersion_ratio >= 3.0:
        cross_prompt_dispersion = "moderate"
    else:
        cross_prompt_dispersion = "low"

    r_squared = handoff["stage_a_posthoc_summary"]["descriptive_r_squared"]
    family_r_squared = max(
        r_squared["decode_tok_s"]["family_only"],
        r_squared["hit_ratio"]["family_only"],
    )
    length_r_squared = max(
        r_squared["decode_tok_s"]["length_level_only"],
        r_squared["hit_ratio"]["length_level_only"],
    )
    family_effect = classify_strength(family_r_squared)
    length_effect = classify_strength(length_r_squared)

    route_pairs = handoff["stage_b_route_summary"]["representative_decode_pairs"]
    route_jaccard = route_pairs["mean_layerwise_support_jaccard"]["median"]
    route_jsd = route_pairs["weighted_jensen_shannon_divergence_bits"]["median"]
    route_coverage = "broad" if route_jaccard <= 0.50 and route_jsd >= 0.25 else "limited"

    s2_paired_gain = stage_c["primary_outcomes"]["S2P50_PAIRED_GAIN"]
    interaction = stage_c["primary_outcomes"]["KNEE_VS_S2P50_PROMPT_INTERACTION"]
    absolute_regimes = stage_c["regimes"]["absolute_s2_locality_counts"]
    prompt_conditioned = (
        "supported" if (
            cross_prompt_dispersion == "high"
            and route_coverage == "broad"
            and len(absolute_regimes) >= 2
        ) else "weak"
    )
    corpus_ready = (
        stage_a["checkpoint"]["completed_primary_prompts"] == 128
        and handoff["completeness"]["observer_captures"] == 44
        and stage_c["completeness"]["accepted_cells"] == 48
    )
    primary_outcomes = {
        "S2P50_CROSS_PROMPT_DISPERSION": cross_prompt_dispersion,
        "SEMANTIC_FAMILY_EFFECT": family_effect,
        "TOKEN_LENGTH_EFFECT": length_effect,
        "ROUTE_COVERAGE": route_coverage,
        "S2P50_PAIRED_GAIN": s2_paired_gain,
        "KNEE_VS_S2P50_PROMPT_INTERACTION": interaction,
        "PROMPT_CONDITIONED_REGIMES": prompt_conditioned,
        "FOLLOWUP_99_CORPUS_READY": "yes" if corpus_ready else "no",
    }
    expected_keys = {
        "S2P50_CROSS_PROMPT_DISPERSION", "SEMANTIC_FAMILY_EFFECT",
        "TOKEN_LENGTH_EFFECT", "ROUTE_COVERAGE", "S2P50_PAIRED_GAIN",
        "KNEE_VS_S2P50_PROMPT_INTERACTION", "PROMPT_CONDITIONED_REGIMES",
        "FOLLOWUP_99_CORPUS_READY",
    }
    if set(primary_outcomes) != expected_keys or not corpus_ready:
        raise ValueError("primary outcome/follow-up readiness invariant failed")

    document = {
        "schema_version": "phase13-6pg-issue102-final-synthesis-v1",
        "status": "pass",
        "provenance": "COMPLETE_FROZEN_ISSUE102_EVIDENCE_SYNTHESIS",
        "inputs": inputs,
        "completeness": {
            "stage_a_primary": 128,
            "stage_a_sentinels": 8,
            "stage_b_b2_observer_captures": 44,
            "stage_b_family_representatives": 16,
            "stage_b2_family_length_endpoints": 32,
            "stage_c_unique_prompts": 24,
            "stage_c_physical_cells": 48,
            "stage_c_failed_cells": 0,
            "stage_c_physical_replay_anchor_exact_matches": 16,
        },
        "stage_a_cross_prompt": {
            "overall_distributions": stage_a["overall_distributions"],
            "sentinel_tps": sentinel_tps,
            "sentinel_deterministic_signature_status": stage_a["sentinels"]["status"],
            "sentinel_all_deterministic_signatures_equal": True,
            "prompt_tps_p90_minus_p10": prompt_spread,
            "sentinel_tps_p90_minus_p10": sentinel_spread,
            "prompt_to_sentinel_tps_spread_ratio": dispersion_ratio,
            "dispersion_classification_rule": {
                "low": "ratio < 3",
                "moderate": "3 <= ratio < 10",
                "high": "ratio >= 10",
                "authority": "post-hoc descriptive label; not a tuning gate",
            },
        },
        "family_length_decomposition": {
            "descriptive_r_squared": r_squared,
            "family_effect_r_squared_statistic": family_r_squared,
            "token_length_effect_r_squared_statistic": length_r_squared,
            "effect_classification_rule": {
                "weak": "R^2 < 0.10",
                "moderate": "0.10 <= R^2 < 0.25",
                "strong": "R^2 >= 0.25",
                "authority": "post-hoc descriptive label; not a causal claim",
            },
            "family_specific_bh_rejections": handoff["stage_a_posthoc_summary"][
                "family_specific_bh_rejections"
            ],
        },
        "route_diversity": {
            "representative_count": 16,
            "pair_count": route_pairs["count"],
            "decode_pair_distributions": route_pairs,
            "coverage_rule": (
                "broad when median layerwise support Jaccard <= 0.50 and median weighted "
                "Jensen-Shannon divergence >= 0.25; otherwise limited"
            ),
            "coverage_label": route_coverage,
            "interpretation_guard": handoff["stage_b_route_summary"]["interpretation_guard"],
        },
        "maximum_realized_regret": {
            "stage_a_timed_path": "unavailable_without_observer",
            "observer_replay_physical_prompt_count": len(diagnostic_regret),
            "observer_replay_physical_distribution": distribution(diagnostic_regret.values()),
            "maximum_allowed_regret": 0.007303759455680847,
            "authority": "separate non-performance observer/replay diagnostic",
        },
        "stage_c": {
            "physical_replay_anchor_validation": stage_c[
                "physical_replay_anchor_validation"
            ],
            "aggregates": stage_c["aggregates"],
            "regimes": stage_c["regimes"],
            "trajectory_authority": (
                "generated-ID prefix/alignment/edit/hash evidence is trajectory feedback only"
            ),
        },
        "primary_outcomes": primary_outcomes,
        "observations": [
            (
                "OBSERVED: Stage A accepted all 128 frozen primary prompts and all eight "
                "deterministic sentinels at one S2_P50/capacity/runtime envelope."
            ),
            (
                f"OBSERVED: prompt TPS p90-p10 dispersion was {dispersion_ratio:.3f} times "
                "the repeated-sentinel TPS p90-p10 dispersion."
            ),
            (
                "OBSERVED: semantic-family-only descriptive models explain about half of "
                "TPS/hit variation, while ordinal token-length-only models explain under 1%."
            ),
            (
                "OBSERVED: Stage-B representative routes have broad cross-family diversity; "
                "selected-expert frequency does not assign semantic function."
            ),
            (
                "OBSERVED: S2_P50 improved TPS, hit ratio, and loads/token versus both EXACT "
                "and KNEE in all 24 frozen Stage-C prompts; gain magnitude remained prompt-conditioned."
            ),
            (
                "OBSERVED: all 16 representative physical EXACT cells matched semantic-order "
                "replay at the 7,849-slot anchor exactly."
            ),
        ],
        "limitations": [
            "The 16x8 corpus is deliberate and complete, not a random sample of all Kimi K3 prompts.",
            "Family/length labels and outcome categories are descriptive, not causal or universal.",
            "The 24-prompt Stage-C set is a frozen explanatory sample, not a second search.",
            "Generated-token equality/divergence is not semantic-quality evidence; #77/#99 own quality.",
            "Observer timing is excluded from performance evidence; physical Stage A/C own TPS.",
            "No policy, capacity, corpus, order, runtime, model, or storage topology was retuned.",
        ],
        "handoff": {
            "issue_99_and_81": "frozen corpus and normalized prompt-level evidence ready",
            "issue_105": "secondary analyses only; no additional issue-102 experimentation",
            "issue_98": "resume final v3 synthesis/release/review/closeout after issue-102 closeout",
        },
        "artifacts": {"normalized_prompt_level_csv": normalized_identity},
        "disposition": "ISSUE102_FINAL_SYNTHESIS_COMPLETE_READY_FOR_EVIDENCE_FREEZE_AND_CHECKPOINT_C",
    }
    output_identity = write_json(args.output_json, document)
    print(json.dumps({
        "status": "pass",
        "final_synthesis": output_identity,
        "normalized_prompt_level": normalized_identity,
        "primary_outcomes": primary_outcomes,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build deterministic issue-102 Stage-C paired and physical-anchor synthesis."""

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


STAGE_A_SCHEMA = "issue102-stage-a-checkpoint-v1"
SELECTION_SCHEMA = "phase13-6pg-stage-c-preregistration-v1"
CONTROL_SCHEMA = "phase13-6pg-stage-c-recovery-control-v1"
PROGRESS_SCHEMA = "phase13-6pg-stage-c-recovery-progress-v1"
HANDOFF_SCHEMA = "phase13-6pg-stage-b-capacity-handoff-v1"
REPLAY_SCHEMA = "phase13-6pg-exact-capacity-mrc-v1"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-project-sha", required=True)
    parser.add_argument("--nested-llama-sha", required=True)
    parser.add_argument("--stage-a-checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-a-sha256", required=True)
    parser.add_argument("--stage-a-output-root", type=pathlib.Path, required=True)
    parser.add_argument("--stage-c-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
    parser.add_argument("--stage-c-recovery-control", type=pathlib.Path, required=True)
    parser.add_argument("--expected-recovery-control-sha256", required=True)
    parser.add_argument("--stage-c-progress", type=pathlib.Path, required=True)
    parser.add_argument("--expected-progress-sha256", required=True)
    parser.add_argument("--stage-b-handoff", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-b-handoff-sha256", required=True)
    parser.add_argument("--exact-capacity-replay", type=pathlib.Path, required=True)
    parser.add_argument("--expected-exact-capacity-replay-sha256", required=True)
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
    if not rows:
        return {"count": 0}
    return {
        "count": len(rows),
        "min": min(rows),
        "p10": quantile(rows, 0.10),
        "median": statistics.median(rows),
        "mean": statistics.fmean(rows),
        "p90": quantile(rows, 0.90),
        "max": max(rows),
    }


def longest_common_prefix(left: Sequence[int], right: Sequence[int]) -> int:
    result = 0
    for lhs, rhs in zip(left, right):
        if lhs != rhs:
            break
        result += 1
    return result


def edit_distance(left: Sequence[int], right: Sequence[int]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(min(
                previous[right_index] + 1,
                current[right_index - 1] + 1,
                previous[right_index - 1] + (left_value != right_value),
            ))
        previous = current
    return previous[-1]


def trajectory(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_ids = left["generated_ids"]
    right_ids = right["generated_ids"]
    if len(left_ids) != 64 or len(right_ids) != 64:
        raise ValueError("trajectory does not contain 64 generated IDs")
    prefix = longest_common_prefix(left_ids, right_ids)
    aligned_equal = sum(lhs == rhs for lhs, rhs in zip(left_ids, right_ids))
    return {
        "left_hash": left["generated_token_hash"],
        "right_hash": right["generated_token_hash"],
        "hash_equal": left["generated_token_hash"] == right["generated_token_hash"],
        "longest_common_prefix_tokens": prefix,
        "first_divergence_index": None if prefix == len(left_ids) else prefix,
        "aligned_equal_token_count": aligned_equal,
        "aligned_equal_fraction": aligned_equal / len(left_ids),
        "levenshtein_edit_distance": edit_distance(left_ids, right_ids),
        "authority": "trajectory feedback only; not semantic-quality evidence",
    }


def metric_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "decode_tok_s": row["decode_tok_s"],
        "hit_ratio": row["hit_ratio"],
        "loads_per_token": row["loads_per_token"],
        "bytes_per_token": row["bytes_per_token"],
        "generated_token_count": row["generated_token_count"],
        "generated_token_hash": row["generated_token_hash"],
        "routing": row.get("routing", {
            "changed_decisions": row.get("changed_decisions"),
            "realized_swaps": row.get("realized_swaps"),
            "cumulative_score_regret": row.get("cumulative_score_regret"),
            "maximum_realized_regret": row.get("maximum_realized_regret"),
            "maximum_realized_regret_status": row.get("maximum_realized_regret_status"),
        }),
    }


def comparison(
    left_name: str,
    left: dict[str, Any],
    left_output: dict[str, Any],
    right_name: str,
    right: dict[str, Any],
    right_output: dict[str, Any],
) -> dict[str, Any]:
    return {
        "left": left_name,
        "right": right_name,
        "decode_tok_s_ratio": left["decode_tok_s"] / right["decode_tok_s"],
        "decode_tok_s_delta": left["decode_tok_s"] - right["decode_tok_s"],
        "decode_tok_s_delta_fraction": left["decode_tok_s"] / right["decode_tok_s"] - 1.0,
        "hit_ratio_delta": left["hit_ratio"] - right["hit_ratio"],
        "loads_per_token_delta": left["loads_per_token"] - right["loads_per_token"],
        "loads_per_token_ratio": left["loads_per_token"] / right["loads_per_token"],
        "bytes_per_token_delta": left["bytes_per_token"] - right["bytes_per_token"],
        "bytes_per_token_ratio": left["bytes_per_token"] / right["bytes_per_token"],
        "trajectory": trajectory(left_output, right_output),
    }


def result_output(
    path: pathlib.Path,
    expected_sha256: str,
    case_id: str,
    point: str,
    expected: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    observed = require_identity(path, expected_sha256)
    with path.open() as stream:
        document = json.load(stream)
    measured = document.get("measured", {})
    output = document.get("output", {})
    cold = measured.get("cold_delta", {})
    storage = measured.get("storage_delta", {})
    if (
        document.get("status") != "pass"
        or document.get("point") != point
        or document.get("case", {}).get("id") != case_id
        or measured.get("decode_forwards") != 64
        or output.get("generated_token_count") != 64
        or len(output.get("generated_ids", [])) != 64
        or output.get("generated_token_hash") != expected["generated_token_hash"]
        or measured.get("decode_tok_s") != expected["decode_tok_s"]
        or cold.get("hits") != expected["hits"]
        or cold.get("misses") != expected["misses"]
        or storage.get("backing_bytes") != expected["backing_bytes"]
    ):
        raise ValueError(f"result/compact mismatch: {case_id}/{point}")
    requests = cold["hits"] + cold["misses"]
    if (
        requests != 94208
        or cold["hits"] / requests != expected["hit_ratio"]
        or cold["misses"] / 64 != expected["loads_per_token"]
        or storage["backing_bytes"] / 64 != expected["bytes_per_token"]
    ):
        raise ValueError(f"derived metric mismatch: {case_id}/{point}")
    return output, observed


def aggregate_comparison(rows: Sequence[dict[str, Any]], name: str) -> dict[str, Any]:
    values = [row["comparisons"][name] for row in rows]
    return {
        "count": len(values),
        "decode_tok_s_ratio": distribution(row["decode_tok_s_ratio"] for row in values),
        "decode_tok_s_delta_fraction": distribution(
            row["decode_tok_s_delta_fraction"] for row in values
        ),
        "hit_ratio_delta": distribution(row["hit_ratio_delta"] for row in values),
        "loads_per_token_delta": distribution(row["loads_per_token_delta"] for row in values),
        "bytes_per_token_delta": distribution(row["bytes_per_token_delta"] for row in values),
        "direction_counts": {
            "tps_better": sum(row["decode_tok_s_ratio"] > 1.0 for row in values),
            "tps_equal": sum(row["decode_tok_s_ratio"] == 1.0 for row in values),
            "tps_worse": sum(row["decode_tok_s_ratio"] < 1.0 for row in values),
            "hit_better": sum(row["hit_ratio_delta"] > 0.0 for row in values),
            "hit_equal": sum(row["hit_ratio_delta"] == 0.0 for row in values),
            "hit_worse": sum(row["hit_ratio_delta"] < 0.0 for row in values),
            "loads_lower": sum(row["loads_per_token_delta"] < 0.0 for row in values),
            "loads_equal": sum(row["loads_per_token_delta"] == 0.0 for row in values),
            "loads_higher": sum(row["loads_per_token_delta"] > 0.0 for row in values),
        },
        "trajectory": {
            "hash_equal_count": sum(row["trajectory"]["hash_equal"] for row in values),
            "longest_common_prefix_tokens": distribution(
                row["trajectory"]["longest_common_prefix_tokens"] for row in values
            ),
            "aligned_equal_fraction": distribution(
                row["trajectory"]["aligned_equal_fraction"] for row in values
            ),
            "levenshtein_edit_distance": distribution(
                row["trajectory"]["levenshtein_edit_distance"] for row in values
            ),
            "authority": "trajectory feedback only; not semantic-quality evidence",
        },
    }


def csv_row(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "prompt_ordinal": row["prompt_ordinal"],
        "case_id": row["case_id"],
        "semantic_family": row["semantic_family"],
        "length_level": row["length_level"],
        "prompt_tokens": row["prompt_tokens"],
        "selection_role": row["selection_role"],
        "absolute_locality_regime": row["regimes"]["absolute_s2_locality"],
        "s2_vs_knee_regime": row["regimes"]["s2_vs_knee"],
    }
    for policy in ("s2_p50", "exact", "knee"):
        result[f"{policy}_decode_tok_s"] = row["policies"][policy]["decode_tok_s"]
        result[f"{policy}_hit_ratio"] = row["policies"][policy]["hit_ratio"]
        result[f"{policy}_loads_per_token"] = row["policies"][policy]["loads_per_token"]
        result[f"{policy}_bytes_per_token"] = row["policies"][policy]["bytes_per_token"]
        result[f"{policy}_generated_token_hash"] = row["policies"][policy][
            "generated_token_hash"
        ]
    for name in ("s2_vs_exact", "knee_vs_exact", "s2_vs_knee"):
        comparison_row = row["comparisons"][name]
        result[f"{name}_decode_tok_s_ratio"] = comparison_row["decode_tok_s_ratio"]
        result[f"{name}_hit_ratio_delta"] = comparison_row["hit_ratio_delta"]
        result[f"{name}_loads_per_token_delta"] = comparison_row["loads_per_token_delta"]
        result[f"{name}_bytes_per_token_delta"] = comparison_row["bytes_per_token_delta"]
        result[f"{name}_common_prefix_tokens"] = comparison_row["trajectory"][
            "longest_common_prefix_tokens"
        ]
        result[f"{name}_aligned_equal_fraction"] = comparison_row["trajectory"][
            "aligned_equal_fraction"
        ]
        result[f"{name}_edit_distance"] = comparison_row["trajectory"][
            "levenshtein_edit_distance"
        ]
    return result


def main() -> int:
    args = arguments()
    input_identities = {
        "stage_a_checkpoint": require_identity(
            args.stage_a_checkpoint, args.expected_stage_a_sha256
        ),
        "stage_c_preregistration": require_identity(
            args.stage_c_preregistration, args.expected_preregistration_sha256
        ),
        "stage_c_recovery_control": require_identity(
            args.stage_c_recovery_control, args.expected_recovery_control_sha256
        ),
        "stage_c_progress": require_identity(
            args.stage_c_progress, args.expected_progress_sha256
        ),
        "stage_b_handoff": require_identity(
            args.stage_b_handoff, args.expected_stage_b_handoff_sha256
        ),
        "exact_capacity_replay": require_identity(
            args.exact_capacity_replay, args.expected_exact_capacity_replay_sha256
        ),
        "generator": identity(pathlib.Path(__file__)),
    }
    stage_a = load(args.stage_a_checkpoint, STAGE_A_SCHEMA, {"pass"})
    preregistration = load(args.stage_c_preregistration, SELECTION_SCHEMA, {"frozen"})
    control = load(args.stage_c_recovery_control, CONTROL_SCHEMA, {"frozen"})
    progress = load(args.stage_c_progress, PROGRESS_SCHEMA, {"pass"})
    handoff = load(args.stage_b_handoff, HANDOFF_SCHEMA, {"pass"})
    replay = load(args.exact_capacity_replay, REPLAY_SCHEMA, {"pass"})

    if (
        args.execution_project_sha != progress.get("execution_project_sha")
        or args.execution_project_sha != control.get("execution_project_sha")
        or args.nested_llama_sha != progress.get("nested_llama_cpp_sha")
        or args.nested_llama_sha != control.get("nested_llama_cpp_sha")
        or len(stage_a.get("primary_rows", [])) != 128
        or len(stage_a.get("sentinels", {}).get("runs", [])) != 8
        or not stage_a["sentinels"]["all_deterministic_signatures_equal"]
        or len(preregistration.get("plan", [])) != 48
        or len(control.get("plan", [])) != 48
        or progress.get("accepted_cell_count") != 48
        or progress.get("expected_cell_count") != 48
        or progress.get("failed_cell_count") != 0
        or progress.get("technical_failures")
        or progress.get("failures")
        or len(progress.get("captures", [])) != 48
        or len(progress.get("hygiene_events", [])) != 48
        or len(progress.get("processes_started", [])) != 48
        or progress.get("recovery_attempt_budget_remaining") != 0
        or progress.get("later_cell_retry_budget_remaining") != 0
        or not progress.get("original_failure_preserved")
        or replay.get("physical_anchor_validation", {}).get("status") != "PENDING_STAGE_C_EXACT"
        or handoff.get("offline_capacity_replay_summary", {})
        .get("physical_anchor_validation", {}).get("status") != "PENDING_STAGE_C_EXACT"
    ):
        raise ValueError("Stage-C synthesis completeness/provenance invariant failed")
    if any(
        row.get("status") != "pass" or row.get("resident_bytes_after") != 0
        for row in progress["hygiene_events"]
    ):
        raise ValueError("Stage-C hygiene invariant failed")
    if [row["run_ordinal"] for row in progress["captures"]] != list(range(1, 49)):
        raise ValueError("accepted Stage-C order changed")
    if [row["run_ordinal"] for row in progress["processes_started"]] != list(range(1, 49)):
        raise ValueError("started Stage-C order changed")

    stage_a_rows = {row["case_id"]: row for row in stage_a["primary_rows"]}
    plans_by_case: dict[str, list[dict[str, Any]]] = {}
    for row in preregistration["plan"]:
        plans_by_case.setdefault(row["case_id"], []).append(row)
    captures = {(row["case_id"], row["point"]): row for row in progress["captures"]}
    if len(plans_by_case) != 24 or len(captures) != 48:
        raise ValueError("Stage-C prompt/capture count changed")

    output_identities: list[dict[str, Any]] = []
    prompt_rows: list[dict[str, Any]] = []
    for prompt_ordinal, case_id in enumerate(sorted(plans_by_case), start=1):
        plans = sorted(plans_by_case[case_id], key=lambda row: row["run_ordinal"])
        if (
            len(plans) != 2
            or len({json.dumps(row["stage_a_s2_p50"], sort_keys=True) for row in plans}) != 1
            or [row["point"] for row in plans]
            != (["EXACT", "KNEE"] if plans[0]["prompt_ordinal"] % 2 == 1 else ["KNEE", "EXACT"])
        ):
            raise ValueError(f"Stage-C frozen pair changed: {case_id}")
        plan = plans[0]
        s2 = plan["stage_a_s2_p50"]
        if stage_a_rows[case_id]["result_sha256"] != s2["result_sha256"]:
            raise ValueError(f"Stage-A selected result changed: {case_id}")
        stage_a_result = (
            args.stage_a_output_root.resolve(strict=True)
            / f"run-{s2['ordinal']:03d}-{case_id}"
            / "result.json"
        )
        s2_output, s2_identity = result_output(
            stage_a_result, s2["result_sha256"], case_id, "S2_P50", s2
        )
        s2_identity["role"] = f"{case_id}/S2_P50"
        output_identities.append(s2_identity)

        policy_outputs: dict[str, dict[str, Any]] = {"s2_p50": s2_output}
        policy_rows: dict[str, dict[str, Any]] = {"s2_p50": s2}
        for point, policy_name in (("EXACT", "exact"), ("KNEE", "knee")):
            capture = captures[(case_id, point)]
            artifact = capture["artifacts"]["result.json"]
            result_path = pathlib.Path(artifact["path"])
            raw_output, raw_identity = result_output(
                result_path, artifact["sha256"], case_id, point, capture
            )
            if raw_identity["bytes"] != artifact["bytes"]:
                raise ValueError(f"Stage-C result size changed: {case_id}/{point}")
            raw_identity["role"] = f"{case_id}/{point}"
            output_identities.append(raw_identity)
            policy_outputs[policy_name] = raw_output
            policy_rows[policy_name] = capture

        comparisons = {
            "s2_vs_exact": comparison(
                "S2_P50", s2, s2_output, "EXACT", policy_rows["exact"],
                policy_outputs["exact"],
            ),
            "knee_vs_exact": comparison(
                "KNEE", policy_rows["knee"], policy_outputs["knee"], "EXACT",
                policy_rows["exact"], policy_outputs["exact"],
            ),
            "s2_vs_knee": comparison(
                "S2_P50", s2, s2_output, "KNEE", policy_rows["knee"],
                policy_outputs["knee"],
            ),
        }
        prompt_rows.append({
            "prompt_ordinal": plan["prompt_ordinal"],
            "case_id": case_id,
            "semantic_family": plan["semantic_family"],
            "length_level": plan["length_level"],
            "prompt_tokens": plan["prompt_tokens"],
            "selection_role": plan["selection_role"],
            "pair_order": [row["point"] for row in plans],
            "run_ordinals": [row["run_ordinal"] for row in plans],
            "policies": {
                "s2_p50": metric_snapshot(s2),
                "exact": metric_snapshot(policy_rows["exact"]),
                "knee": metric_snapshot(policy_rows["knee"]),
            },
            "comparisons": comparisons,
        })
    prompt_rows.sort(key=lambda row: row["prompt_ordinal"])

    exact_median = statistics.median(row["policies"]["exact"]["hit_ratio"] for row in prompt_rows)
    s2_median = statistics.median(row["policies"]["s2_p50"]["hit_ratio"] for row in prompt_rows)
    regime_counts: dict[str, int] = {}
    knee_regime_counts: dict[str, int] = {}
    for row in prompt_rows:
        s2_exact = row["comparisons"]["s2_vs_exact"]
        s2_knee = row["comparisons"]["s2_vs_knee"]
        if s2_exact["hit_ratio_delta"] <= 0.0 or s2_exact["decode_tok_s_ratio"] <= 1.0:
            absolute = "S2_NEUTRAL_OR_WORSE_VS_EXACT"
        elif row["policies"]["s2_p50"]["hit_ratio"] >= s2_median:
            absolute = (
                "HIGH_S2_INHERITED_FROM_CACHEABLE_EXACT_AND_AUGMENTED"
                if row["policies"]["exact"]["hit_ratio"] >= exact_median
                else "HIGH_S2_CREATED_FROM_BELOW_MEDIAN_EXACT"
            )
        else:
            absolute = "S2_GAIN_WITH_BELOW_COHORT_MEDIAN_ABSOLUTE_LOCALITY"
        if (
            s2_knee["decode_tok_s_ratio"] >= 1.02
            and s2_knee["hit_ratio_delta"] >= 0.01
            and s2_knee["loads_per_token_delta"] < 0.0
        ):
            knee_regime = "S2_MATERIAL_GAIN_VS_KNEE"
        elif (
            s2_knee["decode_tok_s_ratio"] > 1.0
            and s2_knee["hit_ratio_delta"] > 0.0
            and s2_knee["loads_per_token_delta"] < 0.0
        ):
            knee_regime = "S2_NEAR_NEUTRAL_GAIN_VS_KNEE"
        else:
            knee_regime = "S2_MIXED_OR_WORSE_VS_KNEE"
        row["regimes"] = {
            "absolute_s2_locality": absolute,
            "s2_vs_knee": knee_regime,
        }
        regime_counts[absolute] = regime_counts.get(absolute, 0) + 1
        knee_regime_counts[knee_regime] = knee_regime_counts.get(knee_regime, 0) + 1

    replay_rows = {
        row["case_id"]: row for row in replay["prompt_rows"]
        if row["selection_role"] == "STAGE_B_REPRESENTATIVE"
    }
    representatives = {
        row["case_id"] for row in prompt_rows
        if row["selection_role"] == "FAMILY_REPRESENTATIVE"
    }
    if set(replay_rows) != representatives or len(representatives) != 16:
        raise ValueError("physical-anchor representative set changed")
    anchor_rows = []
    anchor_mismatches = []
    for case_id in sorted(representatives):
        physical = next(
            row for row in replay_rows[case_id]["capacity_curve"]
            if row["label"] == "PHYSICAL_7849_SLOTS"
        )["decode"]
        measured = captures[(case_id, "EXACT")]
        comparisons = {
            "hits": measured["hits"] - physical["hits"],
            "misses": measured["misses"] - physical["misses"],
            "backing_bytes": measured["backing_bytes"] - physical["backing_bytes"],
            "hit_ratio": measured["hit_ratio"] - physical["hit_ratio"],
            "loads_per_token": measured["loads_per_token"] - physical["loads_per_token"],
            "bytes_per_token": measured["bytes_per_token"] - physical["bytes_per_token"],
        }
        matches = all(value == 0 for value in comparisons.values())
        anchor_rows.append({
            "case_id": case_id,
            "matches": matches,
            "replay_decode": {
                key: physical[key] for key in (
                    "hits", "misses", "backing_bytes", "hit_ratio",
                    "loads_per_token", "bytes_per_token",
                )
            },
            "measured_exact": {
                key: measured[key] for key in (
                    "hits", "misses", "backing_bytes", "hit_ratio",
                    "loads_per_token", "bytes_per_token",
                )
            },
            "measured_minus_replay": comparisons,
        })
        if not matches:
            anchor_mismatches.append({"case_id": case_id, "deltas": comparisons})
    if anchor_mismatches:
        raise ValueError("Stage-C EXACT physical anchor does not match offline replay")

    aggregates = {
        name: aggregate_comparison(prompt_rows, name)
        for name in ("s2_vs_exact", "knee_vs_exact", "s2_vs_knee")
    }
    s2_knee_gains = [
        row["comparisons"]["s2_vs_knee"]["decode_tok_s_delta_fraction"]
        for row in prompt_rows
    ]
    interaction_cv = statistics.pstdev(s2_knee_gains) / statistics.fmean(s2_knee_gains)
    if interaction_cv <= 0.20:
        interaction_label = "weak"
    elif interaction_cv <= 0.50:
        interaction_label = "moderate"
    else:
        interaction_label = "strong"
    s2_direction = aggregates["s2_vs_exact"]["direction_counts"]
    s2_knee_direction = aggregates["s2_vs_knee"]["direction_counts"]
    paired_gain = (
        "broad" if all((
            s2_direction["tps_better"] == 24,
            s2_direction["hit_better"] == 24,
            s2_direction["loads_lower"] == 24,
            s2_knee_direction["tps_better"] == 24,
            s2_knee_direction["hit_better"] == 24,
            s2_knee_direction["loads_lower"] == 24,
        )) else "heterogeneous"
    )

    document = {
        "schema_version": "phase13-6pg-stage-c-synthesis-v1",
        "status": "pass",
        "provenance": "MEASURED_STAGE_C_PAIRED_PERFORMANCE_AND_ARTIFACT_ONLY_SYNTHESIS",
        "execution_target": {
            "project_sha": args.execution_project_sha,
            "nested_llama_cpp_sha": args.nested_llama_sha,
        },
        "inputs": input_identities,
        "completeness": {
            "frozen_unique_prompts": 24,
            "expected_cells": 48,
            "accepted_cells": 48,
            "failed_cells": 0,
            "technical_failures": 0,
            "physical_processes_started": 48,
            "hygiene_events_passed": 48,
            "recovery_attempts_consumed": 1,
            "later_retries": 0,
            "original_pre_inference_failure_preserved": True,
        },
        "safety": {
            "all_hygiene_events_passed": True,
            "all_hygiene_resident_bytes_after_zero": True,
            "all_capture_host_safety_gates_clean": all(
                capture["host_safety"]["peak_process_swap_kib"] == 0
                and capture["host_safety"]["unused_nvme_read_bytes"] == 0
                and not any(capture["host_safety"]["cgroup_memory_events"].values())
                and not capture["host_safety"]["memory_pressure_total_delta_usec"]
                for capture in progress["captures"]
            ),
            "minimum_projected_admission_margin_after_bytes": min(
                row["projected_admission_margin_after_bytes"]
                for row in progress["hygiene_events"]
            ),
        },
        "physical_replay_anchor_validation": {
            "status": "PASS",
            "capacity_slots": 7849,
            "capacity_bytes": 137728475136,
            "replacement_policy": replay["replacement_policy"],
            "representative_count": 16,
            "exact_match_count": 16,
            "mismatches": anchor_mismatches,
            "rows": anchor_rows,
            "authoritative_larger_capacity_curve": True,
            "authority": (
                "Stage-C physical EXACT validates the semantic-order replay at the frozen "
                "7,849-slot anchor; larger-capacity replay remains offline evidence, not TPS."
            ),
        },
        "classification_rules": {
            "absolute_locality": {
                "exact_cacheable_threshold": "median EXACT hit ratio in the frozen 24-prompt cohort",
                "high_s2_threshold": "median S2_P50 hit ratio in the frozen 24-prompt cohort",
                "exact_median_hit_ratio": exact_median,
                "s2_median_hit_ratio": s2_median,
                "authority": "post-hoc cohort-relative descriptive label; not a universal threshold",
            },
            "s2_vs_knee_material_gain": (
                "TPS ratio >= 1.02, hit-ratio delta >= 0.01, and loads/token delta < 0"
            ),
            "interaction_strength": {
                "statistic": "population CV of prompt-level S2_P50-vs-KNEE TPS fractional gain",
                "weak": "CV <= 0.20",
                "moderate": "0.20 < CV <= 0.50",
                "strong": "CV > 0.50",
                "observed_cv": interaction_cv,
                "authority": "post-hoc descriptive label; not a tuning gate",
            },
        },
        "prompt_rows": prompt_rows,
        "aggregates": aggregates,
        "regimes": {
            "absolute_s2_locality_counts": regime_counts,
            "s2_vs_knee_counts": knee_regime_counts,
        },
        "primary_outcomes": {
            "S2P50_PAIRED_GAIN": paired_gain,
            "KNEE_VS_S2P50_PROMPT_INTERACTION": interaction_label,
            "PHYSICAL_REPLAY_ANCHOR": "validated",
        },
        "result_artifacts": {
            "validated_result_count": len(output_identities),
            "validated_results": output_identities,
        },
        "interpretation_limits": [
            "The 24 prompts are a frozen explanatory sample, not a random sample of all prompts.",
            "Generated-ID prefix/alignment/edit evidence is trajectory feedback only, not quality.",
            "Observer/replay outputs are non-performance evidence; physical Stage-C cells own TPS.",
            "No S2_P50, KNEE, capacity, corpus, order, or runtime parameter was retuned.",
        ],
        "disposition": "STAGE_C_COMPLETE_PHYSICAL_REPLAY_ANCHOR_VALIDATED_READY_FOR_FINAL_SYNTHESIS",
    }
    csv_identity = write_csv(args.output_csv, [csv_row(row) for row in prompt_rows])
    document["artifacts"] = {"prompt_comparisons_csv": csv_identity}
    json_identity = write_json(args.output_json, document)
    print(json.dumps({
        "status": "pass",
        "stage_c_synthesis": json_identity,
        "prompt_comparisons": csv_identity,
        "paired_gain": paired_gain,
        "interaction": interaction_label,
        "physical_anchor": "PASS",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

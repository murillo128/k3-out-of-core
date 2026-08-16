#!/usr/bin/env python3
"""Build deterministic issue-102 Stage-A artifact-only post-hoc analyses."""

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


ALPHA = 0.05


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--expected-checkpoint-sha256", required=True)
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
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
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


def average_ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    result = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            result[order[position]] = rank
        start = end
    return result


def pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((lhs - left_mean) * (rhs - right_mean) for lhs, rhs in zip(left, right))
    left_ss = sum((value - left_mean) ** 2 for value in left)
    right_ss = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else None


def association(left: Sequence[float], right: Sequence[float]) -> dict[str, Any]:
    return {
        "count": len(left),
        "pearson_r": pearson(left, right),
        "spearman_rho": pearson(average_ranks(left), average_ranks(right)),
    }


def solve(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [matrix[row][:] + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            raise ValueError("singular design matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                lhs - factor * rhs for lhs, rhs in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def ols(design: Sequence[Sequence[float]], outcome: Sequence[float]) -> dict[str, Any]:
    if not design or len(design) != len(outcome):
        raise ValueError("invalid OLS inputs")
    width = len(design[0])
    if any(len(row) != width for row in design):
        raise ValueError("ragged OLS design")
    xtx = [[sum(row[left] * row[right] for row in design) for right in range(width)]
           for left in range(width)]
    xty = [sum(row[column] * value for row, value in zip(design, outcome))
           for column in range(width)]
    coefficients = solve(xtx, xty)
    fitted = [sum(value * coefficient for value, coefficient in zip(row, coefficients))
              for row in design]
    residuals = [actual - predicted for actual, predicted in zip(outcome, fitted)]
    outcome_mean = statistics.fmean(outcome)
    sse = sum(value * value for value in residuals)
    sst = sum((value - outcome_mean) ** 2 for value in outcome)
    return {
        "coefficients": coefficients,
        "fitted": fitted,
        "residuals": residuals,
        "r_squared": 1.0 - sse / sst if sst else None,
        "adjusted_r_squared": (
            1.0 - (sse / (len(outcome) - width)) / (sst / (len(outcome) - 1))
            if sst and len(outcome) > width else None
        ),
        "sse": sse,
        "degrees_of_freedom": len(outcome) - width,
    }


def regularized_beta(value: float, alpha: float, beta: float) -> float:
    if not 0.0 <= value <= 1.0:
        raise ValueError("beta input outside [0, 1]")
    if value in (0.0, 1.0):
        return value

    def fraction(a: float, b: float, x: float) -> float:
        maximum_iterations = 200
        epsilon = 3e-14
        floor = 1e-300
        qab = a + b
        qap = a + 1.0
        qam = a - 1.0
        c = 1.0
        d = 1.0 - qab * x / qap
        if abs(d) < floor:
            d = floor
        d = 1.0 / d
        result = d
        for iteration in range(1, maximum_iterations + 1):
            even = 2 * iteration
            numerator = iteration * (b - iteration) * x / ((qam + even) * (a + even))
            d = 1.0 + numerator * d
            if abs(d) < floor:
                d = floor
            c = 1.0 + numerator / c
            if abs(c) < floor:
                c = floor
            d = 1.0 / d
            result *= d * c
            numerator = -(a + iteration) * (qab + iteration) * x / (
                (a + even) * (qap + even)
            )
            d = 1.0 + numerator * d
            if abs(d) < floor:
                d = floor
            c = 1.0 + numerator / c
            if abs(c) < floor:
                c = floor
            d = 1.0 / d
            delta = d * c
            result *= delta
            if abs(delta - 1.0) < epsilon:
                return result
        raise ValueError("incomplete beta did not converge")

    factor = math.exp(
        math.lgamma(alpha + beta) - math.lgamma(alpha) - math.lgamma(beta)
        + alpha * math.log(value) + beta * math.log1p(-value)
    )
    if value < (alpha + 1.0) / (alpha + beta + 2.0):
        return factor * fraction(alpha, beta, value) / alpha
    return 1.0 - factor * fraction(beta, alpha, 1.0 - value) / beta


def student_t_two_sided_p(t_value: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("non-positive t degrees of freedom")
    x = degrees_of_freedom / (degrees_of_freedom + t_value * t_value)
    return regularized_beta(x, degrees_of_freedom / 2.0, 0.5)


def student_t_critical(degrees_of_freedom: int, alpha: float = ALPHA) -> float:
    low = 0.0
    high = 20.0
    for _ in range(100):
        midpoint = (low + high) / 2.0
        if student_t_two_sided_p(midpoint, degrees_of_freedom) > alpha:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def simple_slope(rows: Sequence[dict[str, Any]], x_key: str, y_key: str) -> dict[str, Any]:
    x = [float(row[x_key]) / 100.0 for row in rows]
    y = [float(row[y_key]) for row in rows]
    fit = ols([[1.0, value] for value in x], y)
    df = fit["degrees_of_freedom"]
    centered_ss = sum((value - statistics.fmean(x)) ** 2 for value in x)
    standard_error = math.sqrt((fit["sse"] / df) / centered_ss)
    slope = fit["coefficients"][1]
    critical = student_t_critical(df)
    return {
        "count": len(rows),
        "slope_per_100_prompt_tokens": slope,
        "standard_error": standard_error,
        "confidence_interval_95": [slope - critical * standard_error, slope + critical * standard_error],
        "t_statistic": slope / standard_error if standard_error else None,
        "raw_p_value": (
            student_t_two_sided_p(slope / standard_error, df) if standard_error else 0.0
        ),
        "r_squared": fit["r_squared"],
    }


def benjamini_hochberg(items: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(items), key=lambda pair: pair[1]["raw_p_value"])
    count = len(ordered)
    adjusted = [0.0] * count
    running = 1.0
    for reverse_rank in range(count - 1, -1, -1):
        original_index, item = ordered[reverse_rank]
        rank = reverse_rank + 1
        running = min(running, item["raw_p_value"] * count / rank)
        adjusted[original_index] = min(1.0, running)
    for item, value in zip(items, adjusted):
        item["bh_adjusted_p_value"] = value
        item["bh_q_0_05_reject"] = value <= 0.05


def model_summary(rows: Sequence[dict[str, Any]], outcome: str, predictors: list[str]) -> dict[str, Any]:
    families = sorted({row["semantic_family"] for row in rows})
    design = []
    names = ["intercept"]
    if "family" in predictors:
        names.extend(f"family[{family}]" for family in families[1:])
    if "length_level" in predictors:
        names.append("length_level")
    if "templated_prompt_tokens" in predictors:
        names.append("templated_prompt_tokens")
    if "within_family_centered_tokens" in predictors:
        names.append("within_family_centered_tokens")
    for row in rows:
        values = [1.0]
        if "family" in predictors:
            values.extend(float(row["semantic_family"] == family) for family in families[1:])
        for predictor in ("length_level", "templated_prompt_tokens", "within_family_centered_tokens"):
            if predictor in predictors:
                values.append(float(row[predictor]))
        design.append(values)
    fit = ols(design, [float(row[outcome]) for row in rows])
    return {
        "outcome": outcome,
        "predictors": predictors,
        "coefficient_names": names,
        "coefficients": dict(zip(names, fit["coefficients"])),
        "r_squared": fit["r_squared"],
        "adjusted_r_squared": fit["adjusted_r_squared"],
        "degrees_of_freedom": fit["degrees_of_freedom"],
    }


def grouped_summary(rows: Sequence[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    outputs = []
    for value in sorted({row[key] for row in rows}):
        group = [row for row in rows if row[key] == value]
        outputs.append({
            key: value,
            "count": len(group),
            "templated_prompt_tokens": distribution(row["templated_prompt_tokens"] for row in group),
            "hit_ratio": distribution(row["hit_ratio"] for row in group),
            "decode_tok_s": distribution(row["decode_tok_s"] for row in group),
        })
    return outputs


def build_family_length(
    rows: list[dict[str, Any]], checkpoint: dict[str, Any], checkpoint_path: pathlib.Path
) -> dict[str, Any]:
    slopes: dict[str, list[dict[str, Any]]] = {"hit_ratio": [], "decode_tok_s": []}
    for outcome in slopes:
        for family in sorted({row["semantic_family"] for row in rows}):
            result = simple_slope(
                [row for row in rows if row["semantic_family"] == family],
                "templated_prompt_tokens",
                outcome,
            )
            result["semantic_family"] = family
            slopes[outcome].append(result)
        benjamini_hochberg(slopes[outcome])

    models = {}
    specifications = {
        "family_only": ["family"],
        "length_level_only": ["length_level"],
        "actual_token_only": ["templated_prompt_tokens"],
        "family_plus_length_level": ["family", "length_level"],
        "family_plus_actual_tokens": ["family", "templated_prompt_tokens"],
        "within_family_centered_actual_tokens": ["within_family_centered_tokens"],
    }
    for outcome in ("hit_ratio", "decode_tok_s"):
        models[outcome] = {
            name: model_summary(rows, outcome, predictors)
            for name, predictors in specifications.items()
        }

    tokens = [float(row["templated_prompt_tokens"]) for row in rows]
    return {
        "schema_version": "phase13-6pg-stage-a-family-length-analysis-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "STAGE_A_ARTIFACT_ONLY"],
        "performance_evidence": True,
        "inputs": {
            "analysis_script": identity(pathlib.Path(__file__)),
            "stage_a_checkpoint": identity(checkpoint_path),
            "stage_a_checkpoint_schema": checkpoint["schema_version"],
            "row_json_pointer": "/primary_rows",
        },
        "population": {
            "row_count": len(rows),
            "family_count": len({row["semantic_family"] for row in rows}),
            "length_levels": sorted({row["length_level"] for row in rows}),
            "sentinel_count": len(checkpoint["sentinels"]["runs"]),
            "sentinel_status": checkpoint["sentinels"]["status"],
        },
        "global_actual_token_associations": {
            "hit_ratio": association(tokens, [row["hit_ratio"] for row in rows]),
            "decode_tok_s": association(tokens, [row["decode_tok_s"] for row in rows]),
        },
        "per_family": grouped_summary(rows, "semantic_family"),
        "per_length_level": grouped_summary(rows, "length_level"),
        "descriptive_ols": models,
        "per_family_actual_token_slopes": slopes,
        "multiple_testing": {
            "method": "Benjamini-Hochberg",
            "family": "16 semantic-family slope tests separately for each outcome",
            "q": 0.05,
            "raw_p_values_retained": True,
        },
        "interpretation_limits": [
            "Post-hoc exploratory analysis; associations are not causal.",
            "Each family has eight prompts, so family-specific slope uncertainty is substantial.",
            "Stage-A physical results are not replaced or refit by observer evidence.",
        ],
        "disposition": "STAGE_A_FAMILY_LENGTH_POST_HOC_COMPLETE",
    }


def calibration_model(rows: Sequence[dict[str, Any]], predictor: str) -> tuple[dict[str, Any], list[float]]:
    fit = ols(
        [[1.0, float(row[predictor])] for row in rows],
        [float(row["decode_tok_s"]) for row in rows],
    )
    residuals = fit["residuals"]
    return ({
        "formula": f"decode_tok_s ~ {predictor}",
        "count": len(rows),
        "intercept": fit["coefficients"][0],
        "slope": fit["coefficients"][1],
        "r_squared": fit["r_squared"],
        "adjusted_r_squared": fit["adjusted_r_squared"],
        "mae": statistics.fmean(abs(value) for value in residuals),
        "rmse": math.sqrt(statistics.fmean(value * value for value in residuals)),
        "residual_distribution": distribution(residuals),
    }, residuals)


def leave_one_family_out(rows: Sequence[dict[str, Any]], predictor: str) -> dict[str, Any]:
    outputs = []
    all_errors = []
    for family in sorted({row["semantic_family"] for row in rows}):
        train = [row for row in rows if row["semantic_family"] != family]
        held = [row for row in rows if row["semantic_family"] == family]
        fit = ols(
            [[1.0, float(row[predictor])] for row in train],
            [float(row["decode_tok_s"]) for row in train],
        )
        errors = [
            float(row["decode_tok_s"])
            - (fit["coefficients"][0] + fit["coefficients"][1] * float(row[predictor]))
            for row in held
        ]
        all_errors.extend(errors)
        outputs.append({
            "held_out_family": family,
            "training_row_count": len(train),
            "held_out_row_count": len(held),
            "training_intercept": fit["coefficients"][0],
            "training_slope": fit["coefficients"][1],
            "held_out_mae": statistics.fmean(abs(value) for value in errors),
            "held_out_rmse": math.sqrt(statistics.fmean(value * value for value in errors)),
            "held_out_residual_distribution": distribution(errors),
        })
    return {
        "predictor": predictor,
        "fold_count": len(outputs),
        "training_families_per_fold": len(outputs) - 1,
        "folds": outputs,
        "pooled_held_out_mae": statistics.fmean(abs(value) for value in all_errors),
        "pooled_held_out_rmse": math.sqrt(statistics.fmean(value * value for value in all_errors)),
        "pooled_held_out_residual_distribution": distribution(all_errors),
    }


def residual_audit(rows: Sequence[dict[str, Any]], residuals: Sequence[float]) -> dict[str, Any]:
    numeric = [
        "templated_prompt_tokens",
        "length_level",
        "changed_fraction",
        "swaps_per_token",
        "cumulative_score_regret",
    ]
    by_family = []
    for family in sorted({row["semantic_family"] for row in rows}):
        values = [
            residual for row, residual in zip(rows, residuals)
            if row["semantic_family"] == family
        ]
        by_family.append({"semantic_family": family, "residual": distribution(values)})
    return {
        "numeric_associations": {
            key: association([float(row[key]) for row in rows], list(residuals))
            for key in numeric
        },
        "per_family": by_family,
    }


def build_calibration(
    rows: list[dict[str, Any]], checkpoint: dict[str, Any], checkpoint_path: pathlib.Path
) -> dict[str, Any]:
    models = {}
    residuals = {}
    for predictor in ("hit_ratio", "loads_per_token"):
        models[predictor], residuals[predictor] = calibration_model(rows, predictor)
    return {
        "schema_version": "phase13-6pg-locality-throughput-calibration-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "STAGE_A_ARTIFACT_ONLY"],
        "performance_evidence": True,
        "inputs": {
            "analysis_script": identity(pathlib.Path(__file__)),
            "stage_a_checkpoint": identity(checkpoint_path),
            "stage_a_checkpoint_schema": checkpoint["schema_version"],
            "row_json_pointer": "/primary_rows",
        },
        "models": models,
        "leave_one_family_out": {
            predictor: leave_one_family_out(rows, predictor)
            for predictor in ("hit_ratio", "loads_per_token")
        },
        "residual_audit": {
            predictor: residual_audit(rows, residuals[predictor])
            for predictor in ("hit_ratio", "loads_per_token")
        },
        "interpretation_limits": [
            "Calibration is descriptive and post-hoc; it does not establish causal throughput effects.",
            "Leave-one-family-out estimates transfer across the 16 frozen families only.",
            "No observer timing is consumed or interpreted.",
        ],
        "disposition": "LOCALITY_THROUGHPUT_CALIBRATION_COMPLETE",
    }


def write_csv(path: pathlib.Path, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    columns = [
        "ordinal", "case_id", "semantic_family", "length_level", "templated_prompt_tokens",
        "family_mean_prompt_tokens", "within_family_centered_tokens", "hit_ratio",
        "decode_tok_s", "loads_per_token", "changed_fraction", "swaps_per_token",
        "cumulative_score_regret", "result_sha256", "envelope_sha256",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)
    return identity(path)


def validate_and_normalize(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    if checkpoint.get("status") != "pass":
        raise ValueError("Stage-A checkpoint is not pass")
    rows = sorted(checkpoint["primary_rows"], key=lambda row: row["ordinal"])
    families = sorted({row["semantic_family"] for row in rows})
    if len(rows) != 128 or len(families) != 16 or [row["ordinal"] for row in rows] != list(range(1, 129)):
        raise ValueError("Stage-A population is not the frozen 128-case set")
    for family in families:
        group = [row for row in rows if row["semantic_family"] == family]
        if sorted(row["length_level"] for row in group) != list(range(1, 9)):
            raise ValueError(f"family grid changed: {family}")
        mean_tokens = statistics.fmean(row["templated_prompt_tokens"] for row in group)
        for row in group:
            row["family_mean_prompt_tokens"] = mean_tokens
            row["within_family_centered_tokens"] = row["templated_prompt_tokens"] - mean_tokens
    return rows


def main() -> None:
    args = arguments()
    checkpoint_sha = sha256(args.checkpoint)
    if checkpoint_sha != args.expected_checkpoint_sha256:
        raise ValueError(
            f"checkpoint SHA changed: expected {args.expected_checkpoint_sha256}, got {checkpoint_sha}"
        )
    with args.checkpoint.open() as stream:
        checkpoint = json.load(stream)
    rows = validate_and_normalize(checkpoint)
    family_json = write_json(
        args.output_root / "stage-a-family-length-analysis.json",
        build_family_length(rows, checkpoint, args.checkpoint),
    )
    family_csv = write_csv(args.output_root / "stage-a-family-length-analysis.csv", rows)
    calibration = write_json(
        args.output_root / "locality-throughput-calibration.json",
        build_calibration(rows, checkpoint, args.checkpoint),
    )
    index = write_json(args.output_root / "stage-a-posthoc-analysis-index.json", {
        "schema_version": "phase13-6pg-stage-a-posthoc-analysis-index-v1",
        "status": "pass",
        "provenance": ["POST_HOC_EXPLORATORY", "STAGE_A_ARTIFACT_ONLY"],
        "performance_evidence": True,
        "inputs": {
            "analysis_script": identity(pathlib.Path(__file__)),
            "stage_a_checkpoint": identity(args.checkpoint),
        },
        "artifacts": {
            "family_length_json": family_json,
            "family_length_csv": family_csv,
            "locality_throughput_calibration": calibration,
        },
        "model_execution": "NONE_ARTIFACT_ONLY",
        "disposition": "STAGE_A_POST_HOC_ANALYSIS_COMPLETE",
    })
    print(json.dumps({"status": "pass", "index": index}, sort_keys=True))


if __name__ == "__main__":
    main()

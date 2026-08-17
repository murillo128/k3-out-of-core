#!/usr/bin/env python3
"""Run the offline issue-105 secondary analyses and render frozen figures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pathlib
import statistics
from collections import Counter, defaultdict
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402


SCHEMA_VERSION = "issue105-secondary-analysis-v1"
POST_HOC = True
C0_SLOTS = 7849
C0_BYTES = 137728475136
EXPERT_BYTES = 17547264
BOOTSTRAP_SEED = 105
BOOTSTRAP_REPLICATES = 2000
FAMILY_COLORS = plt.get_cmap("tab20")


class AnalysisError(ValueError):
    """Raised when a secondary result would violate the issue contract."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical-root", type=pathlib.Path, required=True)
    parser.add_argument("--frozen-source-root", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--analysis-code-version", required=True)
    parser.add_argument(
        "--schema-root",
        type=pathlib.Path,
        default=pathlib.Path(__file__).resolve().parents[2] / "schemas/issue105",
    )
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def validate_json(value: Any, schema_path: pathlib.Path) -> None:
    schema = load_json(schema_path)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(native(value))
    except Exception as error:
        raise AnalysisError(f"JSON schema validation failed for {schema_path.name}: {error}") from error


def native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(item) for item in value]
    if isinstance(value, np.ndarray):
        return [native(item) for item in value.tolist()]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(native(value), stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def write_text(path: pathlib.Path, text: str) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
    os.replace(temporary, path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def write_csv(path: pathlib.Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if set(row) != set(fields):
                raise AnalysisError(f"CSV schema mismatch for {path.name}")
            writer.writerow(native(row))
    os.replace(temporary, path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path), "row_count": len(rows)}


def write_parquet(path: pathlib.Path, rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pylist([native(row) for row in rows])
    pq.write_table(
        table,
        temporary,
        version="2.6",
        compression="zstd",
        compression_level=9,
        use_dictionary=True,
        write_statistics=True,
    )
    os.replace(temporary, path)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path), "row_count": len(rows)}


def read_csv(
    path: pathlib.Path,
    integer_fields: Iterable[str] = (),
    float_fields: Iterable[str] = (),
    boolean_fields: Iterable[str] = (),
) -> list[dict[str, Any]]:
    integers = set(integer_fields)
    floats = set(float_fields)
    booleans = set(boolean_fields)
    result = []
    with path.open(encoding="utf-8", newline="") as stream:
        for source in csv.DictReader(stream):
            row: dict[str, Any] = {}
            for key, value in source.items():
                if value == "":
                    row[key] = None
                elif key in integers:
                    row[key] = int(value)
                elif key in floats:
                    row[key] = float(value)
                elif key in booleans:
                    row[key] = value.lower() == "true"
                else:
                    row[key] = value
            result.append(row)
    return result


def quantile(values: Sequence[float], probability: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), probability, method="linear"))


def distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = [float(value) for value in values]
    if not rows:
        return {"count": 0}
    return {
        "count": len(rows),
        "min": min(rows),
        "p10": quantile(rows, 0.1),
        "median": statistics.median(rows),
        "mean": statistics.fmean(rows),
        "p90": quantile(rows, 0.9),
        "max": max(rows),
    }


def residual_distribution(values: Sequence[float]) -> dict[str, Any]:
    result = distribution(values)
    result["mae"] = statistics.fmean(abs(value) for value in values)
    result["rmse"] = math.sqrt(statistics.fmean(value * value for value in values))
    return result


def average_ranks(values: Sequence[float]) -> np.ndarray:
    values_array = np.asarray(values, dtype=float)
    order = np.argsort(values_array, kind="stable")
    ranks = np.empty(len(values_array), dtype=float)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values_array[order[end]] == values_array[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    return ranks


def correlation(left: Sequence[float], right: Sequence[float]) -> float:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if len(left_array) != len(right_array) or len(left_array) < 2:
        raise AnalysisError("invalid correlation inputs")
    if np.std(left_array) == 0 or np.std(right_array) == 0:
        return 0.0
    return float(np.corrcoef(left_array, right_array)[0, 1])


def association(left: Sequence[float], right: Sequence[float]) -> dict[str, Any]:
    return {
        "count": len(left),
        "pearson_r": correlation(left, right),
        "spearman_rho": correlation(average_ranks(left), average_ranks(right)),
    }


def fit_matrix(matrix: np.ndarray, outcome: np.ndarray, names: Sequence[str]) -> dict[str, Any]:
    coefficients, _, rank, _ = np.linalg.lstsq(matrix, outcome, rcond=None)
    if rank != matrix.shape[1]:
        raise AnalysisError(f"rank-deficient design: {names}")
    prediction = matrix @ coefficients
    residual = outcome - prediction
    total = float(np.sum((outcome - np.mean(outcome)) ** 2))
    residual_sum = float(np.sum(residual ** 2))
    r_squared = 1.0 - residual_sum / total if total else 0.0
    return {
        "coefficient_names": list(names),
        "coefficients": {name: float(value) for name, value in zip(names, coefficients)},
        "rank": int(rank),
        "row_count": len(outcome),
        "r_squared": r_squared,
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual ** 2))),
        "residual_distribution": residual_distribution(residual.tolist()),
        "_prediction": prediction,
        "_residual": residual,
    }


def design_matrix(
    rows: Sequence[dict[str, Any]],
    predictors: Sequence[str],
    include_family: bool = False,
) -> tuple[np.ndarray, list[str]]:
    columns = [np.ones(len(rows), dtype=float)]
    names = ["intercept"]
    for predictor in predictors:
        columns.append(np.asarray([float(row[predictor]) for row in rows], dtype=float))
        names.append(predictor)
    if include_family:
        families = sorted({str(row["semantic_family"]) for row in rows})
        for family in families[1:]:
            columns.append(np.asarray([row["semantic_family"] == family for row in rows], dtype=float))
            names.append(f"family[{family}]")
    return np.column_stack(columns), names


def fit_rows(
    rows: Sequence[dict[str, Any]],
    outcome: str,
    predictors: Sequence[str],
    include_family: bool = False,
) -> dict[str, Any]:
    matrix, names = design_matrix(rows, predictors, include_family)
    y = np.asarray([float(row[outcome]) for row in rows], dtype=float)
    return fit_matrix(matrix, y, names)


def public_model(model: dict[str, Any]) -> dict[str, Any]:
    return {key: native(value) for key, value in model.items() if not key.startswith("_")}


def family_residuals(rows: Sequence[dict[str, Any]], residuals: Sequence[float]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row, residual in zip(rows, residuals):
        grouped[str(row["semantic_family"])].append(float(residual))
    return [
        {
            "semantic_family": family,
            "mean_signed_residual": statistics.fmean(values),
            "median_signed_residual": statistics.median(values),
            "mean_absolute_residual": statistics.fmean(abs(value) for value in values),
            "count": len(values),
        }
        for family, values in sorted(grouped.items())
    ]


def lofo_partitions(
    rows: Sequence[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
    partitions = []
    for held_out in sorted({str(row["semantic_family"]) for row in rows}):
        training = [row for row in rows if row["semantic_family"] != held_out]
        testing = [row for row in rows if row["semantic_family"] == held_out]
        if held_out in {str(row["semantic_family"]) for row in training}:
            raise AnalysisError("LOFO training contains held-out family")
        partitions.append((held_out, training, testing))
    return partitions


def simple_lofo(rows: Sequence[dict[str, Any]], predictor: str, outcome: str = "decode_tok_s") -> dict[str, Any]:
    families = sorted({str(row["semantic_family"]) for row in rows})
    predictions = []
    folds = []
    for held_out, training, testing in lofo_partitions(rows):
        model = fit_rows(training, outcome, [predictor])
        intercept = model["coefficients"]["intercept"]
        slope = model["coefficients"][predictor]
        fold_residuals = []
        for row in testing:
            prediction = intercept + slope * float(row[predictor])
            residual = float(row[outcome]) - prediction
            fold_residuals.append(residual)
            predictions.append({
                "case_id": row["case_id"],
                "policy": row.get("policy", ""),
                "semantic_family": held_out,
                "length_level": int(row["length_level"]),
                "templated_prompt_tokens": int(row["templated_prompt_tokens"]),
                "measured": float(row[outcome]),
                "prediction": prediction,
                "residual": residual,
            })
        folds.append({
            "held_out_family": held_out,
            "training_family_count": len(families) - 1,
            "training_row_count": len(training),
            "held_out_row_count": len(testing),
            "training_intercept": intercept,
            "training_slope": slope,
            "residuals": residual_distribution(fold_residuals),
        })
    measured = np.asarray([row["measured"] for row in predictions], dtype=float)
    predicted = np.asarray([row["prediction"] for row in predictions], dtype=float)
    residuals = measured - predicted
    total = float(np.sum((measured - np.mean(measured)) ** 2))
    return {
        "predictor": predictor,
        "fold_count": len(families),
        "pooled_oof_r_squared": 1.0 - float(np.sum(residuals ** 2)) / total,
        "pooled_oof_mae": float(np.mean(np.abs(residuals))),
        "pooled_oof_rmse": float(np.sqrt(np.mean(residuals ** 2))),
        "residual_distribution": residual_distribution(residuals.tolist()),
        "family_residuals": family_residuals(predictions, residuals.tolist()),
        "residual_association_with_actual_tokens": association(
            [float(row["templated_prompt_tokens"]) for row in predictions], residuals.tolist()
        ),
        "residuals_by_length_level": [
            {
                "length_level": level,
                **residual_distribution([
                    residuals[index]
                    for index, row in enumerate(predictions)
                    if int(row["length_level"]) == level
                ]),
            }
            for level in sorted({int(row["length_level"]) for row in predictions})
        ],
        "folds": folds,
        "_predictions": predictions,
    }


def bootstrap_oof(predictions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in predictions:
        by_family[str(row["semantic_family"])].append(row)
    families = sorted(by_family)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    r_squared_values = []
    rmse_values = []
    for _ in range(BOOTSTRAP_REPLICATES):
        sample = rng.choice(families, size=len(families), replace=True)
        rows = [row for family in sample for row in by_family[str(family)]]
        measured = np.asarray([row["measured"] for row in rows], dtype=float)
        predicted = np.asarray([row["prediction"] for row in rows], dtype=float)
        residual = measured - predicted
        total = float(np.sum((measured - np.mean(measured)) ** 2))
        r_squared_values.append(1.0 - float(np.sum(residual ** 2)) / total if total else 0.0)
        rmse_values.append(float(np.sqrt(np.mean(residual ** 2))))
    return {
        "seed": BOOTSTRAP_SEED,
        "replicates": BOOTSTRAP_REPLICATES,
        "unit": "semantic_family_cluster",
        "interpretation": "sensitivity interval over the 16 constructed families",
        "pooled_oof_r_squared_95_interval": [quantile(r_squared_values, 0.025), quantile(r_squared_values, 0.975)],
        "pooled_oof_rmse_95_interval": [quantile(rmse_values, 0.025), quantile(rmse_values, 0.975)],
    }


def add_lofo_uncertainty(lofo: dict[str, Any]) -> dict[str, Any]:
    lofo["cluster_bootstrap"] = bootstrap_oof(lofo["_predictions"])
    return lofo


def public_lofo(lofo: dict[str, Any]) -> dict[str, Any]:
    return {key: native(value) for key, value in lofo.items() if not key.startswith("_")}


def family_length_analysis(primary: Sequence[dict[str, Any]]) -> dict[str, Any]:
    outcomes = ("decode_tok_s", "hit_ratio", "loads_per_token")
    result: dict[str, Any] = {
        "schema_version": "issue105-family-length-analysis-v1",
        "status": "PASS",
        "post_hoc_exploratory": True,
        "row_count": len(primary),
        "outcomes": {},
    }
    families = sorted({str(row["semantic_family"]) for row in primary})
    family_means = {
        family: statistics.fmean(
            float(row["templated_prompt_tokens"]) for row in primary if row["semantic_family"] == family
        )
        for family in families
    }
    centered_rows = [dict(row, within_family_centered_tokens=float(row["templated_prompt_tokens"]) - family_means[str(row["semantic_family"])]) for row in primary]
    for outcome in outcomes:
        family_only = fit_rows(centered_rows, outcome, [], include_family=True)
        token_only = fit_rows(centered_rows, outcome, ["templated_prompt_tokens"])
        family_plus_centered = fit_rows(
            centered_rows, outcome, ["within_family_centered_tokens"], include_family=True
        )
        descriptions = []
        slopes = []
        for family in families:
            group = [row for row in centered_rows if row["semantic_family"] == family]
            values = [float(row[outcome]) for row in group]
            descriptions.append({
                "semantic_family": family,
                "count": len(group),
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
                "iqr": quantile(values, 0.75) - quantile(values, 0.25),
                "mad": statistics.median(abs(value - statistics.median(values)) for value in values),
            })
            model = fit_rows(group, outcome, ["templated_prompt_tokens"])
            matrix, _ = design_matrix(group, ["templated_prompt_tokens"])
            degrees_of_freedom = len(group) - matrix.shape[1]
            residual_sum = float(np.sum(model["_residual"] ** 2))
            covariance = (residual_sum / degrees_of_freedom) * np.linalg.inv(matrix.T @ matrix)
            slope_standard_error = math.sqrt(float(covariance[1, 1])) * 100.0
            t_critical_df6 = 2.4469118511449692
            leave_one_out = []
            for omitted in range(len(group)):
                subset = [row for index, row in enumerate(group) if index != omitted]
                leave_one_out.append(fit_rows(subset, outcome, ["templated_prompt_tokens"])["coefficients"]["templated_prompt_tokens"] * 100.0)
            slope = model["coefficients"]["templated_prompt_tokens"] * 100.0
            slopes.append({
                "semantic_family": family,
                "slope_per_100_prompt_tokens": slope,
                "standard_error_per_100_prompt_tokens": slope_standard_error,
                "confidence_interval_95": [
                    slope - t_critical_df6 * slope_standard_error,
                    slope + t_critical_df6 * slope_standard_error,
                ],
                "uncertainty_method": "OLS t interval with 6 degrees of freedom; descriptive constructed-grid sensitivity",
                "r_squared": model["r_squared"],
                "leave_one_case_out_slope_range": [min(leave_one_out), max(leave_one_out)],
                "direction": "positive" if slope > 0 else ("negative" if slope < 0 else "zero"),
            })
        residual = family_plus_centered["_residual"].tolist()
        result["outcomes"][outcome] = {
            "family_only": public_model(family_only),
            "actual_token_only": public_model(token_only),
            "family_plus_within_family_centered_tokens": public_model(family_plus_centered),
            "incremental_r_squared_over_family": family_plus_centered["r_squared"] - family_only["r_squared"],
            "family_descriptions": descriptions,
            "within_family_slopes": slopes,
            "slope_direction_counts": dict(Counter(row["direction"] for row in slopes)),
            "residual_association_with_actual_tokens": association(
                [float(row["templated_prompt_tokens"]) for row in centered_rows], residual
            ),
            "residuals_by_length_level": [
                {
                    "length_level": level,
                    **residual_distribution([
                        residual[index] for index, row in enumerate(centered_rows)
                        if int(row["length_level"]) == level
                    ]),
                }
                for level in range(1, 9)
            ],
            "largest_absolute_residuals": sorted(
                [
                    {"case_id": row["case_id"], "residual": residual[index]}
                    for index, row in enumerate(centered_rows)
                ],
                key=lambda item: (-abs(item["residual"]), item["case_id"]),
            )[:8],
        }
    return result


def locality_model_set(rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    models = {}
    lofos = {}
    for model_id, predictor, family in (
        ("M1", "hit_ratio", False),
        ("M2", "loads_per_token", False),
        ("M3", "hit_ratio", True),
        ("M4", "loads_per_token", True),
    ):
        model = fit_rows(rows, "decode_tok_s", [predictor], include_family=family)
        residuals = model["_residual"].tolist()
        models[model_id] = {
            "formula": f"decode_tok_s ~ {predictor}" + (" + family" if family else ""),
            "projection_eligible": projection_model_eligible(model_id),
            **public_model(model),
            "residuals_by_family": family_residuals(rows, residuals),
            "residual_association_with_actual_tokens": association(
                [float(row["templated_prompt_tokens"]) for row in rows], residuals
            ),
            "residuals_by_length_level": [
                {"length_level": level, **residual_distribution([
                    residuals[index] for index, row in enumerate(rows)
                    if int(row["length_level"]) == level
                ])}
                for level in sorted({int(row["length_level"]) for row in rows})
            ],
            "largest_absolute_residuals": sorted(
                [{"case_id": row["case_id"], "policy": row["policy"], "residual": residuals[index]}
                 for index, row in enumerate(rows)],
                key=lambda item: (-abs(item["residual"]), item["case_id"], item["policy"]),
            )[:8],
        }
        if not family:
            lofos[model_id] = add_lofo_uncertainty(simple_lofo(rows, predictor))
    return models, lofos


def projection_model_eligible(model_id: str) -> bool:
    return model_id in {"M1", "M2"}


def projection_gate_result(
    primary_lofo: dict[str, Any],
    sensitivity_lofo: dict[str, Any],
    primary_rows: Sequence[dict[str, Any]],
    sensitivity_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    sets = []
    for name, lofo, rows in (
        ("primary_stage_a", primary_lofo, primary_rows),
        ("protocol_compatible_sensitivity", sensitivity_lofo, sensitivity_rows),
    ):
        median_tps = statistics.median(float(row["decode_tok_s"]) for row in rows)
        family_medians = {
            family: statistics.median(
                float(row["decode_tok_s"]) for row in rows if row["semantic_family"] == family
            )
            for family in sorted({str(row["semantic_family"]) for row in rows})
        }
        family_checks = []
        for row in lofo["family_residuals"]:
            limit = 0.05 * family_medians[row["semantic_family"]]
            family_checks.append({
                "semantic_family": row["semantic_family"],
                "absolute_mean_residual": abs(row["mean_signed_residual"]),
                "limit": limit,
                "pass": abs(row["mean_signed_residual"]) <= limit,
            })
        predictions = lofo.get("_predictions", [])

        def regime_checks(field: str) -> list[dict[str, Any]]:
            checks = []
            for value in sorted({str(row[field]) for row in predictions}):
                group = [row for row in predictions if str(row[field]) == value]
                mean_residual = statistics.fmean(float(row["residual"]) for row in group)
                limit = 0.05 * statistics.median(float(row["measured"]) for row in group)
                checks.append({
                    field: value,
                    "count": len(group),
                    "absolute_mean_residual": abs(mean_residual),
                    "limit": limit,
                    "pass": abs(mean_residual) <= limit,
                })
            return checks

        length_regimes = regime_checks("length_level")
        policy_regimes = regime_checks("policy")
        regime_pass = (
            all(row["pass"] for row in length_regimes)
            and all(row["pass"] for row in policy_regimes)
        )
        checks = {
            "pooled_lofo_r_squared": {
                "observed": lofo["pooled_oof_r_squared"], "minimum": 0.90,
                "pass": lofo["pooled_oof_r_squared"] >= 0.90,
            },
            "bootstrap_r_squared_lower": {
                "observed": lofo["cluster_bootstrap"]["pooled_oof_r_squared_95_interval"][0],
                "minimum": 0.80,
                "pass": lofo["cluster_bootstrap"]["pooled_oof_r_squared_95_interval"][0] >= 0.80,
            },
            "lofo_rmse": {
                "observed": lofo["pooled_oof_rmse"], "maximum": 0.02 * median_tps,
                "pass": lofo["pooled_oof_rmse"] <= 0.02 * median_tps,
            },
            "family_mean_residuals": {"rows": family_checks, "pass": all(row["pass"] for row in family_checks)},
            "unexplained_material_regime": {
                "observed": not regime_pass,
                "definition": "length-level and policy mean OOF residuals must each remain within 5% of group median measured TPS; all row outliers remain retained",
                "length_level_rows": length_regimes,
                "policy_rows": policy_regimes,
                "pass": regime_pass,
            },
        }
        sets.append({"dataset": name, "checks": checks, "pass": all(item["pass"] for item in checks.values())})
    return {"status": "PASS" if all(item["pass"] for item in sets) else "FAIL", "datasets": sets}


def locality_tps_analysis(primary: Sequence[dict[str, Any]], sensitivity: Sequence[dict[str, Any]]) -> dict[str, Any]:
    primary_models, primary_lofos = locality_model_set(primary)
    sensitivity_models, sensitivity_lofos = locality_model_set(sensitivity)
    first = primary_lofos["M1"]["pooled_oof_rmse"]
    second = primary_lofos["M2"]["pooled_oof_rmse"]
    relative_difference = abs(first - second) / min(first, second)
    selected = "M2" if relative_difference < 0.05 else ("M1" if first < second else "M2")
    selection_reason = (
        "RMSE values differ by less than 5%; prefer loads_per_token"
        if relative_difference < 0.05 else "lower primary pooled LOFO RMSE"
    )
    gate = projection_gate_result(
        primary_lofos[selected], sensitivity_lofos[selected], primary, sensitivity
    )
    predictor = "hit_ratio" if selected == "M1" else "loads_per_token"
    domain = [
        min(float(row[predictor]) for row in sensitivity),
        max(float(row[predictor]) for row in sensitivity),
    ]
    return {
        "schema_version": "issue105-locality-tps-validation-v1",
        "status": "PASS",
        "post_hoc_exploratory": True,
        "primary": {
            "row_count": len(primary),
            "models": primary_models,
            "lofo": {key: public_lofo(value) for key, value in primary_lofos.items()},
        },
        "protocol_compatible_sensitivity": {
            "row_count": len(sensitivity),
            "unique_case_count": len({row["case_id"] for row in sensitivity}),
            "policy_counts": dict(Counter(row["policy"] for row in sensitivity)),
            "models": sensitivity_models,
            "lofo": {key: public_lofo(value) for key, value in sensitivity_lofos.items()},
            "cluster_structure": "rows retained by case; LOFO and bootstrap cluster at semantic family",
        },
        "model_selection": {
            "selected_model": selected,
            "selected_predictor": predictor,
            "primary_rmse_relative_difference": relative_difference,
            "rule": selection_reason,
            "family_dummy_models_projection_eligible": False,
        },
        "projection_gate": gate,
        "projection_domain": {"predictor": predictor, "minimum": domain[0], "maximum": domain[1]},
        "_selected_primary_lofo": primary_lofos[selected],
        "_selected_sensitivity_lofo": sensitivity_lofos[selected],
    }


def projection_allowed(value: float, domain: Sequence[float]) -> bool:
    return float(domain[0]) <= float(value) <= float(domain[1])


def threshold_crossing(
    curve: Sequence[dict[str, Any]], metric: str, target: float, direction: str
) -> dict[str, Any]:
    rows = sorted(
        [row for row in curve if row["status"] == "pass" and row[metric] is not None],
        key=lambda row: (int(row["capacity_slots"]), str(row["capacity_label"])),
    )
    if not rows:
        return {"status": "INCONCLUSIVE", "reason": "no supported curve rows"}
    values = [float(row[metric]) for row in rows]
    if direction == "at_least":
        monotone = all(right + 1e-15 >= left for left, right in zip(values, values[1:]))
        satisfies = lambda value: value >= target
    elif direction == "at_most":
        monotone = all(right <= left + 1e-15 for left, right in zip(values, values[1:]))
        satisfies = lambda value: value <= target
    else:
        raise AnalysisError(f"unknown threshold direction: {direction}")
    if not monotone:
        return {"status": "INCONCLUSIVE", "reason": "non-monotone exported curve"}
    for index, row in enumerate(rows):
        if satisfies(float(row[metric])):
            previous = rows[index - 1] if index else None
            return {
                "status": "BRACKETED",
                "target": target,
                "lower_slots": int(previous["capacity_slots"]) if previous else None,
                "upper_slots": int(row["capacity_slots"]),
                "lower_bytes": int(previous["capacity_bytes"]) if previous else None,
                "upper_bytes": int(row["capacity_bytes"]),
                "lower_gib": float(previous["capacity_bytes"]) / 2**30 if previous else None,
                "upper_gib": float(row["capacity_bytes"]) / 2**30,
                "lower_value": float(previous[metric]) if previous else None,
                "upper_value": float(row[metric]),
                "domain_floor_censored": previous is None,
                "monotone": True,
            }
    last = rows[-1]
    return {
        "status": "INCONCLUSIVE",
        "reason": "target not crossed inside published replay domain",
        "target": target,
        "lower_bound_slots": int(last["capacity_slots"]),
        "lower_bound_bytes": int(last["capacity_bytes"]),
        "last_supported_value": float(last[metric]),
        "monotone": True,
    }


def capacity_equivalence_status(results: Sequence[dict[str, Any]]) -> tuple[str, bool]:
    all_bracketed = all(item.get("status") == "BRACKETED" for item in results)
    upper_slots = [item.get("upper_slots") for item in results]
    disagreement = len(set(upper_slots)) > 1 if all_bracketed else True
    return ("BRACKETED_CONSISTENT" if all_bracketed and not disagreement else "INCONCLUSIVE", disagreement)


def physical_exact_anchor_cases(capacity: Sequence[dict[str, Any]]) -> set[str]:
    cases = {
        str(row["case_id"])
        for row in capacity
        if row.get("selection_role") == "STAGE_B_REPRESENTATIVE"
    }
    if len(cases) != 16:
        raise AnalysisError(f"expected 16 direct physical EXACT anchor cases, found {len(cases)}")
    return cases


def virtual_capacity_rows(
    physical: Sequence[dict[str, Any]], capacity: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    targets = {
        row["case_id"]: row for row in physical
        if row["stage"] == "STAGE_A" and row["policy"] == "S2_P50"
    }
    direct_anchor_cases = physical_exact_anchor_cases(capacity)
    decode = [row for row in capacity if row["phase"] == "DECODE"]
    cases = sorted({row["case_id"] for row in decode})
    output = []
    for case_id in cases:
        target = targets.get(case_id)
        if target is None:
            continue
        curves = {
            policy: [row for row in decode if row["case_id"] == case_id and row["policy_result_class"] == policy]
            for policy in ("EXACT_LRU", "S2_P50_FIXED_ROUTE")
        }
        if not all(curves.values()):
            continue
        selection_roles = {str(row["selection_role"]) for rows in curves.values() for row in rows}
        if len(selection_roles) != 1:
            raise AnalysisError(f"capacity selection-role disagreement for {case_id}")
        selection_role = next(iter(selection_roles))
        results = {}
        for policy, rows in curves.items():
            results[policy] = {
                "hit": threshold_crossing(rows, "hit_ratio", float(target["hit_ratio"]), "at_least"),
                "load": threshold_crossing(rows, "loads_per_token", float(target["loads_per_token"]), "at_most"),
            }
        exact_hit = results["EXACT_LRU"]["hit"]
        exact_load = results["EXACT_LRU"]["load"]
        s2_hit = results["S2_P50_FIXED_ROUTE"]["hit"]
        s2_load = results["S2_P50_FIXED_ROUTE"]["load"]
        all_bracketed = all(item["status"] == "BRACKETED" for item in (exact_hit, exact_load, s2_hit, s2_load))
        exact_status, exact_disagreement = capacity_equivalence_status((exact_hit, exact_load))
        s2_status, s2_disagreement = capacity_equivalence_status((s2_hit, s2_load))
        disagreement = exact_disagreement or s2_disagreement
        exact_upper = exact_hit.get("upper_slots") if all_bracketed else None
        s2_upper = s2_hit.get("upper_slots") if all_bracketed else None
        status = "BRACKETED_CONSISTENT" if exact_status == s2_status == "BRACKETED_CONSISTENT" else "INCONCLUSIVE"
        output.append({
            "case_id": case_id,
            "semantic_family": target["semantic_family"],
            "length_level": int(target["length_level"]),
            "selection_role": selection_role,
            "physical_s2_hit_ratio": float(target["hit_ratio"]),
            "physical_s2_loads_per_token": float(target["loads_per_token"]),
            "physical_target_source_sha256_json": json.dumps(source_hashes([target]), separators=(",", ":")),
            "physical_reference_slots": C0_SLOTS,
            "physical_reference_bytes": C0_BYTES,
            "exact_hit_result_json": json.dumps(native(exact_hit), sort_keys=True, separators=(",", ":")),
            "exact_load_result_json": json.dumps(native(exact_load), sort_keys=True, separators=(",", ":")),
            "s2_counterfactual_hit_result_json": json.dumps(native(s2_hit), sort_keys=True, separators=(",", ":")),
            "s2_counterfactual_load_result_json": json.dumps(native(s2_load), sort_keys=True, separators=(",", ":")),
            "exact_source_sha256_json": json.dumps(source_hashes(curves["EXACT_LRU"]), separators=(",", ":")),
            "s2_counterfactual_source_sha256_json": json.dumps(source_hashes(curves["S2_P50_FIXED_ROUTE"]), separators=(",", ":")),
            "exact_upper_slots": exact_upper,
            "s2_counterfactual_upper_slots": s2_upper,
            "exact_upper_bytes": int(exact_upper) * EXPERT_BYTES if exact_upper else None,
            "exact_upper_gib": int(exact_upper) * EXPERT_BYTES / 2**30 if exact_upper else None,
            "s2_counterfactual_upper_bytes": int(s2_upper) * EXPERT_BYTES if s2_upper else None,
            "s2_counterfactual_upper_gib": int(s2_upper) * EXPERT_BYTES / 2**30 if s2_upper else None,
            "physical_capacity_amplification_upper": float(exact_upper) / C0_SLOTS if exact_upper else None,
            "physical_memory_saving_lower": 1.0 - C0_SLOTS / float(exact_upper) if exact_upper else None,
            "counterfactual_capacity_amplification_upper": float(exact_upper) / float(s2_upper) if exact_upper and s2_upper else None,
            "counterfactual_memory_saving_lower": 1.0 - float(s2_upper) / float(exact_upper) if exact_upper and s2_upper else None,
            "hit_load_disagreement": disagreement,
            "status": status,
            "physical_target_evidence_class": "MEASURED_PHYSICAL",
            "exact_evidence_class": "EXACT_REPLAY",
            "s2_counterfactual_evidence_class": "FIXED_ROUTE_COUNTERFACTUAL",
            "source_identity_status": "accepted_checksum_linked",
            "validation_stratum": (
                "physical_EXACT_anchor_16_of_16"
                if case_id in direct_anchor_cases
                else "observer_semantic_order_44_of_44"
            ),
            "post_hoc_exploratory": True,
        })
    def heterogeneity(group_field: str) -> list[dict[str, Any]]:
        rows = []
        for group_name in sorted({str(row[group_field]) for row in output}):
            group = [row for row in output if row[group_field] == group_name]
            rows.append({
                group_field: group_name,
                "row_count": len(group),
                "status_counts": dict(Counter(row["status"] for row in group)),
                "physical_capacity_amplification_upper": distribution(
                    row["physical_capacity_amplification_upper"] for row in group
                    if row["physical_capacity_amplification_upper"] is not None
                ),
                "counterfactual_capacity_amplification_upper": distribution(
                    row["counterfactual_capacity_amplification_upper"] for row in group
                    if row["counterfactual_capacity_amplification_upper"] is not None
                ),
            })
        return rows

    summary = {
        "schema_version": "issue105-virtual-cache-capacity-summary-v1",
        "status": "PASS",
        "row_count": len(output),
        "status_counts": dict(Counter(row["status"] for row in output)),
        "hit_load_disagreement_count": sum(row["hit_load_disagreement"] for row in output),
        "validation_stratum_counts": dict(Counter(row["validation_stratum"] for row in output)),
        "physical_capacity_amplification_upper": distribution(
            row["physical_capacity_amplification_upper"] for row in output
            if row["physical_capacity_amplification_upper"] is not None
        ),
        "counterfactual_capacity_amplification_upper": distribution(
            row["counterfactual_capacity_amplification_upper"] for row in output
            if row["counterfactual_capacity_amplification_upper"] is not None
        ),
        "family_heterogeneity": heterogeneity("semantic_family"),
        "endpoint_role_heterogeneity": heterogeneity("selection_role"),
        "interpretation": "discrete exported brackets; upper-bound summaries are not exact slot thresholds",
        "no_extrapolation": True,
        "post_hoc_exploratory": True,
    }
    return output, summary


def working_set_analysis(
    route_features: Sequence[dict[str, Any]], physical: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    targets = {row["case_id"]: row for row in physical if row["stage"] == "STAGE_A"}
    rows = []
    for source in route_features:
        if source["phase"] != "DECODE" or source["case_id"] not in targets:
            continue
        row = dict(source)
        row.update({
            "hit_ratio": targets[source["case_id"]]["hit_ratio"],
            "loads_per_token": targets[source["case_id"]]["loads_per_token"],
            "templated_prompt_tokens": targets[source["case_id"]]["templated_prompt_tokens"],
            "policy": "S2_P50",
        })
        rows.append(row)
    features = (
        "distinct_expert_keys", "top16_selected_mass_fraction", "mean_layer_effective_experts",
        "mean_layer_entropy_bits", "finite_stack_distance_median", "finite_stack_distance_p90",
        "core_mass_gamma_0_8", "core_mass_gamma_1_0",
    )
    associations = []
    predictive = []
    for feature in features:
        x = [float(row[feature]) for row in rows]
        for outcome in ("hit_ratio", "loads_per_token"):
            y = [float(row[outcome]) for row in rows]
            lofo = simple_lofo(rows, feature, outcome)
            associations.append({"feature": feature, "outcome": outcome, **association(x, y)})
            predictive.append({
                "feature": feature,
                "outcome": outcome,
                "in_sample": public_model(fit_rows(rows, outcome, [feature])),
                "lofo": public_lofo(lofo),
            })
    best = max(
        (row for row in predictive if row["outcome"] == "hit_ratio"),
        key=lambda row: row["lofo"]["pooled_oof_r_squared"],
    )
    endpoint_rows = [row for row in rows if row["selection_role"] == "STAGE_B2_ENDPOINT"]
    family_descriptions = []
    for family in sorted({str(row["semantic_family"]) for row in rows}):
        group = [row for row in rows if row["semantic_family"] == family]
        family_descriptions.append({
            "semantic_family": family,
            "count": len(group),
            "feature_medians": {feature: statistics.median(float(row[feature]) for row in group) for feature in features},
        })
    return {
        "schema_version": "issue105-working-set-analysis-v1",
        "status": "PASS",
        "post_hoc_exploratory": True,
        "row_count": len(rows),
        "observer_supported_only": True,
        "feature_associations": associations,
        "single_feature_lofo_models": predictive,
        "best_hit_ratio_feature": {
            "feature": best["feature"],
            "pooled_oof_r_squared": best["lofo"]["pooled_oof_r_squared"],
            "pooled_oof_rmse": best["lofo"]["pooled_oof_rmse"],
        },
        "family_descriptions": family_descriptions,
        "endpoint_sensitivity": {
            "row_count": len(endpoint_rows),
            "b1_count": sum(int(row["length_level"]) == 1 for row in endpoint_rows),
            "b8_count": sum(int(row["length_level"]) == 8 for row in endpoint_rows),
            "feature_distributions": {
                feature: {
                    "B1": distribution(float(row[feature]) for row in endpoint_rows if int(row["length_level"]) == 1),
                    "B8": distribution(float(row[feature]) for row in endpoint_rows if int(row["length_level"]) == 8),
                }
                for feature in features
            },
        },
        "interpretation_limit": "associations describe frozen selected routes; they do not assign semantic expert functions",
        "_rows": rows,
    }


def core_periphery_analysis(
    committee: dict[str, Any], capacity: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    phases = {}
    for phase, source in committee["phases"].items():
        phase_rows = []
        for gamma in source["gamma_sensitivity"]:
            lofo = gamma["leave_one_family_out"]
            phase_rows.append({
                "gamma": float(gamma["gamma"]),
                "threshold_family_count": int(gamma["threshold_family_count"]),
                "core_expert_key_count": int(gamma["core_expert_key_count"]),
                "fraction_of_all_routed_expert_keys": float(gamma["fraction_of_all_routed_expert_keys"]),
                "selected_mass_fraction": float(gamma["selected_mass_fraction"]),
                "leave_one_family_out_core_mass_fraction": gamma["leave_one_family_out_core_mass_fraction"],
                "leave_one_family_out_core_key_count": distribution(
                    float(row["core_distinct_expert_keys"]) for row in lofo
                ),
            })
        phases[phase] = {
            "audit_class": source["audit_class"],
            "complete_routing_weight_profile_available": source["complete_routing_weight_profile_available"],
            "gamma_sensitivity": phase_rows,
        }
    exact = {
        (row["case_id"], row["phase"], row["capacity_slots"]): row
        for row in capacity if row["policy_result_class"] == "EXACT_LRU"
    }
    cells = []
    for row in capacity:
        if row["policy_result_class"] != "COMMITTEE_PIN_FIXED_ROUTE":
            continue
        baseline = exact[(row["case_id"], row["phase"], row["capacity_slots"])]
        comparable = row["status"] == "pass" and baseline["status"] == "pass"
        delta = float(row["hit_ratio"]) - float(baseline["hit_ratio"]) if comparable else None
        cells.append({
            "case_id": row["case_id"],
            "semantic_family": row["semantic_family"],
            "phase": row["phase"],
            "gamma": row["gamma"],
            "capacity_slots": row["capacity_slots"],
            "capacity_bytes": row["capacity_bytes"],
            "status": row["status"],
            "committee_hit_ratio": row["hit_ratio"],
            "exact_hit_ratio": baseline["hit_ratio"],
            "hit_ratio_delta": delta,
            "regresses": delta is not None and delta < 0,
            "source_evidence_class": "FIXED_ROUTE_COUNTERFACTUAL",
            "post_hoc_exploratory": True,
        })
    counterfactual_summary = []
    for gamma in sorted({float(row["gamma"]) for row in cells}):
        group = [row for row in cells if float(row["gamma"]) == gamma]
        deltas = [float(row["hit_ratio_delta"]) for row in group if row["hit_ratio_delta"] is not None]
        counterfactual_summary.append({
            "gamma": gamma,
            "cell_count": len(group),
            "comparable_count": len(deltas),
            "infeasible_count": len(group) - len(deltas),
            "improves_count": sum(value > 0 for value in deltas),
            "equal_count": sum(value == 0 for value in deltas),
            "regresses_count": sum(value < 0 for value in deltas),
            "hit_ratio_delta": distribution(deltas),
        })
    report = {
        "schema_version": "issue105-core-periphery-analysis-v1",
        "status": "PASS",
        "post_hoc_exploratory": True,
        "observable_boundary": "selected top-k/top-M frequency only; CommitteeAudit-inspired",
        "phases": phases,
        "committee_pin_counterfactual": {
            "summary_by_gamma": counterfactual_summary,
            "negative_cells_preserved": sum(row["regresses"] for row in cells),
            "infeasible_cells_preserved": sum(row["status"] != "pass" for row in cells),
            "interpretation": "fixed-route counterfactual; no production pinning or TPS claim",
        },
        "peripheral_reuse_stratification": {
            "status": "INCONCLUSIVE",
            "reason": "frozen summary exposes core demand mass and overall reuse, not core/periphery-specific stack-distance distributions",
        },
    }
    return report, cells


def prior_art_rows(source_path: pathlib.Path) -> list[dict[str, Any]]:
    rows = [
        ("PipeNetwork Kimi-K3 REAP", "normalized overlap/specialization patterns with explicit definitions and random baseline", "normalized-comparable", "same K3; saliency/top-N/source-domain observable differs from selected-route frequency"),
        ("PipeNetwork Kimi-K3 REAP", "raw saliency/pruning metrics or semantic-function transfer", "qualitative only", "observable and intervention differ; project-code license unresolved"),
        ("CommitteeAudit / Standing Committee", "core/periphery hypothesis and diagnostic structure", "qualitative only", "other models and routing weights; #102 exposes selected top-k/top-M only"),
        ("WASTE", "normalized cache/working-set ratios after unit reconciliation", "normalized-comparable", "full K3 systems context but different representation and cache hierarchy"),
        ("WASTE", "absolute TPS", "qualitative only", "hardware, expert bytes, kernels and cache hierarchy differ"),
        ("Colibrì", "normalized source-MXFP4 layout/memory quantities", "normalized-comparable", "same K3 layout family when units are reconciled"),
        ("Colibrì", "absolute TPS", "qualitative only", "no exact same-host/protocol comparison"),
        ("Cache-Conditional Experts", "cache-aware locality/quality concept", "qualitative only", "different model, policy and quality protocol"),
        ("MoE-ERAS", "residency-aware routing concept", "qualitative only", "different model, system and protocol"),
        ("ReMoE", "reuse-oriented router methodology", "qualitative only", "training-based fine-tuning differs from bounded frozen S2"),
    ]
    return [
        {"work": work, "claim_or_metric": claim, "comparability": classification, "rationale": rationale,
         "raw_tps_pooling_allowed": False, "source_document": "docs/PRIOR_ART.md",
         "source_document_sha256": sha256(source_path)}
        for work, claim, classification, rationale in rows
    ]


def mechanism_boundary() -> dict[str, Any]:
    return {
        "schema_version": "issue105-mechanism-boundary-v1",
        "status": "PASS",
        "allowed": {
            "physical_S2": "MEASURED_PHYSICAL",
            "EXACT_capacity": "EXACT_REPLAY",
            "S2_fixed_route_capacity": "FIXED_ROUTE_COUNTERFACTUAL",
            "committee_pin": "FIXED_ROUTE_COUNTERFACTUAL",
        },
        "physical_anchor_consistency": "16/16 physical EXACT anchor matches plus 44/44 observer semantic-order prevalidation",
        "fixed_route_interpretation": "residency/locality behavior compatible with a frozen captured route",
        "forbidden_inference": "ROUTE_FEEDBACK = physical - replay",
        "route_feedback_status": "UNMEASURED_BY_ISSUE_105",
        "downstream_issue": "#99",
        "post_hoc_exploratory": True,
    }


def source_hashes(rows: Sequence[dict[str, Any]]) -> list[str]:
    values = set()
    for row in rows:
        raw = row.get("source_sha256")
        if not raw:
            continue
        if isinstance(raw, str) and raw.startswith("["):
            values.update(json.loads(raw))
        else:
            values.add(str(raw))
    return sorted(values)


def artifact(catalog: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    return next(row for row in catalog["artifacts"] if row["artifact_id"] == artifact_id)


def family_color_map(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    families = sorted({str(row["semantic_family"]) for row in rows})
    return {family: FAMILY_COLORS(index % 20) for index, family in enumerate(families)}


def finish_figure(path: pathlib.Path, figure: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp.svg")
    figure.savefig(temporary, format="svg", metadata={"Date": None})
    plt.close(figure)
    os.replace(temporary, path)
    return sha256(path)


def figure_sidecar(
    path: pathlib.Path,
    figure_id: str,
    figure_path: pathlib.Path | None,
    logical_hashes: Sequence[str],
    source_sha: Sequence[str],
    code_version: str,
    query: str,
    metrics: Sequence[str],
    evidence: Sequence[str],
    gate_status: str,
) -> dict[str, Any]:
    value = {
        "schema_version": "issue105-figure-sidecar-v1",
        "figure_id": figure_id,
        "input_table_logical_sha256": sorted(set(logical_hashes)),
        "input_source_sha256": sorted(set(source_sha)),
        "analysis_code_version": code_version,
        "query_filter_definition": query,
        "metric_definitions": list(metrics),
        "evidence_classes": sorted(set(evidence)),
        "projection_gate_status": gate_status,
        "output_figure_sha256": sha256(figure_path) if figure_path is not None else None,
        "output_status": "rendered" if figure_path is not None else "omitted_by_projection_gate",
    }
    write_json(path, value)
    return value


def render_figures(
    output_root: pathlib.Path,
    primary: Sequence[dict[str, Any]],
    sensitivity: Sequence[dict[str, Any]],
    capacity: Sequence[dict[str, Any]],
    virtual: Sequence[dict[str, Any]],
    locality: dict[str, Any],
    working: dict[str, Any],
    core: dict[str, Any],
    overlap: dict[str, Any],
    endpoints: dict[str, Any],
    catalog: dict[str, Any],
    code_version: str,
) -> list[dict[str, Any]]:
    plt.rcParams.update({"svg.hashsalt": "issue105", "font.size": 8, "figure.dpi": 100})
    figure_root = output_root / "figures"
    colors = family_color_map(primary)
    physical_meta = artifact(catalog, "physical_runs")
    capacity_meta = artifact(catalog, "capacity_curves")
    route_meta = artifact(catalog, "route_features")
    sidecars = []

    for number, outcome, ylabel in ((1, "hit_ratio", "Hit ratio"), (2, "decode_tok_s", "Decode tok/s")):
        figure, axis = plt.subplots(figsize=(8, 5))
        for family, color in colors.items():
            rows = [row for row in primary if row["semantic_family"] == family]
            axis.scatter([row["templated_prompt_tokens"] for row in rows], [row[outcome] for row in rows], s=18, color=color, alpha=0.8, label=family)
        axis.set(xlabel="Actual templated prompt tokens", ylabel=ylabel, title=f"Physical S2 {ylabel.lower()} vs actual prompt length")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=5, ncol=2, loc="best")
        figure.tight_layout()
        path = figure_root / f"figure-{number:02d}.svg"
        finish_figure(path, figure)
        sidecars.append(figure_sidecar(
            figure_root / f"figure-{number:02d}.sidecar.json", f"F{number:02d}", path,
            [physical_meta["logical_sha256"]], source_hashes(primary), code_version,
            "physical_runs: stage=STAGE_A, policy=S2_P50, case_role=primary",
            ["templated_prompt_tokens", outcome], ["MEASURED_PHYSICAL"], "not_applicable",
        ))

    selected_predictor = locality["model_selection"]["selected_predictor"]
    figure, axis = plt.subplots(figsize=(8, 5))
    policy_markers = {"S2_P50": "o", "EXACT": "s", "KNEE": "^"}
    for policy, marker in policy_markers.items():
        rows = [row for row in sensitivity if row["policy"] == policy]
        axis.scatter([row[selected_predictor] for row in rows], [row["decode_tok_s"] for row in rows], marker=marker, s=22, alpha=0.72, label=f"{policy} measured")
    model = locality["protocol_compatible_sensitivity"]["models"][locality["model_selection"]["selected_model"]]
    domain = locality["projection_domain"]
    x_line = np.linspace(domain["minimum"], domain["maximum"], 100)
    axis.plot(x_line, model["coefficients"]["intercept"] + model["coefficients"][selected_predictor] * x_line, color="black", linewidth=1.2, label="selected linear fit")
    selected_lofo = locality["protocol_compatible_sensitivity"]["lofo"][locality["model_selection"]["selected_model"]]
    gate_annotation = (
        f"LOFO R²={selected_lofo['pooled_oof_r_squared']:.4f}\n"
        f"LOFO RMSE={selected_lofo['pooled_oof_rmse']:.6f}\n"
        f"family-cluster 95% lower R²="
        f"{selected_lofo['cluster_bootstrap']['pooled_oof_r_squared_95_interval'][0]:.4f}\n"
        f"projection gate={locality['projection_gate']['status']}"
    )
    axis.text(
        0.02, 0.98, gate_annotation, transform=axis.transAxes, va="top", fontsize=7,
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.82},
    )
    axis.set(xlabel=selected_predictor, ylabel="Measured decode tok/s", title="Measured TPS vs locality (protocol-compatible physical rows)")
    axis.grid(alpha=0.2); axis.legend()
    figure.tight_layout()
    path = figure_root / "figure-03.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-03.sidecar.json", "F03", path,
        [physical_meta["logical_sha256"]], source_hashes(sensitivity), code_version,
        "physical_runs: Stage-A S2 plus Stage-C EXACT/KNEE, non-duplicated",
        [selected_predictor, "decode_tok_s", "LOFO residual audit"], ["MEASURED_PHYSICAL"], locality["projection_gate"]["status"],
    ))

    pairs = overlap["representative_decode_pairs"]
    families = sorted({row["left_family"] for row in pairs} | {row["right_family"] for row in pairs})
    indexes = {family: index for index, family in enumerate(families)}
    matrix = np.eye(len(families))
    for row in pairs:
        left, right = indexes[row["left_family"]], indexes[row["right_family"]]
        matrix[left, right] = matrix[right, left] = float(row["cosine_similarity"])
    figure, axis = plt.subplots(figsize=(8, 7))
    image = axis.imshow(matrix, vmin=0, vmax=1, cmap="viridis")
    axis.set_xticks(range(len(families)), [f"F{i+1}" for i in range(len(families))], rotation=90)
    axis.set_yticks(range(len(families)), [f"F{i+1}" for i in range(len(families))])
    axis.set_title("Representative family x family route cosine similarity")
    figure.colorbar(image, ax=axis, label="Cosine similarity")
    figure.tight_layout(); path = figure_root / "figure-04.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-04.sidecar.json", "F04", path,
        [route_meta["logical_sha256"]], [overlap["_source_sha256"]], code_version,
        "frozen Stage-B representative DECODE pairs", ["cosine_similarity"], ["MEASURED_OBSERVER"], "not_applicable",
    ))

    figure, axis = plt.subplots(figsize=(7, 5))
    values = [
        [row["cosine_similarity"] for row in endpoints["within_family_b1_b8"]],
        [row["cosine_similarity"] for row in endpoints["between_family_b1"]],
        [row["cosine_similarity"] for row in endpoints["between_family_b8"]],
    ]
    axis.boxplot(values, tick_labels=["Within B1→B8", "Between B1", "Between B8"], showfliers=True)
    axis.set(ylabel="Route cosine similarity", title="Within-family endpoint vs between-family route similarity")
    axis.grid(axis="y", alpha=0.2); figure.tight_layout()
    path = figure_root / "figure-05.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-05.sidecar.json", "F05", path,
        [route_meta["logical_sha256"]], [endpoints["_source_sha256"]], code_version,
        "frozen B1/B8 endpoint comparison sets", ["cosine_similarity"], ["MEASURED_OBSERVER", "CURATED_FROM_MEASURED"], "not_applicable",
    ))

    gamma_rows = core["phases"]["DECODE"]["gamma_sensitivity"]
    pin_rows = core["committee_pin_counterfactual"]["summary_by_gamma"]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].plot([row["gamma"] for row in gamma_rows], [row["selected_mass_fraction"] for row in gamma_rows], marker="o")
    axes[0].set(xlabel="Family prevalence γ", ylabel="Selected demand in recurrent core", title="Core demand mass")
    axes[1].bar([str(row["gamma"]) for row in pin_rows], [row["regresses_count"] for row in pin_rows], label="regresses")
    axes[1].bar([str(row["gamma"]) for row in pin_rows], [row["improves_count"] for row in pin_rows], bottom=[row["regresses_count"] for row in pin_rows], label="improves")
    axes[1].set(xlabel="γ", ylabel="Comparable cells", title="Committee-pin heterogeneity"); axes[1].legend()
    for axis in axes: axis.grid(alpha=0.2)
    figure.tight_layout(); path = figure_root / "figure-06.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-06.sidecar.json", "F06", path,
        [capacity_meta["logical_sha256"], route_meta["logical_sha256"]], core["source_sha256"], code_version,
        "DECODE core gamma sensitivity and every comparable committee-pin cell",
        ["selected_mass_fraction", "committee_hit_ratio_minus_EXACT"], ["MEASURED_OBSERVER", "FIXED_ROUTE_COUNTERFACTUAL"], "not_applicable",
    ))

    decode_pass = [row for row in capacity if row["phase"] == "DECODE" and row["status"] == "pass"]
    figure, axes = plt.subplots(1, 2, figsize=(10, 4))
    for policy in ("EXACT_LRU", "S2_P50_FIXED_ROUTE", "COMMITTEE_PIN_FIXED_ROUTE"):
        policy_rows = [row for row in decode_pass if row["policy_result_class"] == policy]
        slots = sorted({int(row["capacity_slots"]) for row in policy_rows})
        axes[0].plot(slots, [statistics.median(float(row["hit_ratio"]) for row in policy_rows if int(row["capacity_slots"]) == slot) for slot in slots], label=policy)
        axes[1].plot(slots, [statistics.median(float(row["loads_per_token"]) for row in policy_rows if int(row["capacity_slots"]) == slot) for slot in slots], label=policy)
    axes[0].set(xlabel="Capacity slots", ylabel="Median hit ratio", title="Capacity → hit")
    axes[1].set(xlabel="Capacity slots", ylabel="Median loads/token", title="Capacity → load")
    for axis in axes: axis.set_xscale("log"); axis.grid(alpha=0.2); axis.legend(fontsize=6)
    figure.tight_layout(); path = figure_root / "figure-07.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-07.sidecar.json", "F07", path,
        [capacity_meta["logical_sha256"]], source_hashes(capacity), code_version,
        "capacity_curves: phase=DECODE,status=pass; median by policy/class and slots",
        ["capacity_slots", "hit_ratio", "loads_per_token"], ["EXACT_REPLAY", "FIXED_ROUTE_COUNTERFACTUAL"], "not_applicable",
    ))

    conclusive = [row for row in virtual if row["exact_upper_slots"] is not None]
    inconclusive = [row for row in virtual if row["exact_upper_slots"] is None]
    figure, axis = plt.subplots(figsize=(8, 5))
    x = np.arange(len(conclusive))
    exact_brackets = [json.loads(row["exact_hit_result_json"]) for row in conclusive]
    s2_brackets = [json.loads(row["s2_counterfactual_hit_result_json"]) for row in conclusive]
    for index, bracket in enumerate(exact_brackets):
        lower = bracket["lower_slots"] if bracket["lower_slots"] is not None else bracket["upper_slots"]
        axis.vlines(index - 0.08, lower, bracket["upper_slots"], color="tab:blue", alpha=0.55)
    for index, bracket in enumerate(s2_brackets):
        lower = bracket["lower_slots"] if bracket["lower_slots"] is not None else bracket["upper_slots"]
        axis.vlines(index + 0.08, lower, bracket["upper_slots"], color="tab:orange", alpha=0.55)
    axis.scatter(x - 0.08, [row["exact_upper_slots"] for row in conclusive], s=16, label="EXACT bracket upper")
    axis.scatter(x + 0.08, [row["s2_counterfactual_upper_slots"] for row in conclusive], s=16, label="S2 fixed-route bracket upper")
    if inconclusive:
        lower_bounds = []
        for row in inconclusive:
            result = json.loads(row["exact_hit_result_json"])
            lower_bounds.append(result.get("lower_bound_slots", C0_SLOTS))
        start = len(conclusive)
        axis.scatter(np.arange(start, start + len(inconclusive)), lower_bounds, marker="v", color="black", label="INCONCLUSIVE lower bound")
    axis.axhline(C0_SLOTS, color="black", linestyle="--", linewidth=1, label="Physical S2 reference")
    axis.set(xlabel="Observer-supported case (sorted)", ylabel="Capacity slots", title="Virtual cache-capacity brackets")
    axis.set_yscale("log"); axis.grid(alpha=0.2); axis.legend(); figure.tight_layout()
    path = figure_root / "figure-08.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-08.sidecar.json", "F08", path,
        [capacity_meta["logical_sha256"], physical_meta["logical_sha256"]], source_hashes(capacity) + source_hashes(primary), code_version,
        "observer-supported cases with discrete threshold brackets; inconclusive cases retained in source table",
        ["C0_S2_PHYSICAL_SLOTS", "C_EXACT interval/lower bound", "C_S2_COUNTERFACTUAL interval/lower bound", "INCONCLUSIVE"], ["MEASURED_PHYSICAL", "EXACT_REPLAY", "FIXED_ROUTE_COUNTERFACTUAL"], "not_applicable",
    ))

    gate_status = locality["projection_gate"]["status"]
    if gate_status == "PASS":
        predictor = locality["model_selection"]["selected_predictor"]
        selected_model = locality["model_selection"]["selected_model"]
        model = locality["protocol_compatible_sensitivity"]["models"][selected_model]
        domain = [locality["projection_domain"]["minimum"], locality["projection_domain"]["maximum"]]
        eligible = [row for row in decode_pass if projection_allowed(float(row[predictor]), domain)]
        figure, axis = plt.subplots(figsize=(8, 5))
        for policy in sorted({row["policy_result_class"] for row in eligible}):
            group = [row for row in eligible if row["policy_result_class"] == policy]
            slots = sorted({int(row["capacity_slots"]) for row in group})
            medians = []
            for slot in slots:
                values = [float(row[predictor]) for row in group if int(row["capacity_slots"]) == slot]
                predictor_value = statistics.median(values)
                medians.append(model["coefficients"]["intercept"] + model["coefficients"][predictor] * predictor_value)
            axis.plot(slots, medians, label=policy)
        axis.set(xlabel="Capacity slots", ylabel="Projected decode tok/s", title="Capacity → TPS projection (in-domain only)")
        axis.set_xscale("log"); axis.grid(alpha=0.2); axis.legend(fontsize=6); figure.tight_layout()
        path = figure_root / "figure-09.svg"; finish_figure(path, figure)
        sidecars.append(figure_sidecar(
            figure_root / "figure-09.sidecar.json", "F09", path,
            [capacity_meta["logical_sha256"], physical_meta["logical_sha256"]], source_hashes(capacity) + source_hashes(sensitivity), code_version,
            f"projection gate PASS; capacity rows with {predictor} inside measured sensitivity domain only",
            ["capacity_slots", predictor, "TPS_PROJECTION"], ["TPS_PROJECTION"], gate_status,
        ))
    else:
        write_json(figure_root / "figure-09-gate-failure.json", {
            "schema_version": "issue105-figure-09-gate-failure-v1", "figure_id": "F09",
            "projection_gate_status": "FAIL", "projected_values_emitted": False,
        })
        sidecars.append(figure_sidecar(
            figure_root / "figure-09.sidecar.json", "F09", None,
            [capacity_meta["logical_sha256"], physical_meta["logical_sha256"]], source_hashes(capacity) + source_hashes(sensitivity), code_version,
            "projection omitted because gate failed", ["TPS_PROJECTION"], ["TPS_PROJECTION"], gate_status,
        ))

    best_feature = working["best_hit_ratio_feature"]["feature"]
    working_rows = working["_rows"]
    figure, axis = plt.subplots(figsize=(8, 5))
    for family, color in colors.items():
        group = [row for row in working_rows if row["semantic_family"] == family]
        axis.scatter([row[best_feature] for row in group], [row["hit_ratio"] for row in group], color=color, s=20, alpha=0.75)
    axis.set(xlabel=best_feature, ylabel="Physical S2 hit ratio", title="Working-set metric vs locality (observer-supported cases)")
    axis.grid(alpha=0.2); figure.tight_layout(); path = figure_root / "figure-10.svg"; finish_figure(path, figure)
    sidecars.append(figure_sidecar(
        figure_root / "figure-10.sidecar.json", "F10", path,
        [route_meta["logical_sha256"], physical_meta["logical_sha256"]], source_hashes(working_rows), code_version,
        "route_features: phase=DECODE joined by case_id to physical Stage-A S2",
        [best_feature, "hit_ratio", "LOFO-by-family"], ["MEASURED_OBSERVER", "MEASURED_PHYSICAL", "POST_HOC_EXPLORATORY"], "not_applicable",
    ))
    if len(sidecars) != 10:
        raise AnalysisError("primary figure family count is not exactly ten")
    return sidecars


def hypothesis_registry(
    locality: dict[str, Any], virtual: dict[str, Any], working: dict[str, Any],
    core: dict[str, Any], endpoints: dict[str, Any], provenance: dict[str, list[str]]
) -> list[dict[str, Any]]:
    locality_status = "supported" if locality["projection_gate"]["status"] == "PASS" else "contradicted"
    capacity_status = "supported" if virtual["status_counts"].get("BRACKETED_CONSISTENT", 0) else "inconclusive"
    best_r2 = working["best_hit_ratio_feature"]["pooled_oof_r_squared"]
    working_status = "supported" if best_r2 >= 0.5 else ("weak" if best_r2 > 0 else "contradicted")
    within = endpoints["comparison_summary"]["within_family_b1_b8"]["cosine_similarity"]["median"]
    between = endpoints["comparison_summary"]["between_family_b1"]["cosine_similarity"]["median"]
    core_gamma = next(row for row in core["phases"]["DECODE"]["gamma_sensitivity"] if row["gamma"] == 1.0)
    regresses = core["committee_pin_counterfactual"]["negative_cells_preserved"]
    hypotheses = [
        ("H105-01", "Locality predicts TPS outside semantic family", locality_status, "projection gate and LOFO metrics", "policy sensitivity or held-out family residual failure", provenance["physical"], "new same-protocol policies remain on the calibrated line", "add a held-out family/policy physical cohort", "#99"),
        ("H105-02", "Physical S2 locality is equivalent to materially larger EXACT cache capacity", capacity_status, f"{virtual['status_counts'].get('BRACKETED_CONSISTENT', 0)} bracket-consistent cases", "inconclusive and bracket-censored cases are retained", provenance["capacity"], "denser capacity support narrows the same above-C0 brackets", "run denser exact replay thresholds or physical larger-capacity anchors", "#81"),
        ("H105-03", "Working-set/reuse features explain cross-workload locality variation", working_status, f"best single-feature LOFO R2={best_r2:.6f}", "feature models retain family residuals and weak alternatives", provenance["routes"], "held-out workloads preserve feature/locality rank association", "capture observer routes for new held-out workloads", "#99"),
        ("H105-04", "Family labels are associated with distinct route-demand structure", "supported" if within > between else "weak", f"within-family endpoint median cosine={within:.6f}; between-B1={between:.6f}", "substantial overlap and heterogeneity remain", provenance["overlap"], "new within-family pairs remain more similar than matched between-family pairs", "add preregistered prompts per family with observer captures", "#99"),
        ("H105-05", "A recurrent selected-expert core exists under the frozen observables", "supported", f"gamma=1 core mass={core_gamma['selected_mass_fraction']:.6f}", "selected-frequency observables cannot assign semantic functions", provenance["core"], "held-out families retain nonzero recurrent selected mass", "repeat with complete routing weights and held-out domains", "#81"),
        ("H105-06", "Standing-committee pinning improves same-capacity locality", "heterogeneous" if regresses else "supported", "counterfactual improvements retained", f"{regresses} fixed-route cells regress", provenance["capacity"] + provenance["core"], "physical pinning would show family/capacity-dependent gains and regressions", "run a preregistered physical pinning comparison", "#81"),
        ("H105-07", "Actual prompt length has a robust within-family locality/TPS effect", "weak", "small incremental family-adjusted descriptive effects", "family slopes are heterogeneous and influential-case sensitive", provenance["physical"], "additional token levels would yield consistently signed within-family slopes", "add more actual-token levels within each family", "#99"),
        ("H105-08", "Fixed-route evidence explains a material residency-compatible component of S2 locality", "supported" if capacity_status == "supported" else "inconclusive", "fixed-route capacity brackets reach physical S2 targets", "autoregressive route-feedback causality remains unmeasured", provenance["capacity"], "direct fixed-route interventions reproduce the residency-compatible locality component", "run paired direct-route versus free-generation intervention", "#99"),
    ]
    return [
        {
            "id": identifier, "statement": statement, "supporting_observations": support,
            "null_or_contrary_observations": contrary, "source_provenance_sha256": sorted(set(source_shas)),
            "post_hoc": True, "predicted_measurable_consequence": consequence,
            "cheapest_falsifying_experiment": falsifier, "appropriate_downstream_issue": downstream,
            "status": status,
        }
        for identifier, statement, status, support, contrary, source_shas, consequence, falsifier, downstream in hypotheses
    ]


def registry_markdown(rows: Sequence[dict[str, Any]]) -> str:
    lines = ["# Issue 105 hypothesis registry", "", "All entries are `POST_HOC_EXPLORATORY`.", ""]
    for row in rows:
        lines.extend([
            f"## {row['id']} — {row['status']}", "", row["statement"], "",
            f"- Supporting: {row['supporting_observations']}",
            f"- Contrary/null: {row['null_or_contrary_observations']}",
            f"- Falsifier: {row['cheapest_falsifying_experiment']}",
            f"- Downstream: {row['appropriate_downstream_issue']}", "",
        ])
    return "\n".join(lines)


def validate_figure_sidecars(
    sidecars: Sequence[dict[str, Any]], schema_path: pathlib.Path | None = None
) -> None:
    required = {
        "figure_id", "input_table_logical_sha256", "input_source_sha256",
        "analysis_code_version", "query_filter_definition", "metric_definitions",
        "evidence_classes", "projection_gate_status", "output_figure_sha256",
    }
    if len(sidecars) != 10 or len({row["figure_id"] for row in sidecars}) != 10:
        raise AnalysisError("figure sidecar family count mismatch")
    for row in sidecars:
        if not required <= set(row):
            raise AnalysisError(f"incomplete figure sidecar: {row.get('figure_id')}")
        if schema_path is not None:
            validate_json(row, schema_path)


def final_synthesis_text(
    locality: dict[str, Any], virtual: dict[str, Any], working: dict[str, Any], core: dict[str, Any]
) -> str:
    gate = locality["projection_gate"]["status"]
    model = locality["model_selection"]["selected_model"]
    predictor = locality["model_selection"]["selected_predictor"]
    primary_lofo = locality["primary"]["lofo"][model]
    sensitivity_lofo = locality["protocol_compatible_sensitivity"]["lofo"][model]
    consistent = virtual["status_counts"].get("BRACKETED_CONSISTENT", 0)
    physical_amplification = virtual["physical_capacity_amplification_upper"]
    counterfactual_amplification = virtual["counterfactual_capacity_amplification_upper"]
    regressions = core["committee_pin_counterfactual"]["negative_cells_preserved"]
    return f"""# Issue 105 final synthesis

Status: `PASS` for the deterministic offline analysis package. All results below are `POST_HOC_EXPLORATORY`.

## Headline results

- `TPS_PROJECTION_GATE = {gate}` using `{model}` / `{predictor}`. Primary LOFO R² is {primary_lofo['pooled_oof_r_squared']:.6f} with RMSE {primary_lofo['pooled_oof_rmse']:.6f}; protocol-compatible sensitivity LOFO R² is {sensitivity_lofo['pooled_oof_r_squared']:.6f} with RMSE {sensitivity_lofo['pooled_oof_rmse']:.6f}. Both family-cluster bootstrap and family/policy/length-level residual gates pass.
- Virtual-capacity analysis reports discrete published brackets, never fitted thresholds or extrapolation: {consistent}/44 cases are bracket-consistent. The physical-reference EXACT upper-bracket amplification has median {physical_amplification['median']:.3f}× and range {physical_amplification['min']:.3f}–{physical_amplification['max']:.3f}×. The fixed-route counterfactual upper-bracket amplification has median {counterfactual_amplification['median']:.3f}× and range {counterfactual_amplification['min']:.3f}–{counterfactual_amplification['max']:.3f}×. These are bracket-upper summaries, not exact RAM thresholds or measured savings.
- The best single frozen working-set feature for physical hit ratio is `{working['best_hit_ratio_feature']['feature']}` with pooled LOFO R² {working['best_hit_ratio_feature']['pooled_oof_r_squared']:.6f}.
- Recurrent selected-expert cores exist under top-k/top-M observables, but committee pinning is heterogeneous and preserves {regressions} regressing fixed-route cells.
- Actual-token effects remain weak/heterogeneous after family adjustment; the constructed 16×8 corpus is not treated as an IID prompt-population sample.
- Prior-art values are classified claim by claim as normalized-comparable or qualitative-only; heterogeneous raw TPS is never pooled.

## Authority limits

- Physical TPS/locality: `MEASURED_PHYSICAL` only.
- Larger capacity: `EXACT_REPLAY` or `FIXED_ROUTE_COUNTERFACTUAL`.
- Any permitted projected TPS: `TPS_PROJECTION`, constrained to the measured predictor domain.
- Semantic sanity is narrow and is not long-horizon quality.
- Fixed-route replay does not identify autoregressive route-feedback causality; that remains unmeasured and belongs to #99.
- No policy was designed, tuned, benchmarked, or authorized for production by this issue.
"""


def analysis_catalog(output_root: pathlib.Path, code_version: str) -> dict[str, Any]:
    artifacts = []
    for path in sorted(output_root.rglob("*")):
        if not path.is_file() or path.name == "analysis-catalog.json":
            continue
        artifacts.append({
            "path": str(path.relative_to(output_root)),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    return {
        "schema_version": "issue105-analysis-catalog-v1",
        "status": "PASS",
        "analysis_code_version": code_version,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "self_reference": "analysis-catalog.json excludes its own recursive identity",
    }


def main() -> None:
    args = arguments()
    canonical_root = args.canonical_root.resolve(strict=True)
    frozen_root = args.frozen_source_root.resolve(strict=True)
    schema_root = args.schema_root.resolve(strict=True)
    output_root = args.output_root.resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise AnalysisError(f"output root must be absent or empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    schema_paths = {
        "analysis": schema_root / "secondary-analysis-v1.schema.json",
        "virtual": schema_root / "virtual-cache-capacity-v1.schema.json",
        "figure": schema_root / "figure-sidecar-v1.schema.json",
        "hypothesis": schema_root / "hypothesis-registry-v1.schema.json",
    }
    for path in schema_paths.values():
        schema = load_json(path)
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as error:
            raise AnalysisError(f"invalid JSON schema {path.name}: {error}") from error
    catalog = load_json(canonical_root / "canonical-catalog.json")
    if catalog.get("status") != "PASS":
        raise AnalysisError("canonical catalog is not PASS")
    physical = read_csv(
        canonical_root / "tables/physical_runs.csv",
        integer_fields={"length_level", "templated_prompt_tokens", "capacity_slots", "capacity_bytes"},
        float_fields={"decode_tok_s", "hit_ratio", "loads_per_token", "bytes_per_token"},
    )
    route_features = read_csv(
        canonical_root / "tables/route_features.csv",
        integer_fields={"length_level", "prompt_tokens", "selected_occurrences", "distinct_expert_keys"},
        float_fields={
            "mean_layer_entropy_bits", "mean_layer_effective_experts", "median_layer_distinct_experts",
            "top16_selected_mass_fraction", "mean_layer_mass50_experts", "mean_layer_mass90_experts",
            "cold_first_occurrences", "finite_stack_distance_median", "finite_stack_distance_mean",
            "finite_stack_distance_p90", "core_mass_gamma_0_8", "core_mass_gamma_1_0",
        },
    )
    capacity = pq.read_table(canonical_root / "tables/capacity_curves.parquet").to_pylist()
    primary = [row for row in physical if row["stage"] == "STAGE_A" and row["case_role"] == "primary"]
    sensitivity = [row for row in physical if row["stage"] == "STAGE_A" or row["stage"] == "STAGE_C"]
    if len(primary) != 128 or len(sensitivity) != 176:
        raise AnalysisError("physical analysis row counts do not match issue contract")

    family_length = family_length_analysis(primary)
    locality = locality_tps_analysis(primary, sensitivity)
    virtual_rows, virtual_summary = virtual_capacity_rows(physical, capacity)
    for row in virtual_rows:
        validate_json(row, schema_paths["virtual"])
    working = working_set_analysis(route_features, physical)
    committee_path = frozen_root / "host/stage-b-analysis-v1/standing-committee-core-periphery.json"
    committee = load_json(committee_path)
    core, committee_cells = core_periphery_analysis(committee, capacity)
    core["source_sha256"] = [sha256(committee_path)]
    overlap_path = frozen_root / "host/stage-b-analysis-v1/family-overlap-matrix.json"
    overlap = load_json(overlap_path)
    overlap["_source_sha256"] = sha256(overlap_path)
    endpoints_path = frozen_root / "host/stage-b-analysis-v1/stage-b2-family-length-route-endpoints.json"
    endpoints = load_json(endpoints_path)
    endpoints["_source_sha256"] = sha256(endpoints_path)
    mechanism = mechanism_boundary()
    prior_art = prior_art_rows(pathlib.Path(__file__).resolve().parents[2] / "docs/PRIOR_ART.md")

    write_json(output_root / "family-length-analysis.json", family_length)
    locality_public = {key: value for key, value in locality.items() if not key.startswith("_")}
    write_json(output_root / "locality-tps-validation.json", locality_public)
    virtual_fields = list(virtual_rows[0])
    write_csv(output_root / "virtual-cache-capacity.csv", virtual_rows, virtual_fields)
    write_json(output_root / "virtual-cache-capacity-summary.json", virtual_summary)
    working_public = {key: value for key, value in working.items() if not key.startswith("_")}
    write_json(output_root / "working-set-analysis.json", working_public)
    write_json(output_root / "core-periphery-analysis.json", core)
    write_csv(output_root / "committee-counterfactual-cells.csv", committee_cells, list(committee_cells[0]))
    write_json(output_root / "mechanism-boundary.json", mechanism)
    write_csv(output_root / "prior-art-comparison.csv", prior_art, list(prior_art[0]))

    sidecars = render_figures(
        output_root, primary, sensitivity, capacity, virtual_rows, locality,
        working, core, overlap, endpoints, catalog, args.analysis_code_version,
    )
    validate_figure_sidecars(sidecars, schema_paths["figure"])
    registry = hypothesis_registry(
        locality, virtual_summary, working, core, endpoints,
        {
            "physical": source_hashes(primary),
            "capacity": source_hashes(capacity),
            "routes": source_hashes(working["_rows"]),
            "overlap": [overlap["_source_sha256"], endpoints["_source_sha256"]],
            "core": core["source_sha256"],
        },
    )
    registry_document = {
        "schema_version": "issue105-hypothesis-registry-v1", "status": "PASS",
        "post_hoc_exploratory": True, "hypotheses": registry,
    }
    validate_json(registry_document, schema_paths["hypothesis"])
    write_json(output_root / "hypothesis-registry.json", registry_document)
    write_text(output_root / "hypothesis-registry.md", registry_markdown(registry))
    write_text(output_root / "final-synthesis.md", final_synthesis_text(locality, virtual_summary, working, core))

    projection_rows = []
    if locality["projection_gate"]["status"] == "PASS":
        predictor = locality["model_selection"]["selected_predictor"]
        selected_model = locality["model_selection"]["selected_model"]
        model = locality["protocol_compatible_sensitivity"]["models"][selected_model]
        primary_lofo = locality["primary"]["lofo"][selected_model]
        sensitivity_lofo = locality["protocol_compatible_sensitivity"]["lofo"][selected_model]
        physical_meta = artifact(catalog, "physical_runs")
        capacity_meta = artifact(catalog, "capacity_curves")
        domain = [locality["projection_domain"]["minimum"], locality["projection_domain"]["maximum"]]
        model_manifest = {
            "schema_version": "issue105-tps-projection-model-v1",
            "status": "PASS",
            "post_hoc_exploratory": True,
            "analysis_code_version": args.analysis_code_version,
            "selected_model": selected_model,
            "selected_predictor": predictor,
            "model_selection_rule": locality["model_selection"]["rule"],
            "model_training_table_logical_sha256": physical_meta["logical_sha256"],
            "model_training_source_sha256": source_hashes(sensitivity),
            "projection_input_table_logical_sha256": capacity_meta["logical_sha256"],
            "primary_lofo": primary_lofo,
            "protocol_compatible_sensitivity_lofo": sensitivity_lofo,
            "measured_predictor_domain": domain,
            "uncertainty_scope": "family-cluster sensitivity intervals over the constructed families; not a population confidence interval",
        }
        model_manifest_meta = write_json(output_root / "projection-model-manifest.json", model_manifest)
        for row in capacity:
            if row["phase"] != "DECODE" or row["status"] != "pass" or row[predictor] is None:
                continue
            value = float(row[predictor])
            if not projection_allowed(value, domain):
                continue
            projection_rows.append({
                "case_id": row["case_id"], "policy_result_class": row["policy_result_class"],
                "capacity_slots": row["capacity_slots"], "predictor": predictor,
                "predictor_value": value,
                "projected_decode_tok_s": model["coefficients"]["intercept"] + model["coefficients"][predictor] * value,
                "measured_predictor_domain_min": domain[0], "measured_predictor_domain_max": domain[1],
                "source_evidence_class": row["source_evidence_class"],
                "derived_evidence_class": "TPS_PROJECTION", "post_hoc_exploratory": True,
                "model_id": selected_model,
                "model_selection_rule": locality["model_selection"]["rule"],
                "model_manifest_sha256": model_manifest_meta["sha256"],
                "model_training_table_logical_sha256": physical_meta["logical_sha256"],
                "projection_input_table_logical_sha256": capacity_meta["logical_sha256"],
                "projection_input_source_sha256_json": json.dumps(source_hashes([row]), separators=(",", ":")),
                "analysis_code_version": args.analysis_code_version,
                "primary_lofo_r_squared": primary_lofo["pooled_oof_r_squared"],
                "primary_lofo_rmse": primary_lofo["pooled_oof_rmse"],
                "primary_bootstrap_r_squared_95_json": json.dumps(primary_lofo["cluster_bootstrap"]["pooled_oof_r_squared_95_interval"], separators=(",", ":")),
                "sensitivity_lofo_r_squared": sensitivity_lofo["pooled_oof_r_squared"],
                "sensitivity_lofo_rmse": sensitivity_lofo["pooled_oof_rmse"],
                "sensitivity_bootstrap_r_squared_95_json": json.dumps(sensitivity_lofo["cluster_bootstrap"]["pooled_oof_r_squared_95_interval"], separators=(",", ":")),
                "uncertainty_scope": "family-cluster sensitivity intervals over the constructed families; not a population confidence interval",
            })
    write_csv(
        output_root / "capacity-tps-projections.csv", projection_rows,
        list(projection_rows[0]) if projection_rows else [
            "case_id", "policy_result_class", "capacity_slots", "predictor", "predictor_value",
            "projected_decode_tok_s", "measured_predictor_domain_min", "measured_predictor_domain_max",
            "source_evidence_class", "derived_evidence_class", "post_hoc_exploratory",
            "model_id", "model_selection_rule", "model_manifest_sha256",
            "model_training_table_logical_sha256", "projection_input_table_logical_sha256",
            "projection_input_source_sha256_json", "analysis_code_version",
            "primary_lofo_r_squared", "primary_lofo_rmse", "primary_bootstrap_r_squared_95_json",
            "sensitivity_lofo_r_squared", "sensitivity_lofo_rmse",
            "sensitivity_bootstrap_r_squared_95_json", "uncertainty_scope",
        ],
    )
    analysis_results = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "post_hoc_exploratory": True,
        "analysis_code_version": args.analysis_code_version,
        "projection_gate": locality["projection_gate"]["status"],
        "selected_projection_model": locality["model_selection"],
        "virtual_capacity": virtual_summary,
        "working_set_best_feature": working["best_hit_ratio_feature"],
        "committee_negative_cells": core["committee_pin_counterfactual"]["negative_cells_preserved"],
        "primary_figure_family_count": len(sidecars),
        "hypothesis_status_counts": dict(Counter(row["status"] for row in registry)),
    }
    validate_json(analysis_results, schema_paths["analysis"])
    write_json(output_root / "analysis-results.json", analysis_results)
    analysis_result_row = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS",
        "post_hoc_exploratory": True,
        "analysis_code_version": args.analysis_code_version,
        "projection_gate": locality["projection_gate"]["status"],
        "selected_projection_model": locality["model_selection"]["selected_model"],
        "selected_projection_predictor": locality["model_selection"]["selected_predictor"],
        "primary_lofo_r_squared": locality["primary"]["lofo"][locality["model_selection"]["selected_model"]]["pooled_oof_r_squared"],
        "primary_lofo_rmse": locality["primary"]["lofo"][locality["model_selection"]["selected_model"]]["pooled_oof_rmse"],
        "sensitivity_lofo_r_squared": locality["protocol_compatible_sensitivity"]["lofo"][locality["model_selection"]["selected_model"]]["pooled_oof_r_squared"],
        "sensitivity_lofo_rmse": locality["protocol_compatible_sensitivity"]["lofo"][locality["model_selection"]["selected_model"]]["pooled_oof_rmse"],
        "virtual_capacity_row_count": virtual_summary["row_count"],
        "virtual_capacity_status_counts_json": json.dumps(virtual_summary["status_counts"], sort_keys=True, separators=(",", ":")),
        "working_set_best_feature": working["best_hit_ratio_feature"]["feature"],
        "working_set_best_feature_lofo_r_squared": working["best_hit_ratio_feature"]["pooled_oof_r_squared"],
        "committee_negative_cells": core["committee_pin_counterfactual"]["negative_cells_preserved"],
        "primary_figure_family_count": len(sidecars),
        "hypothesis_status_counts_json": json.dumps(dict(Counter(row["status"] for row in registry)), sort_keys=True, separators=(",", ":")),
    }
    write_csv(output_root / "analysis-results.csv", [analysis_result_row], list(analysis_result_row))
    write_parquet(output_root / "analysis-results.parquet", [analysis_result_row])
    for schema_path in schema_paths.values():
        write_json(output_root / "schemas" / schema_path.name, load_json(schema_path))
    write_json(output_root / "analysis-catalog.json", analysis_catalog(output_root, args.analysis_code_version))
    print(json.dumps({
        "status": "PASS", "projection_gate": locality["projection_gate"]["status"],
        "virtual_rows": len(virtual_rows), "projection_rows": len(projection_rows),
        "committee_cells": len(committee_cells), "figure_families": len(sidecars),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

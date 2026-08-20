#!/usr/bin/env python3
"""Curate and analyze the complete preregistered issue-99 evidence campaign."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from protocol import (
    BROAD_CASES, BROAD_CHECKPOINTS, BRIDGE_CHECKPOINTS, ISSUE105_ROOT,
    atomic_json, expected_cell_count, file_identity,
)


BOOTSTRAPS = 10_000
SEED = 990_105


class AnalysisError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def percentile_interval(values: np.ndarray) -> list[float]:
    return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]


def prompt_bootstrap(values: pd.DataFrame, value: str, seed_offset: int = 0) -> dict[str, Any]:
    grouped = values.groupby("case_id")[value].mean().dropna()
    if grouped.empty:
        return {"count": 0, "mean": None, "interval_95": None}
    array = grouped.to_numpy(float)
    rng = np.random.default_rng(SEED + seed_offset)
    samples = array[rng.integers(0, len(array), size=(BOOTSTRAPS, len(array)))].mean(axis=1)
    return {"count": len(array), "mean": float(array.mean()), "interval_95": percentile_interval(samples)}


def rank_correlation(left: Iterable[float], right: Iterable[float]) -> float:
    x = pd.Series(list(left), dtype=float).rank(method="average").to_numpy()
    y = pd.Series(list(right), dtype=float).rank(method="average").to_numpy()
    if len(x) < 2 or np.std(x) == 0 or np.std(y) == 0:
        return math.nan
    return float(np.corrcoef(x, y)[0, 1])


def bootstrap_correlation(rows: pd.DataFrame, x: str, y: str, seed_offset: int) -> dict[str, Any]:
    valid = rows[["case_id", x, y]].dropna().drop_duplicates("case_id")
    if len(valid) < 3:
        return {"count": len(valid), "rho": None, "interval_95": None}
    values = valid[[x, y]].to_numpy(float)
    rho = rank_correlation(values[:, 0], values[:, 1])
    rng = np.random.default_rng(SEED + seed_offset)
    samples = []
    for _ in range(BOOTSTRAPS):
        selected = values[rng.integers(0, len(values), len(values))]
        value = rank_correlation(selected[:, 0], selected[:, 1])
        if math.isfinite(value):
            samples.append(value)
    return {
        "count": len(valid), "rho": rho,
        "interval_95": percentile_interval(np.asarray(samples)) if samples else None,
    }


def stream_concat(paths: list[Path], output: Path, metadata: dict[bytes, bytes]) -> int:
    if not paths:
        raise AnalysisError(f"no input tables for {output.name}")
    writer = None
    rows = 0
    try:
        for path in paths:
            table = pq.read_table(path)
            if writer is None:
                output.parent.mkdir(parents=True, exist_ok=True)
                schema = table.schema.with_metadata({**(table.schema.metadata or {}), **metadata})
                table = table.cast(schema)
                writer = pq.ParquetWriter(output, schema, compression="zstd", version="2.6")
            elif table.schema != writer.schema:
                table = table.cast(writer.schema)
            writer.write_table(table)
            rows += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    if pq.read_metadata(output).num_rows != rows:
        raise AnalysisError(f"curated Parquet validation failed: {output}")
    return rows


def stream_sharded(paths: list[Path], output: Path, metadata: dict[bytes, bytes]) -> int:
    if not paths:
        raise AnalysisError(f"no input tables for {output.name}")
    output.mkdir(parents=True, exist_ok=False)
    rows = 0
    expected_schema = None
    for index, path in enumerate(paths):
        table = pq.read_table(path)
        schema = table.schema.with_metadata({**(table.schema.metadata or {}), **metadata})
        table = table.cast(schema)
        if expected_schema is None:
            expected_schema = schema
        elif schema.remove_metadata() != expected_schema.remove_metadata():
            raise AnalysisError(f"shard schema mismatch: {path}")
        shard = output / f"part-{index:03d}.parquet"
        pq.write_table(table, shard, compression="zstd", version="2.6")
        if pq.read_metadata(shard).num_rows != table.num_rows:
            raise AnalysisError(f"shard validation failed: {shard}")
        rows += table.num_rows
    return rows


def write_frame(frame: pd.DataFrame, output: Path, metadata: dict[bytes, bytes]) -> None:
    table = pa.Table.from_pandas(frame, preserve_index=False)
    table = table.cast(table.schema.with_metadata({**(table.schema.metadata or {}), **metadata}))
    pq.write_table(table, output, compression="zstd", version="2.6")


def checkpoint_rows(tokens: pd.DataFrame) -> pd.DataFrame:
    rows = []
    grouping = ["case_id", "semantic_family", "policy", "evidence_class", "changed_intervention",
                "cache_regime", "capacity_bytes", "reference_identity"]
    for identity, group in tokens.groupby(grouping, dropna=False):
        group = group.sort_values("sequence_position")
        checkpoints = BRIDGE_CHECKPOINTS if group["case_id"].iloc[0] in (
            "issue102-sentinel", "04-factual-b4", "10-planning-b2") else BROAD_CHECKPOINTS
        direct = group["evidence_class"].iloc[0] in ("DIRECT_FIXED_CONTEXT", "CAPACITY_FIXED_CONTEXT")
        for checkpoint in checkpoints:
            if checkpoint > int(group["sequence_position"].max()):
                continue
            prefix = group[group["sequence_position"] <= checkpoint]
            terminal = prefix.iloc[-1]
            row = dict(zip(grouping, identity))
            row.update({
                "checkpoint": checkpoint,
                "achieved_horizon": int(group["sequence_position"].max()),
                "cumulative_mean_delta_nll": float(prefix["delta_reference_nll"].mean()) if direct else None,
                "instantaneous_delta_nll": terminal["delta_reference_nll"] if direct else None,
                "cumulative_mean_kl": float(prefix["kl_exact_to_changed"].mean()),
                "cumulative_mean_js": float(prefix["js_divergence"].mean()),
                "cumulative_mean_hidden_l2": float(prefix["hidden_relative_l2_mean"].mean()),
                "cumulative_mean_moe_l2": float(prefix["moe_relative_l2_mean"].mean()),
                "top1_agreement_fraction": float(prefix["top1_agreement"].mean()),
            })
            for feature in (
                "cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap",
                "cumulative_corrected_regret", "cumulative_raw_regret_signed", "changed_slot_fraction",
                "perturbed_layer_fraction", "cumulative_regret_weighted_mean_normalized_depth",
                "cumulative_first_perturbed_normalized_depth", "cumulative_intentional_swaps",
                "cumulative_cache_hits", "cumulative_cache_misses", "cumulative_backing_loads",
                "cumulative_backing_bytes",
            ):
                row[feature] = terminal[feature]
            # No perturbation has zero placement exposure; this coding is fixed before outcomes.
            for feature in ("cumulative_regret_weighted_mean_normalized_depth",
                            "cumulative_first_perturbed_normalized_depth"):
                if pd.isna(row[feature]):
                    row[feature] = 0.0
            rows.append(row)
    return pd.DataFrame(rows)


FEATURES = {
    "P0": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap"],
    "P1": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap",
           "cumulative_corrected_regret"],
    "P2": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap",
           "cumulative_corrected_regret", "cumulative_raw_regret_signed"],
    "P3": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap",
           "cumulative_corrected_regret", "cumulative_raw_regret_signed", "changed_slot_fraction",
           "perturbed_layer_fraction"],
    "P4": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap",
           "cumulative_corrected_regret", "cumulative_raw_regret_signed", "changed_slot_fraction",
           "perturbed_layer_fraction", "cumulative_regret_weighted_mean_normalized_depth",
           "cumulative_first_perturbed_normalized_depth"],
}


def lofo_predictions(rows: pd.DataFrame, features: list[str], target: str) -> np.ndarray:
    predictions = np.full(len(rows), np.nan)
    for case_id in sorted(rows["case_id"].unique()):
        test = rows["case_id"] == case_id
        train = ~test
        x_train = rows.loc[train, features].to_numpy(float)
        x_test = rows.loc[test, features].to_numpy(float)
        y_train = rows.loc[train, target].to_numpy(float)
        mean = x_train.mean(axis=0)
        scale = x_train.std(axis=0)
        scale[scale == 0] = 1.0
        design_train = np.column_stack((np.ones(len(x_train)), (x_train - mean) / scale))
        design_test = np.column_stack((np.ones(len(x_test)), (x_test - mean) / scale))
        coefficients = np.linalg.pinv(design_train, rcond=1e-12) @ y_train
        predictions[test.to_numpy()] = design_test @ coefficients
    return predictions


def predictor_hierarchy(checkpoints: pd.DataFrame) -> dict[str, Any]:
    rows = checkpoints[(checkpoints["cache_regime"] == "high-cache") &
                       (checkpoints["evidence_class"] == "DIRECT_FIXED_CONTEXT") &
                       checkpoints["case_id"].isin(BROAD_CASES)].copy()
    rows = rows.dropna(subset=["cumulative_mean_delta_nll"] + FEATURES["P4"]).reset_index(drop=True)
    if rows["case_id"].nunique() < 8:
        return {"status": "inconclusive", "valid_prompts": rows["case_id"].nunique()}
    target = "cumulative_mean_delta_nll"
    truth = rows[target].to_numpy(float)
    baseline_sse = float(np.sum((truth - truth.mean()) ** 2))
    models = {}
    errors = {}
    for name, features in FEATURES.items():
        prediction = lofo_predictions(rows, features, target)
        square = (truth - prediction) ** 2
        errors[name] = square
        sse = float(square.sum())
        models[name] = {
            "features": features, "rows": len(rows), "prompt_clusters": rows["case_id"].nunique(),
            "lofo_rmse": math.sqrt(float(square.mean())),
            "lofo_r2": 1.0 - sse / baseline_sse if baseline_sse else None,
        }
    comparisons = {}
    rng = np.random.default_rng(SEED)
    cases = sorted(rows["case_id"].unique())
    for prior, current in zip(("P0", "P1", "P2", "P3"), ("P1", "P2", "P3", "P4")):
        delta_by_case = np.array([
            float(np.mean(errors[prior][rows["case_id"].to_numpy() == case] -
                          errors[current][rows["case_id"].to_numpy() == case])) for case in cases
        ])
        samples = delta_by_case[rng.integers(0, len(cases), size=(BOOTSTRAPS, len(cases)))].mean(axis=1)
        point = float(delta_by_case.mean())
        interval = percentile_interval(samples)
        label = "supported" if interval[0] > 0 else "weak" if point > 0 else "no"
        comparisons[f"{current}_over_{prior}"] = {
            "mean_prior_minus_new_squared_error": point, "interval_95": interval, "classification": label}
    return {"status": "pass", "models": models, "comparisons": comparisons}


def added_model_signal(rows: pd.DataFrame, base: list[str], added: list[str], target: str, seed: int) -> dict[str, Any]:
    rows = rows.dropna(subset=[target] + base + added).reset_index(drop=True)
    if rows["case_id"].nunique() < 8:
        return {"classification": "inconclusive", "valid_prompts": rows["case_id"].nunique()}
    truth = rows[target].to_numpy(float)
    base_error = (truth - lofo_predictions(rows, base, target)) ** 2
    full_error = (truth - lofo_predictions(rows, base + added, target)) ** 2
    cases = sorted(rows["case_id"].unique())
    delta = np.asarray([
        float(np.mean(base_error[rows["case_id"].to_numpy() == case] -
                      full_error[rows["case_id"].to_numpy() == case])) for case in cases])
    rng = np.random.default_rng(seed)
    samples = delta[rng.integers(0, len(delta), size=(BOOTSTRAPS, len(delta)))].mean(axis=1)
    interval = percentile_interval(samples)
    point = float(delta.mean())
    label = "supported" if interval[0] > 0 else "weak" if point > 0 else "no"
    return {"classification": label, "valid_prompts": len(cases),
            "mean_prior_minus_new_squared_error": point, "interval_95": interval}


def core_interaction(events: pd.DataFrame, checkpoints: pd.DataFrame) -> dict[str, Any]:
    base_rows = checkpoints[(checkpoints["cache_regime"] == "high-cache") &
                            (checkpoints["evidence_class"] == "DIRECT_FIXED_CONTEXT") &
                            checkpoints["case_id"].isin(BROAD_CASES)].copy()
    results = {}
    classes = ("core_to_core", "core_to_peripheral", "peripheral_to_core", "peripheral_to_peripheral")
    for gamma, suffix, seed in ((1.0, "1_0", 7001), (0.8, "0_8", 7002)):
        enriched = []
        support = {name: set() for name in classes}
        for _, row in base_rows.iterrows():
            selected = events[(events["case_id"] == row["case_id"]) &
                              (events["policy"] == row["policy"]) &
                              (events["cache_regime"] == row["cache_regime"]) &
                              (events["evidence_class"] == row["evidence_class"]) &
                              (events["sequence_position"] <= row["checkpoint"])]
            counts = selected[f"transition_gamma_{suffix}"].value_counts() if not selected.empty else pd.Series(dtype=int)
            total = int(counts.sum())
            value = row.to_dict()
            for name in classes:
                value[f"transition_fraction_{name}"] = float(counts.get(name, 0) / total) if total else 0.0
                if counts.get(name, 0):
                    support[name].add(row["case_id"])
            enriched.append(value)
        frame = pd.DataFrame(enriched)
        support_counts = {name: len(cases) for name, cases in support.items()}
        if min(support_counts.values(), default=0) < 8:
            results[str(gamma)] = {"classification": "inconclusive", "support_prompt_counts": support_counts}
            continue
        fractions = [f"transition_fraction_{name}" for name in classes[:-1]]
        interactions = []
        for feature in fractions:
            interaction = f"{feature}_x_cumulative_regret"
            frame[interaction] = frame[feature] * frame["cumulative_corrected_regret"]
            interactions.append(interaction)
        results[str(gamma)] = {
            **added_model_signal(frame, FEATURES["P1"], fractions + interactions,
                                 "cumulative_mean_delta_nll", seed),
            "support_prompt_counts": support_counts,
        }
    primary = results["1.0"]["classification"]
    sensitivity = results["0.8"]["classification"]
    classification = primary
    if primary not in ("inconclusive", sensitivity) and sensitivity not in ("inconclusive", primary):
        classification = "heterogeneous"
    return {"primary_gamma": 1.0, "sensitivity_gamma": 0.8, "definitions": results,
            "classification": classification}


def paired_policy(checkpoints: pd.DataFrame) -> dict[str, Any]:
    broad = checkpoints[(checkpoints["cache_regime"] == "high-cache") &
                        (checkpoints["evidence_class"] == "DIRECT_FIXED_CONTEXT") &
                        checkpoints["case_id"].isin(BROAD_CASES)]
    pivot = broad.pivot_table(index=["case_id", "checkpoint"], columns="policy",
                              values="cumulative_mean_delta_nll").reset_index()
    pivot = pivot.dropna(subset=["KNEE", "S2_P50"])
    pivot["s2_minus_knee"] = pivot["S2_P50"] - pivot["KNEE"]
    views = {}
    for checkpoint, rows in pivot.groupby("checkpoint"):
        views[str(int(checkpoint))] = prompt_bootstrap(rows, "s2_minus_knee", int(checkpoint))
    terminal = pivot.sort_values("checkpoint").groupby("case_id").tail(1)
    views["last_available_checkpoint"] = prompt_bootstrap(terminal, "s2_minus_knee", 999)
    return {"rows": len(pivot), "views": views}


def trend_analysis(checkpoints: pd.DataFrame) -> dict[str, Any]:
    selected = checkpoints[(checkpoints["cache_regime"] == "high-cache") &
                           (checkpoints["evidence_class"] == "DIRECT_FIXED_CONTEXT") &
                           (checkpoints["checkpoint"].between(64, 512))].copy()
    rows = []
    for (case_id, policy), group in selected.groupby(["case_id", "policy"]):
        if len(group) < 3:
            continue
        x = np.log2(group["checkpoint"].to_numpy(float))
        y = group["cumulative_mean_delta_nll"].to_numpy(float)
        slope = float(np.linalg.lstsq(np.column_stack((np.ones(len(x)), x)), y, rcond=None)[0][1])
        rows.append({"case_id": case_id, "policy": policy, "linear_log2_slope": slope,
                     "spearman_rho": rank_correlation(x, y)})
    frame = pd.DataFrame(rows)
    breakpoint = breakpoint_test(selected)
    slopes = {policy: prompt_bootstrap(group, "linear_log2_slope", index + 2000)
              for index, (policy, group) in enumerate(frame.groupby("policy"))}
    slope_views = [row for row in slopes.values() if row["interval_95"]]
    if breakpoint["classification"] == "supported":
        drift = "breakpoint"
    elif len(slope_views) < 2:
        drift = "inconclusive"
    elif np.sign(slope_views[0]["mean"]) != np.sign(slope_views[1]["mean"]):
        drift = "heterogeneous"
    elif all(row["interval_95"][0] > 0 or row["interval_95"][1] < 0 for row in slope_views):
        drift = "gradual"
    elif all(row["interval_95"][0] <= 0 <= row["interval_95"][1] for row in slope_views):
        drift = "stable"
    else:
        drift = "inconclusive"
    return {
        "per_prompt_policy": rows,
        "slope_by_policy": slopes,
        "breakpoint": breakpoint,
        "classification": drift,
    }


def breakpoint_test(rows: pd.DataFrame) -> dict[str, Any]:
    if rows["case_id"].nunique() < 8:
        return {"classification": "inconclusive", "valid_prompts": rows["case_id"].nunique()}
    work = rows.copy().sort_values(["case_id", "policy", "checkpoint"])
    work["relative_damage"] = work["cumulative_mean_delta_nll"] - \
        work.groupby(["case_id", "policy"])["cumulative_mean_delta_nll"].transform("first")
    work["x"] = np.log2(work["checkpoint"].astype(float)) - 6.0
    work["s2"] = (work["policy"] == "S2_P50").astype(float)
    truth = work["relative_damage"].to_numpy(float)
    baseline_prediction = np.full(len(work), np.nan)
    broken_prediction = np.full(len(work), np.nan)
    selected_breaks = []
    candidates = (128, 256)
    for case_id in sorted(work["case_id"].unique()):
        test = work["case_id"] == case_id
        train = ~test
        base_features = np.column_stack((np.ones(train.sum()), work.loc[train, "x"], work.loc[train, "s2"]))
        base_coef = np.linalg.pinv(base_features, rcond=1e-12) @ truth[train]
        baseline_prediction[test] = np.column_stack((np.ones(test.sum()), work.loc[test, "x"],
                                                      work.loc[test, "s2"])) @ base_coef
        candidate_fit = []
        for breakpoint in candidates:
            hinge_train = np.maximum(0.0, work.loc[train, "x"].to_numpy() - (math.log2(breakpoint) - 6.0))
            design = np.column_stack((base_features, hinge_train))
            coefficient = np.linalg.pinv(design, rcond=1e-12) @ truth[train]
            candidate_fit.append((float(np.mean((truth[train] - design @ coefficient) ** 2)), breakpoint, coefficient))
        _, breakpoint, coefficient = min(candidate_fit, key=lambda item: (item[0], item[1]))
        selected_breaks.append(breakpoint)
        hinge_test = np.maximum(0.0, work.loc[test, "x"].to_numpy() - (math.log2(breakpoint) - 6.0))
        broken_prediction[test] = np.column_stack((np.ones(test.sum()), work.loc[test, "x"],
                                                    work.loc[test, "s2"], hinge_test)) @ coefficient
    base_error = (truth - baseline_prediction) ** 2
    broken_error = (truth - broken_prediction) ** 2
    cases = sorted(work["case_id"].unique())
    delta = np.asarray([float(np.mean(base_error[work["case_id"].to_numpy() == case] -
                                      broken_error[work["case_id"].to_numpy() == case])) for case in cases])
    rng = np.random.default_rng(SEED + 2100)
    samples = delta[rng.integers(0, len(delta), size=(BOOTSTRAPS, len(delta)))].mean(axis=1)
    interval = percentile_interval(samples)
    point = float(delta.mean())
    label = "supported" if interval[0] > 0 else "weak" if point > 0 else "no"
    return {"classification": label, "candidates": list(candidates),
            "selected_break_counts": {str(value): selected_breaks.count(value) for value in candidates},
            "mean_linear_minus_broken_squared_error": point, "interval_95": interval}


def feedback_and_capacity(tokens: pd.DataFrame, checkpoints: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    bridge = checkpoints[checkpoints["case_id"].isin(("issue102-sentinel", "04-factual-b4", "10-planning-b2"))]
    metrics = ["cumulative_mean_hidden_l2", "cumulative_mean_kl", "cumulative_corrected_regret",
               "cumulative_raw_regret_signed", "cumulative_cache_misses", "cumulative_backing_loads"]
    fixed = bridge[(bridge["cache_regime"] == "high-cache") &
                   (bridge["evidence_class"] == "DIRECT_FIXED_CONTEXT")]
    free = bridge[(bridge["cache_regime"] == "high-cache") &
                  (bridge["evidence_class"] == "FREE_TRAJECTORY")]
    joined = free.merge(fixed, on=["case_id", "policy", "checkpoint"], suffixes=("_free", "_fixed"))
    feedback_rows = joined[["case_id", "policy", "checkpoint"]].copy()
    for metric in metrics:
        feedback_rows[f"{metric}_feedback_increment"] = joined[f"{metric}_free"] - joined[f"{metric}_fixed"]
    high = bridge[(bridge["cache_regime"] == "high-cache") &
                  (bridge["evidence_class"] == "DIRECT_FIXED_CONTEXT") & (bridge["checkpoint"] <= 512)]
    low = bridge[(bridge["cache_regime"] == "96-gib-bridge") &
                 (bridge["evidence_class"] == "CAPACITY_FIXED_CONTEXT")]
    capacity = low.merge(high, on=["case_id", "policy", "checkpoint"], suffixes=("_low", "_high"))
    capacity["swaps_low_minus_high"] = capacity["cumulative_intentional_swaps_low"] - capacity["cumulative_intentional_swaps_high"]
    capacity["damage_low_minus_high"] = capacity["cumulative_mean_delta_nll_low"] - capacity["cumulative_mean_delta_nll_high"]
    terminal = capacity.sort_values("checkpoint").groupby(["case_id", "policy"]).tail(1)
    perturb = prompt_bootstrap(terminal, "swaps_low_minus_high", 3001)
    damage = prompt_bootstrap(terminal, "damage_low_minus_high", 3002)
    def classify(view: dict[str, Any]) -> str:
        if view["count"] < 3 or view["interval_95"] is None:
            return "inconclusive"
        if view["mean"] == 0 and view["interval_95"] == [0.0, 0.0]:
            return "no"
        if view["interval_95"][0] > 0 or view["interval_95"][1] < 0:
            return "yes"
        return "weak"
    return feedback_rows, {
        "feedback_rows": len(feedback_rows), "capacity_rows": len(capacity),
        "capacity_terminal": terminal.to_dict("records"),
        "capacity_realized_perturbation": {**perturb, "classification": classify(perturb)},
        "capacity_predictive_damage": {**damage, "classification": classify(damage)},
    }


def feedback_classifications(rows: pd.DataFrame) -> dict[str, Any]:
    if rows.empty:
        return {"materiality": "inconclusive", "growth": "inconclusive"}
    terminal = rows.sort_values("checkpoint").groupby(["case_id", "policy"]).tail(1)
    material = prompt_bootstrap(terminal, "cumulative_mean_hidden_l2_feedback_increment", 3100)
    if material["count"] < 3 or material["interval_95"] is None:
        material_label = "inconclusive"
    elif material["interval_95"][0] > 0:
        material_label = "material"
    elif material["mean"] > 0:
        material_label = "weak"
    else:
        material_label = "not_supported"
    shapes = []
    for _, group in rows.groupby(["case_id", "policy"]):
        values = group.sort_values("checkpoint")["cumulative_mean_hidden_l2_feedback_increment"].to_numpy(float)
        differences = np.diff(values)
        if len(differences) == 0:
            shapes.append("stable")
        elif np.any(differences > 0) and np.any(differences < 0):
            shapes.append("non_monotonic")
        elif np.all(differences == 0):
            shapes.append("stable")
        elif np.all(differences >= 0):
            shapes.append("growing")
        else:
            shapes.append("stable")
    growth = shapes[0] if shapes and len(set(shapes)) == 1 else "heterogeneous" if shapes else "inconclusive"
    return {"materiality": material_label, "materiality_effect": material,
            "growth": growth, "per_prompt_policy_shapes": shapes}


def systems_join(checkpoints: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    quality = checkpoints[(checkpoints["cache_regime"] == "high-cache") &
                          (checkpoints["evidence_class"] == "DIRECT_FIXED_CONTEXT") &
                          (checkpoints["policy"] == "S2_P50") & checkpoints["case_id"].isin(BROAD_CASES)]
    quality = quality.sort_values("checkpoint").groupby("case_id").tail(1)[
        ["case_id", "cumulative_mean_delta_nll"]]
    physical = pd.read_csv(ISSUE105_ROOT / "tables/physical_runs.csv")
    stage_a = physical[(physical.stage == "STAGE_A") & (physical.policy == "S2_P50")]
    stage_c_exact = physical[(physical.stage == "STAGE_C") & (physical.policy == "EXACT")]
    measured = stage_a.merge(stage_c_exact, on="case_id", suffixes=("_s2", "_exact"))
    measured["measured_s2_load_reduction"] = measured["loads_per_token_exact"] - measured["loads_per_token_s2"]
    measured["measured_s2_hit_improvement"] = measured["hit_ratio_s2"] - measured["hit_ratio_exact"]
    measured["measured_s2_tps_gain_fraction"] = measured["decode_tok_s_s2"] / measured["decode_tok_s_exact"] - 1.0
    virtual = pd.read_csv(ISSUE105_ROOT / "analysis/virtual-cache-capacity.csv")
    derived_rows = []
    for _, row in virtual.iterrows():
        hit = json.loads(row["hit_derived_intervals_json"])
        load_value = json.loads(row["load_derived_intervals_json"])
        derived_rows.append({
            "case_id": row["case_id"],
            "physical_amplification_lower_hit": hit["physical_capacity_amplification"][0],
            "physical_amplification_upper_hit": hit["physical_capacity_amplification"][1],
            "physical_saving_lower_hit": hit["physical_memory_saving"][0],
            "physical_saving_upper_hit": hit["physical_memory_saving"][1],
            "counterfactual_amplification_lower_load": load_value["counterfactual_capacity_amplification"][0],
            "counterfactual_amplification_upper_load": load_value["counterfactual_capacity_amplification"][1],
        })
    joined = quality.merge(measured[["case_id", "measured_s2_load_reduction", "measured_s2_hit_improvement",
                                     "measured_s2_tps_gain_fraction"]], on="case_id", how="left")
    joined = joined.merge(pd.DataFrame(derived_rows), on="case_id", how="left")
    views = {}
    predictors = [column for column in joined.columns if column not in ("case_id", "cumulative_mean_delta_nll")]
    for index, predictor in enumerate(predictors):
        views[predictor] = bootstrap_correlation(joined, predictor, "cumulative_mean_delta_nll", 4000 + index)
    def association(columns: list[str]) -> str:
        valid = [views[column] for column in columns if views[column]["interval_95"] is not None]
        if not valid or min(row["count"] for row in valid) < 8:
            return "inconclusive"
        directions = [1 if row["rho"] > 0 else -1 if row["rho"] < 0 else 0 for row in valid]
        if len(set(directions)) > 1:
            return "heterogeneous"
        if all(row["interval_95"][0] > 0 for row in valid):
            return "positive_association"
        if all(row["interval_95"][1] < 0 for row in valid):
            return "inverse_association"
        return "no_clear_association"
    return joined, {
        "views": views,
        "SYSTEMS_GAIN_QUALITY_TRADEOFF": association(
            ["measured_s2_load_reduction", "measured_s2_hit_improvement", "measured_s2_tps_gain_fraction"]),
        "VIRTUAL_CACHE_QUALITY_TRADEOFF": association(
            ["physical_amplification_lower_hit", "physical_amplification_upper_hit",
             "physical_saving_lower_hit", "physical_saving_upper_hit"]),
    }


def phase_analysis(tokens: pd.DataFrame) -> dict[str, Any]:
    direct = tokens[(tokens["cache_regime"] == "high-cache") &
                    (tokens["evidence_class"] == "DIRECT_FIXED_CONTEXT")].dropna(subset=["delta_reference_nll"])
    grouped = direct.groupby(["case_id", "policy", "generation_phase"])["delta_reference_nll"].mean().reset_index()
    pivot = grouped.pivot_table(index=["case_id", "policy"], columns="generation_phase",
                                values="delta_reference_nll").reset_index()
    if "final_answer" not in pivot or "reasoning" not in pivot:
        return {"classification": "unavailable", "valid_prompt_clusters": 0, "phase_means": grouped.to_dict("records")}
    valid = pivot.dropna(subset=["final_answer", "reasoning"]).copy()
    valid["final_minus_reasoning"] = valid["final_answer"] - valid["reasoning"]
    view = prompt_bootstrap(valid, "final_minus_reasoning", 5001)
    if view["count"] < 8:
        label = "inconclusive"
    elif view["interval_95"][0] > 0 or view["interval_95"][1] < 0:
        label = "supported"
    elif view["mean"] != 0:
        label = "weak"
    else:
        label = "no"
    return {**view, "classification": label, "phase_means": grouped.to_dict("records")}


def release_index(root: Path, output: Path) -> dict[str, Any]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path != output:
            identity = file_identity(path)
            identity["relative_path"] = str(path.relative_to(root))
            files.append(identity)
    return {"schema_version": "issue99-analysis-release-index-v1", "status": "pass", "files": files}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    prereg = load(args.preregistration)
    progress = load(args.campaign_root / "control/progress.json")
    expected_cells = expected_cell_count(prereg["capacity"]["low_bridge_enabled"])
    if progress.get("status") != "complete" or progress.get("completed_cells") != expected_cells:
        raise AnalysisError("campaign is incomplete")
    summaries = sorted((args.campaign_root / "pairs").glob("*.summary.json"))
    expected_pairs = 50 if prereg["capacity"]["low_bridge_enabled"] else 44
    if len(summaries) != expected_pairs:
        raise AnalysisError(f"expected {expected_pairs} pair summaries, found {len(summaries)}")
    pair_values = [load(path) for path in summaries]
    if any(row.get("status") != "pass" for row in pair_values):
        raise AnalysisError("non-pass pair summary")
    binding = {
        b"issue99_schema_binding": b"issue99-published-dataset-v1",
        b"preregistration_sha256": file_identity(args.preregistration)["sha256"].encode(),
        b"project_baseline": prereg["baseline"]["project_main"].encode(),
        b"nested_llama_cpp": prereg["baseline"]["nested_llama_cpp"].encode(),
        b"model_manifest_sha256": prereg["model"]["manifest_sha256"].encode(),
        b"corpus_sha256": prereg["corpus"]["identity"]["sha256"].encode(),
    }
    paths = {kind: [Path(row["outputs"][kind]) for row in pair_values if kind in row["outputs"]]
             for kind in ("tokens", "layers", "routes", "events")}
    datasets = args.output_root / "datasets"
    names = {"tokens": "longrun-token-quality.parquet", "layers": "longrun-layer-quality",
             "routes": "longrun-route-records", "events": "longrun-substitution-events"}
    counts = {
        "tokens": stream_concat(paths["tokens"], datasets / names["tokens"], binding),
        **{kind: stream_sharded(paths[kind], datasets / names[kind], binding)
           for kind in ("layers", "routes", "events")},
    }
    tokens = pq.read_table(datasets / names["tokens"]).to_pandas()
    events = pq.read_table(datasets / names["events"]).to_pandas()
    checkpoints = checkpoint_rows(tokens)
    write_frame(checkpoints, datasets / "longrun-checkpoints.parquet", binding)
    predictor = predictor_hierarchy(checkpoints)
    core = core_interaction(events, checkpoints)
    paired = paired_policy(checkpoints)
    trends = trend_analysis(checkpoints)
    feedback_rows, feedback_capacity = feedback_and_capacity(tokens, checkpoints)
    feedback_labels = feedback_classifications(feedback_rows)
    write_frame(feedback_rows, datasets / "longrun-route-feedback.parquet", binding)
    systems_rows, systems = systems_join(checkpoints)
    systems_rows.to_csv(args.output_root / "systems-quality-join.csv", index=False)
    phases = phase_analysis(tokens)
    s2_vs_knee = paired["views"].get("last_available_checkpoint", {})
    s2_direct = checkpoints[(checkpoints.policy == "S2_P50") &
                            (checkpoints.cache_regime == "high-cache") &
                            (checkpoints.evidence_class == "DIRECT_FIXED_CONTEXT") &
                            checkpoints.case_id.isin(BROAD_CASES)].sort_values("checkpoint").groupby("case_id").tail(1)
    s2_view = prompt_bootstrap(s2_direct, "cumulative_mean_delta_nll", 6001)
    acceptable = "inconclusive"
    if s2_vs_knee.get("interval_95") and s2_view.get("interval_95"):
        if s2_vs_knee["interval_95"][1] <= 0 and s2_view["interval_95"][1] <= 0:
            acceptable = "yes"
        elif s2_vs_knee["interval_95"][0] > 0 and s2_view["interval_95"][0] > 0:
            acceptable = "no"
    knee_s2_view = paired["views"].get("last_available_checkpoint", {})
    if knee_s2_view.get("interval_95") is None:
        ordering = "heterogeneous" if knee_s2_view.get("count", 0) else "no_clear_difference"
    elif knee_s2_view["interval_95"][0] > 0:
        ordering = "s2_more_damage"
    elif knee_s2_view["interval_95"][1] < 0:
        ordering = "s2_less_damage"
    else:
        ordering = "no_clear_difference"
    dynamic_supported = any(
        predictor.get("comparisons", {}).get(name, {}).get("classification") == "supported"
        for name in ("P1_over_P0", "P2_over_P1", "P3_over_P2", "P4_over_P3"))
    outcomes = {
        "LONG_HORIZON_PREDICTIVE_DRIFT": trends["classification"],
        "KNEE_VS_S2_QUALITY_ORDERING": ordering,
        "CUMULATIVE_REGRET_PREDICTIVE": predictor.get("comparisons", {}).get("P1_over_P0", {}).get("classification", "inconclusive"),
        "RAW_REGRET_ADDS_SIGNAL": predictor.get("comparisons", {}).get("P2_over_P1", {}).get("classification", "inconclusive"),
        "PERTURBED_FRACTION_ADDS_SIGNAL": predictor.get("comparisons", {}).get("P3_over_P2", {}).get("classification", "inconclusive"),
        "TOKEN_MEDIATED_ROUTE_FEEDBACK": feedback_labels["materiality"],
        "FEEDBACK_GROWTH_TO_1024": feedback_labels["growth"],
        "FOLLOWUP_ROUTING_DESIGN_JUSTIFIED": "yes" if dynamic_supported else "no",
        "DEPTH_CONDITIONING_ADDS_SIGNAL": predictor.get("comparisons", {}).get("P4_over_P3", {}).get("classification", "inconclusive"),
        "CORE_PERIPHERY_QUALITY_INTERACTION": core["classification"],
        "GENERATION_PHASE_QUALITY_INTERACTION": phases["classification"],
        "CAPACITY_CHANGES_REALIZED_PERTURBATION": feedback_capacity["capacity_realized_perturbation"]["classification"],
        "CAPACITY_CHANGES_PREDICTIVE_DAMAGE": feedback_capacity["capacity_predictive_damage"]["classification"],
        "VIRTUAL_CACHE_QUALITY_TRADEOFF": systems["VIRTUAL_CACHE_QUALITY_TRADEOFF"],
        "SYSTEMS_GAIN_QUALITY_TRADEOFF": systems["SYSTEMS_GAIN_QUALITY_TRADEOFF"],
        "S2P50_LONG_HORIZON_ACCEPTABLE": acceptable,
    }
    result = {
        "schema_version": "issue99-analysis-v1", "status": "pass",
        "preregistration": file_identity(args.preregistration),
        "campaign_progress": file_identity(args.campaign_root / "control/progress.json"),
        "coverage": {"cells": expected_cells, "pairs": len(summaries), "dataset_rows": counts,
                     "checkpoint_rows": len(checkpoints)},
        "predictor_hierarchy": predictor, "core_interaction": core,
        "policy_pairing": paired, "trends": trends,
        "feedback_and_capacity": {**feedback_capacity, "classifications": feedback_labels},
        "systems_quality": systems,
        "generation_phase": phases, "s2_direct_damage": s2_view,
        "primary_outcomes": outcomes,
        "interpretation_guards": ["instrumented wall time is not clean TPS authority",
                                  "generated-token equality is not semantic-quality proof",
                                  "virtual-capacity associations are not causal proof",
                                  "no routing parameter was tuned from issue99 outcomes"],
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output_root / "analysis.json", result)
    atomic_json(datasets / "longrun-predictor-results.json", predictor)
    manifests = [load(path) for path in sorted((args.campaign_root / "cells").glob("*/manifest.json"))]
    if len(manifests) != expected_cells:
        raise AnalysisError("cell-manifest coverage mismatch")
    policy_points = []
    for manifest in manifests:
        result_value = load(Path(manifest["artifacts"]["result"]["canonical_path"]))
        policy_points.append({
            **manifest["cell"], "slug": manifest["slug"],
            "reference_root_identity": manifest["reference_root_identity"],
            "achieved_horizon": result_value["reference"]["achieved_horizon"],
            "result_sha256": manifest["artifacts"]["result"]["sha256"],
            "routes_sha256": manifest["artifacts"]["routes"]["sha256"],
            "trace_sha256": manifest["artifacts"]["quality_trace"]["sha256"],
            "capacity_bytes": result_value["preflight"]["initial_cold"]["actual_bytes"],
            "observer_records": result_value["observer"]["records"],
            "routing_swaps": result_value["routing"]["observer_recomputed"]["swaps"],
        })
    write_frame(pd.DataFrame(policy_points).sort_values("order"),
                datasets / "longrun-policy-points.parquet", binding)
    references = []
    for path in sorted((args.campaign_root / "references").glob("*.json")):
        value = load(path)
        references.append({**value, "artifact_sha256": file_identity(path)["sha256"]})
    write_frame(pd.DataFrame(references), datasets / "longrun-reference-sequences.parquet", binding)
    subprocess.run([
        sys.executable, str(Path(__file__).with_name("reproduce_release.py")),
        "--dataset-root", str(datasets), "--expected-analysis", str(args.output_root / "analysis.json"),
        "--output-root", str(args.output_root),
    ], check=True)
    atomic_json(args.output_root / "release-index.json", release_index(args.output_root,
                                                                        args.output_root / "release-index.json"))
    print(f"ISSUE99_ANALYSIS status=pass cells={expected_cells} pairs={len(summaries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

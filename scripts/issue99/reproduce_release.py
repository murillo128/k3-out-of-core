#!/usr/bin/env python3
"""Reproduce issue-99 primary analysis/figures from published datasets and #105 only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from analyze_campaign import (
    core_interaction, feedback_and_capacity, feedback_classifications, paired_policy,
    phase_analysis, predictor_hierarchy, prompt_bootstrap, systems_join, trend_analysis,
)
from protocol import BROAD_CASES, atomic_json, file_identity


def load(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def calculate(tokens: pd.DataFrame, events: pd.DataFrame, checkpoints: pd.DataFrame) -> dict[str, Any]:
    predictor = predictor_hierarchy(checkpoints)
    core = core_interaction(events, checkpoints)
    paired = paired_policy(checkpoints)
    trends = trend_analysis(checkpoints)
    feedback_rows, feedback_capacity = feedback_and_capacity(tokens, checkpoints)
    feedback_labels = feedback_classifications(feedback_rows)
    systems_rows, systems = systems_join(checkpoints)
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
    if s2_vs_knee.get("interval_95") is None:
        ordering = "heterogeneous" if s2_vs_knee.get("count", 0) else "no_clear_difference"
    elif s2_vs_knee["interval_95"][0] > 0:
        ordering = "s2_more_damage"
    elif s2_vs_knee["interval_95"][1] < 0:
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
    return {
        "predictor_hierarchy": predictor, "core_interaction": core, "policy_pairing": paired,
        "trends": trends, "feedback_and_capacity": {**feedback_capacity, "classifications": feedback_labels},
        "systems_quality": systems, "generation_phase": phases, "s2_direct_damage": s2_view,
        "primary_outcomes": outcomes,
        "feedback_rows": feedback_rows, "systems_rows": systems_rows,
    }


def figure(path: Path, draw: Any, title: str, inputs: dict[str, Any]) -> None:
    plt.rcParams.update({"svg.hashsalt": "issue99", "font.size": 9})
    canvas, axes = plt.subplots(figsize=(6.4, 4.0), constrained_layout=True)
    draw(axes)
    axes.set_title(title)
    canvas.savefig(path, format="svg", metadata={"Date": None})
    plt.close(canvas)
    atomic_json(path.with_suffix(".sidecar.json"), {
        "schema_version": "issue99-figure-sidecar-v1", "title": title,
        "figure": file_identity(path), "inputs": inputs,
    })


def make_figures(
    output: Path,
    checkpoints: pd.DataFrame,
    calculated: dict[str, Any],
    inputs: dict[str, Any],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    broad = checkpoints[(checkpoints.cache_regime == "high-cache") &
                        (checkpoints.evidence_class == "DIRECT_FIXED_CONTEXT") &
                        checkpoints.case_id.isin(BROAD_CASES)]
    def trajectory(axes: Any) -> None:
        for policy, group in broad.groupby("policy"):
            summary = group.groupby("checkpoint")["cumulative_mean_delta_nll"].agg(["mean", "min", "max"])
            axes.plot(summary.index, summary["mean"], marker="o", label=policy)
            axes.fill_between(summary.index, summary["min"], summary["max"], alpha=0.15)
        axes.set(xscale="log", xlabel="decode checkpoint", ylabel="cumulative mean ΔNLL")
        axes.legend()
    figure(output / "figure-01-quality-trajectories.svg", trajectory,
           "Long-horizon fixed-context predictive damage", inputs)

    def predictors(axes: Any) -> None:
        models = calculated["predictor_hierarchy"].get("models", {})
        names = list(models)
        axes.bar(names, [models[name]["lofo_rmse"] for name in names])
        axes.set(xlabel="registered predictor model", ylabel="LOFO RMSE")
    figure(output / "figure-02-predictor-hierarchy.svg", predictors,
           "Held-out dynamic predictor hierarchy", inputs)

    def feedback(axes: Any) -> None:
        rows = calculated["feedback_rows"]
        for (case_id, policy), group in rows.groupby(["case_id", "policy"]):
            group = group.sort_values("checkpoint")
            axes.plot(group["checkpoint"], group["cumulative_mean_hidden_l2_feedback_increment"],
                      marker=".", label=f"{case_id}/{policy}")
        axes.set(xscale="log", xlabel="decode checkpoint", ylabel="free − fixed hidden relative L2")
        axes.legend(fontsize=6)
    figure(output / "figure-03-feedback.svg", feedback,
           "Controlled token-mediated feedback increment", inputs)

    def systems(axes: Any) -> None:
        rows = calculated["systems_rows"].dropna(subset=["measured_s2_load_reduction"])
        axes.scatter(rows["measured_s2_load_reduction"], rows["cumulative_mean_delta_nll"])
        for _, row in rows.iterrows():
            axes.annotate(row["case_id"], (row["measured_s2_load_reduction"],
                                           row["cumulative_mean_delta_nll"]), fontsize=5)
        axes.set(xlabel="frozen #102 S2 load reduction", ylabel="issue-99 S2 cumulative mean ΔNLL")
    figure(output / "figure-04-systems-quality.svg", systems,
           "Frozen systems benefit versus predictive damage", inputs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--expected-analysis", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    tokens_path = args.dataset_root / "longrun-token-quality.parquet"
    events_path = args.dataset_root / "longrun-substitution-events"
    checkpoints_path = args.dataset_root / "longrun-checkpoints.parquet"
    tokens = pq.read_table(tokens_path).to_pandas()
    events = pq.read_table(events_path).to_pandas()
    checkpoints = pq.read_table(checkpoints_path).to_pandas()
    calculated = calculate(tokens, events, checkpoints)
    expected = load(args.expected_analysis)
    comparable = {key: value for key, value in calculated.items() if key not in ("feedback_rows", "systems_rows")}
    for key, value in comparable.items():
        if expected.get(key) != value:
            raise RuntimeError(f"published-dataset reproduction differs: {key}")
    args.output_root.mkdir(parents=True, exist_ok=True)
    atomic_json(args.output_root / "reproduced-primary-analysis.json", comparable)
    checkpoints.to_csv(args.output_root / "primary-checkpoints.csv", index=False)
    inputs = {
        "tokens": file_identity(tokens_path), "checkpoints": file_identity(checkpoints_path),
        "expected_analysis": file_identity(args.expected_analysis),
        "issue105_physical_runs": file_identity(Path("results/2026-08-17/issue105/tables/physical_runs.csv")),
        "issue105_virtual_capacity": file_identity(
            Path("results/2026-08-17/issue105/analysis/virtual-cache-capacity.csv")),
    }
    make_figures(args.output_root / "figures", checkpoints, calculated, inputs)
    atomic_json(args.output_root / "reproduction.json", {
        "schema_version": "issue99-published-dataset-reproduction-v1", "status": "pass",
        "model_or_original_host_required": False, "compared_sections": sorted(comparable), "inputs": inputs,
    })
    print(f"ISSUE99_REPRODUCTION status=pass output={args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

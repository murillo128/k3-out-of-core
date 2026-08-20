#!/usr/bin/env python3
"""Figure 5: physical backing loads/token versus TPS plus authorized LOFO validation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from common import BLUE, GREEN, ORANGE, PURPLE, RED, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, require_file, save_figure


PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"
ANALYSIS = "results/2026-08-17/issue105/analysis/locality-tps-validation.json"
ANALYSIS_SHA256 = "8e81c4cfe22bc59ab65fb6df475515efe72dd23d3d409a46a3765161db0e0f9e"


def main() -> None:
    configure()
    frame = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    primary = frame[(frame.stage == "STAGE_A") & (frame.case_role == "primary") & (frame.policy == "S2_P50")].copy()
    assert_equal(len(primary), 128, "primary physical rows")
    assert_equal(primary.semantic_family.nunique(), 16, "primary family count")
    assert_set(primary.source_evidence_class, ["MEASURED_PHYSICAL"], "primary evidence class")
    assert_equal(analysis["model_selection"]["selected_predictor"], "loads_per_token", "selected predictor")
    model = analysis["primary"]["models"]["M2"]
    lofo = analysis["primary"]["lofo"]["M2"]
    assert_equal(model["row_count"], 128, "M2 row count")
    assert_equal(lofo["fold_count"], 16, "LOFO fold count")
    assert_equal(sum(item["count"] for item in lofo["family_residuals"]), 128, "LOFO residual rows")
    intercept = float(model["coefficients"]["intercept"])
    slope = float(model["coefficients"]["loads_per_token"])
    lofo_r2 = float(lofo["pooled_oof_r_squared"])
    lofo_rmse = float(lofo["pooled_oof_rmse"])

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.15), gridspec_kw={"width_ratios": [1.35, 1.0]})
    family_codes = pd.Categorical(primary.semantic_family).codes
    palette = [BLUE, ORANGE, GREEN, RED, PURPLE]
    colors = [palette[value % len(palette)] for value in family_codes]
    markers = ["o", "s", "^", "D"]
    for level, marker in zip(range(1, 9), markers * 2):
        subset = primary[primary.length_level.astype(int) == level]
        subset_colors = [colors[index] for index in subset.index.map(lambda value: primary.index.get_loc(value))]
        ax_a.scatter(
            subset.loads_per_token,
            subset.decode_tok_s,
            s=18,
            marker=marker,
            c=subset_colors,
            edgecolors="white",
            linewidths=0.25,
            alpha=0.82,
        )
    xline = np.linspace(primary.loads_per_token.min(), primary.loads_per_token.max(), 200)
    ax_a.plot(xline, intercept + slope * xline, color="#111111", lw=1.4, label="pooled linear fit")
    ax_a.set_xlabel("physical backing loads/token")
    ax_a.set_ylabel("physical decode TPS")
    ax_a.grid(color="#E6E6E6", lw=0.6)
    ax_a.text(
        0.03,
        0.04,
        "points: 16 families × 8 levels\ncolor + marker encode workload variation",
        transform=ax_a.transAxes,
        fontsize=6.8,
        va="bottom",
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "points: MEASURED_PHYSICAL\nfit: POST_HOC_EXPLORATORY")

    residuals = sorted(lofo["family_residuals"], key=lambda item: item["mean_signed_residual"])
    y = np.arange(len(residuals))
    means = np.array([item["mean_signed_residual"] for item in residuals])
    maes = np.array([item["mean_absolute_residual"] for item in residuals])
    short_labels = {
        "mathematical reasoning": "math",
        "formal logic / proof-style reasoning": "formal logic",
        "physics / scientific reasoning": "science",
        "factual / explanatory knowledge": "factual",
        "code generation": "codegen",
        "debugging / code review": "debugging",
        "algorithms / data-structure reasoning": "algorithms",
        "summarization / synthesis": "summary",
        "structured extraction / transformation": "extraction",
        "planning / constraint satisfaction": "planning",
        "multi-step instruction following / structured response": "instructions",
        "analytical comparison / argumentation": "comparison",
        "creative / language generation": "creative",
        "conversational / direct QA": "direct QA",
        "Spanish-language reasoning/explanation": "Spanish",
        "multilingual / translation / cross-language transformation": "multilingual",
    }
    labels = [short_labels[item["semantic_family"]] for item in residuals]
    ax_b.axvline(0, color="#333333", lw=0.8)
    ax_b.hlines(y, means - maes, means + maes, color="#BBBBBB", lw=1)
    ax_b.scatter(means, y, marker="D", s=18, color=PURPLE, edgecolors="white", linewidths=0.3)
    ax_b.set_yticks(y, labels)
    ax_b.set_xlabel("LOFO family residual (decode TPS)\npoint = mean; bar = ±MAE")
    ax_b.grid(axis="x", color="#E6E6E6", lw=0.6)
    ax_b.text(
        0.97,
        0.05,
        f"LOFO R² = {lofo_r2:.6f}\nLOFO RMSE = {lofo_rmse:.6f} TPS\n16 held-out-family folds",
        transform=ax_b.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.7,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.84, "pad": 1.5},
    )
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "POST_HOC_EXPLORATORY")
    fig.subplots_adjust(wspace=0.34)
    save_figure(fig, "fig05-loads-to-tps")


if __name__ == "__main__":
    main()

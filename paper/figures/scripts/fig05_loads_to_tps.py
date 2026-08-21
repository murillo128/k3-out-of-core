#!/usr/bin/env python3
"""Physical backing loads/token versus TPS plus authorized LOFO validation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from common import assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, require_file, save_figure


PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"
ANALYSIS = "results/2026-08-17/issue105/analysis/locality-tps-validation.json"
ANALYSIS_SHA256 = "8e81c4cfe22bc59ab65fb6df475515efe72dd23d3d409a46a3765161db0e0f9e"


def main(stem: str = "fig05-loads-to-tps") -> None:
    configure()
    frame = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    primary = frame[(frame.stage == "STAGE_A") & (frame.case_role == "primary") & (frame.policy == "S2_P50")].copy()
    sensitivity = frame[
        (
            (frame.stage == "STAGE_A")
            & (frame.case_role == "primary")
            & (frame.policy == "S2_P50")
        )
        | (
            (frame.stage == "STAGE_C")
            & (frame.case_role == "primary")
            & frame.policy.isin(["EXACT", "KNEE"])
        )
    ].copy()
    assert_equal(len(primary), 128, "primary physical rows")
    assert_equal(primary.semantic_family.nunique(), 16, "primary family count")
    assert_equal(primary.length_level.nunique(), 8, "primary length-level count")
    assert_equal(
        primary.groupby("semantic_family").size().unique().tolist(),
        [8],
        "primary rows per family",
    )
    assert_set(primary.source_evidence_class, ["MEASURED_PHYSICAL"], "primary evidence class")
    assert_equal(len(sensitivity), 176, "protocol-compatible sensitivity rows")
    assert_equal(sensitivity.case_id.nunique(), 128, "sensitivity unique cases")
    assert_equal(sensitivity.semantic_family.nunique(), 16, "sensitivity family count")
    assert_equal(
        sensitivity.groupby("policy").size().to_dict(),
        {"EXACT": 24, "KNEE": 24, "S2_P50": 128},
        "sensitivity policy rows",
    )
    stage_c = sensitivity[sensitivity.stage == "STAGE_C"]
    assert_equal(len(stage_c), 48, "Stage-C sensitivity rows")
    assert_equal(stage_c.case_id.nunique(), 24, "Stage-C sensitivity cases")
    assert_equal(
        stage_c.groupby("case_id").policy.nunique().unique().tolist(),
        [2],
        "Stage-C policies per case",
    )
    assert_equal(
        stage_c[stage_c.policy == "EXACT"].case_id.tolist(),
        stage_c[stage_c.policy == "KNEE"].case_id.tolist(),
        "matched Stage-C case order",
    )
    assert_equal(
        int(sensitivity.duplicated(subset=["case_id", "policy"]).sum()),
        0,
        "duplicate sensitivity case-policy rows",
    )
    assert_set(
        sensitivity.source_evidence_class,
        ["MEASURED_PHYSICAL"],
        "sensitivity evidence class",
    )
    assert_equal(analysis["model_selection"]["selected_predictor"], "loads_per_token", "selected predictor")
    model = analysis["primary"]["models"]["M2"]
    lofo = analysis["primary"]["lofo"]["M2"]
    sensitivity_analysis = analysis["protocol_compatible_sensitivity"]
    sensitivity_model = sensitivity_analysis["models"]["M2"]
    sensitivity_lofo = sensitivity_analysis["lofo"]["M2"]
    assert_equal(model["row_count"], 128, "M2 row count")
    assert_equal(lofo["fold_count"], 16, "LOFO fold count")
    assert_equal(sum(item["count"] for item in lofo["family_residuals"]), 128, "LOFO residual rows")
    assert_equal(sensitivity_analysis["row_count"], 176, "sensitivity analysis row count")
    assert_equal(sensitivity_analysis["unique_case_count"], 128, "sensitivity analysis case count")
    assert_equal(
        sensitivity_analysis["policy_counts"],
        {"EXACT": 24, "KNEE": 24, "S2_P50": 128},
        "sensitivity analysis policy counts",
    )
    assert_equal(sensitivity_model["row_count"], 176, "sensitivity M2 row count")
    assert_equal(sensitivity_lofo["fold_count"], 16, "sensitivity LOFO fold count")
    assert_equal(
        sum(item["count"] for item in sensitivity_lofo["family_residuals"]),
        176,
        "sensitivity LOFO residual rows",
    )
    assert_equal(
        float(analysis["projection_domain"]["minimum"]),
        float(sensitivity.loads_per_token.min()),
        "sensitivity predictor minimum",
    )
    assert_equal(
        float(analysis["projection_domain"]["maximum"]),
        float(sensitivity.loads_per_token.max()),
        "sensitivity predictor maximum",
    )
    intercept = float(model["coefficients"]["intercept"])
    slope = float(model["coefficients"]["loads_per_token"])
    lofo_r2 = float(lofo["pooled_oof_r_squared"])
    lofo_rmse = float(lofo["pooled_oof_rmse"])
    sensitivity_intercept = float(sensitivity_model["coefficients"]["intercept"])
    sensitivity_slope = float(sensitivity_model["coefficients"]["loads_per_token"])
    sensitivity_lofo_r2 = float(sensitivity_lofo["pooled_oof_r_squared"])
    sensitivity_lofo_rmse = float(sensitivity_lofo["pooled_oof_rmse"])

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.35), gridspec_kw={"width_ratios": [1.35, 1.0]})
    families = sorted(primary.semantic_family.unique())
    family_colors = {
        family: plt.get_cmap("tab20")(index)
        for index, family in enumerate(families)
    }
    policy_markers = {"S2_P50": "o", "EXACT": "s", "KNEE": "^"}
    for policy in ("S2_P50", "EXACT", "KNEE"):
        subset = sensitivity[sensitivity.policy == policy]
        ax_a.scatter(
            subset.loads_per_token,
            subset.decode_tok_s,
            s=18 if policy == "S2_P50" else 25,
            marker=policy_markers[policy],
            c=[family_colors[family] for family in subset.semantic_family],
            edgecolors="#222222" if policy != "S2_P50" else "white",
            linewidths=0.55 if policy != "S2_P50" else 0.25,
            alpha=0.76 if policy == "S2_P50" else 0.92,
            zorder=3 if policy != "S2_P50" else 2,
        )
    xline = np.linspace(primary.loads_per_token.min(), primary.loads_per_token.max(), 200)
    ax_a.plot(
        xline,
        intercept + slope * xline,
        color="#111111",
        lw=1.4,
        label="primary fit (128 S2_P50)",
        zorder=4,
    )
    sensitivity_xline = np.linspace(sensitivity.loads_per_token.min(), sensitivity.loads_per_token.max(), 200)
    ax_a.plot(
        sensitivity_xline,
        sensitivity_intercept + sensitivity_slope * sensitivity_xline,
        color="#666666",
        lw=1.15,
        linestyle="--",
        label="sensitivity fit (176 rows)",
        zorder=4,
    )
    ax_a.set_xlabel("physical backing loads/token")
    ax_a.set_ylabel("physical decode TPS")
    ax_a.grid(color="#E6E6E6", lw=0.6)
    legend_handles = [
        Line2D(
            [],
            [],
            color="none",
            marker=policy_markers[policy],
            markerfacecolor="#BBBBBB",
            markeredgecolor="#222222",
            markeredgewidth=0.55,
            markersize=4.7,
            label=f"{policy} ({len(sensitivity[sensitivity.policy == policy])})",
        )
        for policy in ("S2_P50", "EXACT", "KNEE")
    ]
    legend_handles += [
        Line2D([], [], color="#111111", lw=1.4, label="primary fit (128)"),
        Line2D([], [], color="#666666", lw=1.15, linestyle="--", label="sensitivity fit (176)"),
    ]
    ax_a.legend(handles=legend_handles, loc="lower left", frameon=True, ncol=1, borderpad=0.35)
    ax_a.text(
        0.03,
        0.41,
        "color = semantic family (named in B)\nmarker = policy; all 176 points retained",
        transform=ax_a.transAxes,
        fontsize=6.8,
        va="bottom",
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "points: MEASURED_PHYSICAL\nfit: POST_HOC_EXPLORATORY")

    residuals = sorted(lofo["family_residuals"], key=lambda item: item["mean_signed_residual"])
    sensitivity_by_family = {
        item["semantic_family"]: item for item in sensitivity_lofo["family_residuals"]
    }
    y = np.arange(len(residuals))
    means = np.array([item["mean_signed_residual"] for item in residuals])
    maes = np.array([item["mean_absolute_residual"] for item in residuals])
    sensitivity_means = np.array(
        [sensitivity_by_family[item["semantic_family"]]["mean_signed_residual"] for item in residuals]
    )
    sensitivity_maes = np.array(
        [sensitivity_by_family[item["semantic_family"]]["mean_absolute_residual"] for item in residuals]
    )
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
    for index, item in enumerate(residuals):
        color = family_colors[item["semantic_family"]]
        ax_b.hlines(index + 0.13, means[index] - maes[index], means[index] + maes[index], color=color, lw=1.0)
        ax_b.hlines(
            index - 0.13,
            sensitivity_means[index] - sensitivity_maes[index],
            sensitivity_means[index] + sensitivity_maes[index],
            color=color,
            lw=1.0,
            linestyle="--",
        )
        ax_b.scatter(
            means[index], index + 0.13, marker="D", s=17, color=color,
            edgecolors="#222222", linewidths=0.3, zorder=3,
        )
        ax_b.scatter(
            sensitivity_means[index], index - 0.13, marker="o", s=17, color=color,
            edgecolors="#222222", linewidths=0.3, zorder=3,
        )
    ax_b.set_yticks(y, labels)
    ax_b.set_ylim(-5.0, 15.8)
    ax_b.set_xlabel("LOFO family residual (decode TPS)\npoint = mean; bar = ±MAE")
    ax_b.grid(axis="x", color="#E6E6E6", lw=0.6)
    ax_b.legend(
        handles=[
            Line2D([], [], color="#555555", marker="D", lw=1, markersize=4, label="primary: 128 S2_P50"),
            Line2D([], [], color="#555555", marker="o", lw=1, linestyle="--", markersize=4, label="sensitivity: 128/24/24"),
        ],
        loc="lower left",
        bbox_to_anchor=(0.0, 1.01),
        frameon=True,
        borderpad=0.35,
    )
    ax_b.text(
        0.97,
        0.05,
        f"primary: R² {lofo_r2:.6f}; RMSE {lofo_rmse:.6f}\n"
        f"sensitivity: R² {sensitivity_lofo_r2:.6f}; RMSE {sensitivity_lofo_rmse:.6f}\n"
        "16 held-out-family folds",
        transform=ax_b.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.7,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.84, "pad": 1.5},
    )
    panel_label(ax_b, "B")
    ax_b.text(
        1.035,
        0.5,
        "POST_HOC_EXPLORATORY",
        transform=ax_b.transAxes,
        rotation=90,
        ha="left",
        va="center",
        fontsize=6.4,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": "white", "edgecolor": "#777777", "lw": 0.7},
    )
    fig.subplots_adjust(wspace=0.34)
    save_figure(fig, stem)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Figure 10: negative predictor decision and frozen systems–quality association."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from common import BLUE, GRAY, ORANGE, PURPLE, RED, assert_equal, configure, evidence_badge, panel_label, read_json, require_file, save_figure


PREDICTORS = "results/2026-08-20/issue99/analysis/datasets/longrun-predictor-results.json"
PREDICTORS_SHA256 = "e08470e8f98eb52f76d43b0dafe70711f534406b1e30ff57d6039c2aa2fcdb5f"
JOIN = "results/2026-08-20/issue99/analysis/systems-quality-join.csv"
JOIN_SHA256 = "8be3c7aaa994bd327ad505b4e30d8397f559e5c0539756a299c31b14933220ec"
ANALYSIS = "results/2026-08-20/issue99/analysis/analysis.json"
ANALYSIS_SHA256 = "fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c"


def main() -> None:
    configure()
    predictors = read_json(PREDICTORS, PREDICTORS_SHA256)
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    joined = pd.read_csv(require_file(JOIN, JOIN_SHA256))
    model_order = ["P0", "P1", "P2", "P3", "P4"]
    assert_equal(list(predictors["models"]), model_order, "registered predictor models")
    for model in model_order:
        assert_equal(predictors["models"][model]["rows"], 192, f"{model} rows")
        assert_equal(predictors["models"][model]["prompt_clusters"], 16, f"{model} clusters")
    expected_classes = {"P1_over_P0": "weak", "P2_over_P1": "weak", "P3_over_P2": "no", "P4_over_P3": "no"}
    assert_equal(
        {name: row["classification"] for name, row in predictors["comparisons"].items()},
        expected_classes,
        "incremental predictor classifications",
    )
    outcomes = analysis["primary_outcomes"]
    assert_equal(outcomes["FOLLOWUP_ROUTING_DESIGN_JUSTIFIED"], "no", "follow-up controller decision")
    assert_equal(outcomes["SYSTEMS_GAIN_QUALITY_TRADEOFF"], "inverse_association", "systems–quality classification")
    assert_equal(len(joined), 16, "systems–quality joined prompts")
    assert_equal(joined.case_id.nunique(), 16, "systems–quality unique prompts")
    view = analysis["systems_quality"]["views"]["measured_s2_load_reduction"]
    rho = joined.measured_s2_load_reduction.rank().corr(joined.cumulative_mean_delta_nll.rank())
    if not np.isclose(rho, view["rho"]):
        raise AssertionError("systems–quality Spearman association changed")

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.45), gridspec_kw={"width_ratios": [0.95, 1.35]})
    rmse = [predictors["models"][model]["lofo_rmse"] for model in model_order]
    colors = [GRAY, ORANGE, ORANGE, RED, RED]
    hatches = ["", "//", "//", "xx", "xx"]
    bars = ax_a.bar(np.arange(5), rmse, color=colors, edgecolor="#333333", hatch=hatches)
    ax_a.set_xticks(
        np.arange(5),
        ["P0\nlocal", "P1\n+corr. cum.", "P2\n+raw", "P3\n+fraction", "P4\n+depth"],
        rotation=28,
        ha="right",
    )
    ax_a.set_ylabel("LOFO RMSE for cumulative ΔNLL")
    ax_a.set_ylim(0, max(rmse) * 1.18)
    ax_a.grid(axis="y", color="#E5E5E5", lw=0.6)
    for index, (bar, classification) in enumerate(zip(bars, ["baseline", "weak", "weak", "no", "no"])):
        ax_a.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.00012, classification, ha="center", fontsize=6.4)
    ax_a.text(
        0.5,
        -0.30,
        "held-out evidence did not justify\na cumulative/depth safety controller",
        transform=ax_a.transAxes,
        ha="center",
        fontsize=6.7,
        color=PURPLE,
        va="top",
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "DIRECT_FIXED_CONTEXT\nheld-out POST_HOC analysis")

    ax_b.axhline(0, color="#333333", lw=0.8)
    ax_b.scatter(
        joined.measured_s2_load_reduction,
        joined.cumulative_mean_delta_nll,
        s=28,
        marker="o",
        facecolors="white",
        edgecolors=BLUE,
        lw=1.0,
    )
    for row in joined.itertuples(index=False):
        ax_b.annotate(row.case_id.split("-")[0], (row.measured_s2_load_reduction, row.cumulative_mean_delta_nll), xytext=(2, 2), textcoords="offset points", fontsize=5.7)
    ax_b.set_xlabel("measured S2_P50 backing-load reduction (loads/token)")
    ax_b.set_ylabel("fixed-context cumulative mean ΔNLL")
    ax_b.grid(color="#E5E5E5", lw=0.6)
    ax_b.text(
        0.03,
        0.06,
        f"Spearman ρ = {view['rho']:.3f}\n95% prompt-bootstrap interval [{view['interval_95'][0]:.3f}, {view['interval_95'][1]:.3f}]\nclassification: inverse association; not a causal law",
        transform=ax_b.transAxes,
        ha="left",
        va="bottom",
        fontsize=6.5,
    )
    panel_label(ax_b, "B")
    evidence_badge(
        ax_b,
        "systems: MEASURED_PHYSICAL\nquality: DIRECT_FIXED_CONTEXT\njoin: POST_HOC_EXPLORATORY",
        x=0.70,
    )
    fig.subplots_adjust(wspace=0.35, bottom=0.30)
    save_figure(fig, "fig10-decision-quality")


if __name__ == "__main__":
    main()

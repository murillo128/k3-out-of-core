#!/usr/bin/env python3
"""Figure 9: fixed-context predictive damage and controlled feedback."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue99 import ensure_issue99_inputs  # noqa: E402
from scripts.common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, save_figure  # noqa: E402


PREREG = "results/2026-08-17/issue99/checkpoint-a/preregistration.json"
PREREG_SHA256 = "73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba"
ANALYSIS = "results/2026-08-20/issue99/analysis/analysis.json"
ANALYSIS_SHA256 = "fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c"


def main() -> None:
    configure()
    prereg = read_json(PREREG, PREREG_SHA256)
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    checkpoints = pd.read_parquet(ensure_issue99_inputs())
    assert_equal(len(checkpoints), 312, "checkpoint rows")
    broad_cases = prereg["cohorts"]["broad"]
    bridge_cases = prereg["cohorts"]["bridge"]
    assert_equal(len(broad_cases), 16, "broad cohort prompts")
    assert_equal(bridge_cases, ["issue102-sentinel", "04-factual-b4", "10-planning-b2"], "bridge prompts")

    broad = checkpoints[
        (checkpoints.cache_regime == "high-cache")
        & (checkpoints.evidence_class == "DIRECT_FIXED_CONTEXT")
        & checkpoints.case_id.isin(broad_cases)
    ].copy()
    assert_equal(len(broad), 192, "broad fixed-context rows")
    assert_set(broad.policy, ["KNEE", "S2_P50"], "broad policies")
    assert_set(broad.checkpoint, [16, 32, 64, 128, 256, 512], "broad checkpoints")
    broad_cells = broad.groupby(["case_id", "policy"]).size()
    if len(broad_cells) != 32 or not (broad_cells == 6).all():
        raise AssertionError("broad evidence must retain 16 prompts × 2 policies × 6 checkpoints")

    bridge = checkpoints[(checkpoints.cache_regime == "high-cache") & checkpoints.case_id.isin(bridge_cases)].copy()
    assert_equal(len(bridge), 84, "bridge rows")
    assert_set(bridge.policy, ["KNEE", "S2_P50"], "bridge policies")
    assert_set(bridge.evidence_class, ["DIRECT_FIXED_CONTEXT", "FREE_TRAJECTORY"], "bridge evidence classes")
    assert_set(bridge.checkpoint, [16, 32, 64, 128, 256, 512, 1024], "bridge checkpoints")
    bridge_cells = bridge.groupby(["case_id", "policy", "evidence_class"]).size()
    if len(bridge_cells) != 12 or not (bridge_cells == 7).all():
        raise AssertionError("bridge evidence must retain 3 × 2 × 2 × 7 cells")

    fig = plt.figure(figsize=(7.2, 6.0))
    grid = fig.add_gridspec(2, 3, height_ratios=[1.18, 1.0], hspace=0.42, wspace=0.18)
    ax_a = fig.add_subplot(grid[0, :])
    policy_styles = {"KNEE": (ORANGE, "s"), "S2_P50": (BLUE, "o")}
    for policy, (color, marker) in policy_styles.items():
        group = broad[broad.policy == policy]
        for _, trajectory in group.groupby("case_id"):
            trajectory = trajectory.sort_values("checkpoint")
            ax_a.plot(trajectory.checkpoint, trajectory.cumulative_mean_delta_nll, color=color, alpha=0.15, lw=0.65)
        summary = group.groupby("checkpoint")["cumulative_mean_delta_nll"].agg(["mean", "min", "max"])
        ax_a.fill_between(summary.index, summary["min"], summary["max"], color=color, alpha=0.12, linewidth=0)
        ax_a.plot(summary.index, summary["mean"], color=color, marker=marker, ms=4, lw=1.5, label=f"{policy} mean")
    direct = analysis["s2_direct_damage"]
    ax_a.axhline(0, color="#333333", lw=0.8)
    ax_a.set_xscale("log", base=2)
    ax_a.set_xticks([16, 32, 64, 128, 256, 512], ["16", "32", "64", "128", "256", "512"])
    ax_a.set_xlabel("decode horizon checkpoint")
    ax_a.set_ylabel("cumulative mean ΔNLL vs EXACT")
    ax_a.grid(color="#E5E5E5", lw=0.6)
    ax_a.legend(frameon=False, loc="upper left", ncol=2)
    ax_a.text(
        0.99,
        0.04,
        f"S2_P50 terminal prompt mean = {direct['mean']:.4f}\n95% prompt-bootstrap interval [{direct['interval_95'][0]:.4f}, {direct['interval_95'][1]:.4f}]\nall 16 prompt trajectories + observed min–max envelope",
        transform=ax_a.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.8,
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "DIRECT_FIXED_CONTEXT")

    evidence_styles = {"DIRECT_FIXED_CONTEXT": "-", "FREE_TRAJECTORY": "--"}
    bridge_axes = [fig.add_subplot(grid[1, i]) for i in range(3)]
    bridge_y_max = float(bridge.cumulative_mean_hidden_l2.max())
    for index, (ax, case_id) in enumerate(zip(bridge_axes, bridge_cases)):
        case = bridge[bridge.case_id == case_id]
        for (policy, evidence), group in case.groupby(["policy", "evidence_class"]):
            group = group.sort_values("checkpoint")
            color, marker = policy_styles[policy]
            ax.plot(
                group.checkpoint,
                group.cumulative_mean_hidden_l2,
                color=color,
                marker=marker,
                ms=2.8,
                lw=1.15,
                ls=evidence_styles[evidence],
            )
        ax.set_xscale("log", base=2)
        ax.set_xticks([16, 64, 256, 1024], ["16", "64", "256", "1024"])
        ax.set_ylim(0, bridge_y_max * 1.08)
        ax.set_xlabel("decode checkpoint")
        ax.grid(color="#E5E5E5", lw=0.6)
        display_case = "sentinel" if case_id == "issue102-sentinel" else case_id
        ax.text(0.04, 0.95, display_case, transform=ax.transAxes, va="top", fontsize=6.5, fontweight="bold")
        panel_label(ax, chr(ord("B") + index))
        if index == 0:
            ax.set_ylabel("cumulative mean hidden relative L2 vs EXACT")
        else:
            ax.tick_params(labelleft=False)
    handles = [
        Line2D([], [], color=BLUE, marker="o", label="S2_P50"),
        Line2D([], [], color=ORANGE, marker="s", label="KNEE"),
        Line2D([], [], color="#333333", ls="-", label="DIRECT_FIXED_CONTEXT"),
        Line2D([], [], color="#333333", ls="--", label="FREE_TRAJECTORY"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 0.055))
    materiality = analysis["feedback_and_capacity"]["classifications"]["materiality_effect"]
    fig.text(
        0.5,
        0.01,
        f"Registered controlled-perturbation amplification = {materiality['mean']:.4f}× "
        f"(95% prompt-bootstrap interval {materiality['interval_95'][0]:.4f}–{materiality['interval_95'][1]:.4f}); "
        "this is not a multiplicative quality score.",
        ha="center",
        fontsize=6.7,
    )
    fig.subplots_adjust(bottom=0.15)
    save_figure(fig, "fig09-quality-feedback")


if __name__ == "__main__":
    main()

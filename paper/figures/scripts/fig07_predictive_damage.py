#!/usr/bin/env python3
"""Figure 7: reviewed #99 long-horizon fixed-context ΔNLL trajectories."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue99 import ensure_issue99_inputs  # noqa: E402
from scripts.common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, read_json, save_figure  # noqa: E402


PREREG = "results/2026-08-17/issue99/checkpoint-a/preregistration.json"
PREREG_SHA256 = "73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba"
ANALYSIS = "results/2026-08-20/issue99/analysis/analysis.json"
ANALYSIS_SHA256 = "fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c"


def main() -> None:
    configure()
    prereg = read_json(PREREG, PREREG_SHA256)
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    checkpoints = pd.read_parquet(ensure_issue99_inputs())
    assert_equal(len(checkpoints), 312, "#99 checkpoint rows")
    broad_cases = prereg["cohorts"]["broad"]
    assert_equal(len(broad_cases), 16, "broad cohort size")
    broad = checkpoints[
        (checkpoints.cache_regime == "high-cache")
        & (checkpoints.evidence_class == "DIRECT_FIXED_CONTEXT")
        & checkpoints.case_id.isin(broad_cases)
    ].copy()
    assert_equal(len(broad), 192, "broad fixed-context rows")
    assert_equal(broad.case_id.nunique(), 16, "broad fixed-context prompt count")
    assert_set(broad.policy, ["KNEE", "S2_P50"], "broad policies")
    assert_set(broad.checkpoint, [16, 32, 64, 128, 256, 512], "broad checkpoints")
    expected_cells = broad.groupby(["case_id", "policy"]).size()
    if not (expected_cells == 6).all():
        raise AssertionError("every broad case/policy cell must contain six checkpoints")

    fig, ax = plt.subplots(figsize=(7.2, 3.55))
    styles = {"KNEE": (ORANGE, "s"), "S2_P50": (BLUE, "o")}
    for policy, (color, marker) in styles.items():
        group = broad[broad.policy == policy]
        for _, trajectory in group.groupby("case_id"):
            trajectory = trajectory.sort_values("checkpoint")
            ax.plot(trajectory.checkpoint, trajectory.cumulative_mean_delta_nll, color=color, alpha=0.16, lw=0.7)
        summary = group.groupby("checkpoint")["cumulative_mean_delta_nll"].agg(["mean", "min", "max"])
        ax.fill_between(summary.index, summary["min"], summary["max"], color=color, alpha=0.13, linewidth=0)
        ax.plot(summary.index, summary["mean"], color=color, marker=marker, ms=4, lw=1.5, label=f"{policy} mean")

    s2 = analysis["s2_direct_damage"]
    ax.axhline(0, color="#333333", lw=0.8)
    ax.set_xscale("log", base=2)
    ax.set_xticks([16, 32, 64, 128, 256, 512], ["16", "32", "64", "128", "256", "512"])
    ax.set_xlabel("decode horizon checkpoint")
    ax.set_ylabel("cumulative mean ΔNLL vs EXACT")
    ax.grid(color="#E5E5E5", lw=0.6)
    ax.legend(frameon=False, loc="upper left", ncol=2)
    ax.text(
        0.99,
        0.04,
        f"S2_P50 terminal prompt mean = {s2['mean']:.4f}\n95% prompt-bootstrap interval [{s2['interval_95'][0]:.4f}, {s2['interval_95'][1]:.4f}]\nmean with observed prompt min–max envelope; n = 16 prompts",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=7,
    )
    evidence_badge(ax, "DIRECT_FIXED_CONTEXT")
    save_figure(fig, "fig07-predictive-damage")


if __name__ == "__main__":
    main()

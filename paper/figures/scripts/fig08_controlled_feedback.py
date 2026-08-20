#!/usr/bin/env python3
"""Figure 8: controlled direct versus free-trajectory feedback on all bridge prompts."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue99 import ensure_issue99_inputs  # noqa: E402
from scripts.common import BLUE, ORANGE, assert_equal, assert_set, configure, panel_label, read_json, save_figure  # noqa: E402


PREREG = "results/2026-08-17/issue99/checkpoint-a/preregistration.json"
PREREG_SHA256 = "73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba"
ANALYSIS = "results/2026-08-20/issue99/analysis/analysis.json"
ANALYSIS_SHA256 = "fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c"


def main() -> None:
    configure()
    prereg = read_json(PREREG, PREREG_SHA256)
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    checkpoints = pd.read_parquet(ensure_issue99_inputs())
    bridge_cases = prereg["cohorts"]["bridge"]
    assert_equal(bridge_cases, ["issue102-sentinel", "04-factual-b4", "10-planning-b2"], "registered bridge prompts")
    bridge = checkpoints[
        (checkpoints.cache_regime == "high-cache") & checkpoints.case_id.isin(bridge_cases)
    ].copy()
    assert_equal(len(bridge), 84, "bridge checkpoint rows")
    assert_set(bridge.policy, ["KNEE", "S2_P50"], "bridge policies")
    assert_set(bridge.evidence_class, ["DIRECT_FIXED_CONTEXT", "FREE_TRAJECTORY"], "bridge evidence classes")
    assert_set(bridge.checkpoint, [16, 32, 64, 128, 256, 512, 1024], "bridge checkpoints")
    cells = bridge.groupby(["case_id", "policy", "evidence_class"]).size()
    if not (cells == 7).all() or len(cells) != 12:
        raise AssertionError("bridge must contain 3 prompts × 2 policies × 2 evidence classes × 7 checkpoints")

    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.85), sharey=True)
    policy_styles = {"KNEE": (ORANGE, "s"), "S2_P50": (BLUE, "o")}
    evidence_styles = {"DIRECT_FIXED_CONTEXT": "-", "FREE_TRAJECTORY": "--"}
    for index, (ax, case_id) in enumerate(zip(axes, bridge_cases)):
        case = bridge[bridge.case_id == case_id]
        for (policy, evidence), group in case.groupby(["policy", "evidence_class"]):
            group = group.sort_values("checkpoint")
            color, marker = policy_styles[policy]
            ax.plot(
                group.checkpoint,
                group.cumulative_mean_hidden_l2,
                color=color,
                marker=marker,
                ms=3,
                lw=1.2,
                ls=evidence_styles[evidence],
            )
        ax.set_xscale("log", base=2)
        ax.set_xticks([16, 64, 256, 1024], ["16", "64", "256", "1024"])
        ax.set_xlabel("decode checkpoint")
        ax.grid(color="#E5E5E5", lw=0.6)
        ax.text(0.04, 0.96, case_id, transform=ax.transAxes, ha="left", va="top", fontsize=7, fontweight="bold")
        panel_label(ax, chr(ord("A") + index))
    axes[0].set_ylabel("cumulative mean hidden relative L2 vs EXACT")
    handles = [
        Line2D([], [], color=BLUE, marker="o", label="S2_P50"),
        Line2D([], [], color=ORANGE, marker="s", label="KNEE"),
        Line2D([], [], color="#333333", ls="-", label="DIRECT_FIXED_CONTEXT"),
        Line2D([], [], color="#333333", ls="--", label="FREE_TRAJECTORY"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, bbox_to_anchor=(0.5, 1.03))
    materiality = analysis["feedback_and_capacity"]["classifications"]["materiality_effect"]
    fig.text(
        0.50,
        -0.01,
        f"Registered amplification of the measured controlled perturbation: {materiality['mean']:.4f}× "
        f"(95% prompt-bootstrap interval {materiality['interval_95'][0]:.4f}–{materiality['interval_95'][1]:.4f}); "
        "growth shape is heterogeneous.",
        ha="center",
        fontsize=7,
    )
    fig.subplots_adjust(wspace=0.16, top=0.83, bottom=0.22)
    save_figure(fig, "fig08-controlled-feedback")


if __name__ == "__main__":
    main()

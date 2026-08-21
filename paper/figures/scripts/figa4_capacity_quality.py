#!/usr/bin/env python3
"""Appendix Figure A4: capacity-conditioned perturbation and predictive damage."""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, save_figure


ANALYSIS = "results/2026-08-20/issue99/analysis/analysis.json"
ANALYSIS_SHA256 = "fdd0877cd5f25bd6858181eb2fede8291ab280f33616da7b0fd0d58717b3553c"


def main() -> None:
    configure()
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    outcomes = analysis["primary_outcomes"]
    assert_equal(outcomes["CAPACITY_CHANGES_PREDICTIVE_DAMAGE"], "yes", "capacity/damage classification")
    assert_equal(outcomes["CAPACITY_CHANGES_REALIZED_PERTURBATION"], "yes", "capacity/perturbation classification")
    rows = analysis["feedback_and_capacity"]["capacity_terminal"]
    assert_equal(len(rows), 6, "capacity bridge pairs")
    assert_set([row["case_id"] for row in rows], ["issue102-sentinel", "04-factual-b4", "10-planning-b2"], "capacity bridge prompts")
    assert_set([row["policy"] for row in rows], ["KNEE", "S2_P50"], "capacity bridge policies")
    assert_set([row["checkpoint"] for row in rows], [512], "common capacity horizon")
    assert_set([row["evidence_class_low"] for row in rows], ["CAPACITY_FIXED_CONTEXT"], "low-capacity evidence class")
    assert_set([row["evidence_class_high"] for row in rows], ["DIRECT_FIXED_CONTEXT"], "high-capacity evidence class")
    low_gib = rows[0]["capacity_bytes_low"] / 2**30
    high_gib = rows[0]["capacity_bytes_high"] / 2**30
    if not all(row["capacity_bytes_low"] == rows[0]["capacity_bytes_low"] and row["capacity_bytes_high"] == rows[0]["capacity_bytes_high"] for row in rows):
        raise AssertionError("capacity bridge must use one fixed pair of accepted regimes")

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.3))
    policy_color = {"KNEE": ORANGE, "S2_P50": BLUE}
    prompt_marker = {"issue102-sentinel": "o", "04-factual-b4": "s", "10-planning-b2": "D"}
    for row in rows:
        color = policy_color[row["policy"]]
        marker = prompt_marker[row["case_id"]]
        ax_a.plot([0, 1], [row["cumulative_intentional_swaps_low"], row["cumulative_intentional_swaps_high"]], color=color, lw=0.9, alpha=0.75)
        ax_a.scatter([0, 1], [row["cumulative_intentional_swaps_low"], row["cumulative_intentional_swaps_high"]], marker=marker, s=27, facecolors="white", edgecolors=color, lw=1.0)
        ax_b.plot([0, 1], [row["cumulative_mean_delta_nll_low"], row["cumulative_mean_delta_nll_high"]], color=color, lw=0.9, alpha=0.75)
        ax_b.scatter([0, 1], [row["cumulative_mean_delta_nll_low"], row["cumulative_mean_delta_nll_high"]], marker=marker, s=27, facecolors="white", edgecolors=color, lw=1.0)
    labels = [f"lower\n{low_gib:.1f} GiB", f"higher\n{high_gib:.1f} GiB"]
    for ax in (ax_a, ax_b):
        ax.set_xticks([0, 1], labels)
        ax.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_a.set_ylabel("cumulative intentional swaps at token 512")
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "CAPACITY_FIXED_CONTEXT ↔\nDIRECT_FIXED_CONTEXT", y=1.06)
    ax_b.axhline(0, color="#333333", lw=0.8)
    ax_b.set_ylabel("cumulative mean ΔNLL at token 512")
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "controlled model execution", y=1.06)
    handles = [
        Line2D([], [], color=BLUE, label="S2_P50"),
        Line2D([], [], color=ORANGE, label="KNEE"),
        Line2D([], [], marker="o", color="none", mec="#333333", label="sentinel"),
        Line2D([], [], marker="s", color="none", mec="#333333", label="factual"),
        Line2D([], [], marker="D", color="none", mec="#333333", label="planning"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False, bbox_to_anchor=(0.5, 0.01))
    fig.text(
        0.5,
        0.11,
        "Three frozen bridge prompts only; capacity changes realized substitutions and ΔNLL, but does not establish a universal capacity law.",
        ha="center",
        fontsize=6.7,
    )
    fig.subplots_adjust(wspace=0.34, bottom=0.25)
    save_figure(fig, "figa4-capacity-quality")


if __name__ == "__main__":
    main()

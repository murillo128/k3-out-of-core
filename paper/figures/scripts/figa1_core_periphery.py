#!/usr/bin/env python3
"""Appendix Figure A1: post-hoc core structure and all static-pinning outcomes."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from common import BLUE, GRAY, GREEN, LIGHT, ORANGE, RED, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, require_file, save_figure


CORE = "results/2026-08-17/issue105/analysis/core-periphery-analysis.json"
CORE_SHA256 = "0f21902f655f86e46f892f885d26c590f38be9bd8a4c2509d43dc945a6d2ad92"
CELLS = "results/2026-08-17/issue105/analysis/committee-counterfactual-cells.csv"
CELLS_SHA256 = "b9e70c327119c0924159614a689025be4f87a2dda7a9c473e385b02c73ac8f9d"


def main(stem: str = "figa1-core-periphery") -> None:
    configure()
    core = read_json(CORE, CORE_SHA256)
    cells = pd.read_csv(require_file(CELLS, CELLS_SHA256))
    decode = cells[cells.phase == "DECODE"].copy()
    assert_equal(len(decode), 1440, "DECODE committee cells")
    assert_set(decode.source_evidence_class, ["FIXED_ROUTE_COUNTERFACTUAL"], "committee evidence class")
    sensitivity = core["phases"]["DECODE"]["gamma_sensitivity"]
    assert_equal(len(sensitivity), 5, "gamma sensitivity points")
    summary = core["committee_pin_counterfactual"]["summary_by_gamma"]
    assert_equal(sum(item["regresses_count"] for item in summary), 308, "preserved regressing cells")
    assert_equal(sum(item["infeasible_count"] for item in summary), 196, "preserved infeasible cells")
    if any(item["cell_count"] != 288 for item in summary):
        raise AssertionError("each gamma must preserve all 288 committee cells")

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.15), gridspec_kw={"width_ratios": [0.9, 1.25]})
    gamma = np.array([item["gamma"] for item in sensitivity])
    keys = np.array([item["core_expert_key_count"] for item in sensitivity]) / 1000
    mass = np.array([item["selected_mass_fraction"] for item in sensitivity]) * 100
    line_keys = ax_a.plot(gamma, keys, color=BLUE, marker="o", lw=1.4, label="core keys (thousands)")
    ax_a.set_xlabel("family recurrence threshold γ")
    ax_a.set_ylabel("core expert keys (thousands)", color=BLUE)
    ax_a.tick_params(axis="y", colors=BLUE)
    twin = ax_a.twinx()
    line_mass = twin.plot(gamma, mass, color=ORANGE, marker="s", ls="--", lw=1.4, label="selected-mass fraction")
    twin.set_ylabel("selected-mass fraction (%)", color=ORANGE)
    twin.tick_params(axis="y", colors=ORANGE)
    ax_a.grid(color="#E5E5E5", lw=0.6)
    ax_a.legend(line_keys + line_mass, [line.get_label() for line in line_keys + line_mass], frameon=False, loc="lower left")
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "POST_HOC_EXPLORATORY", y=1.06)

    x = np.arange(len(summary))
    categories = [
        ("improves_count", "improve", GREEN, "//"),
        ("equal_count", "unchanged", LIGHT, ""),
        ("regresses_count", "regress", RED, "xx"),
        ("infeasible_count", "infeasible", GRAY, ".."),
    ]
    bottom = np.zeros(len(summary))
    for key, label, color, hatch in categories:
        values = np.array([item[key] for item in summary])
        ax_b.bar(x, values, bottom=bottom, color=color, edgecolor="#333333", lw=0.5, hatch=hatch, label=label)
        bottom += values
    assert_equal(bottom.tolist(), [288.0] * 5, "stacked outcome totals")
    ax_b.set_xticks(x, [str(item["gamma"]) for item in summary])
    ax_b.set_xlabel("family recurrence threshold γ")
    ax_b.set_ylabel("fixed-route cells (all outcomes retained)")
    ax_b.grid(axis="y", color="#E5E5E5", lw=0.6)
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "FIXED_ROUTE_COUNTERFACTUAL\nPOST_HOC_EXPLORATORY", y=1.06)
    fig.legend(
        handles=[Patch(facecolor=color, edgecolor="#333333", hatch=hatch, label=label) for _, label, color, hatch in categories],
        loc="lower center",
        ncol=4,
        frameon=False,
        bbox_to_anchor=(0.72, 0.02),
    )
    fig.text(0.72, 0.005, "1,440 cells total · 308 regress · 196 infeasible", ha="center", fontsize=7)
    fig.subplots_adjust(wspace=0.40, bottom=0.22)
    save_figure(fig, stem)


if __name__ == "__main__":
    main()

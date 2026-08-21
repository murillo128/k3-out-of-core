#!/usr/bin/env python3
"""Appendix Figure A3: non-monotonic 16–512-token systems/locality diagnostic."""

from __future__ import annotations

import matplotlib.pyplot as plt

from common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, save_figure


ANALYSIS = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/long-horizon-analysis.json"
ANALYSIS_SHA256 = "99cea4736cd230e8c2dd4df28d0e409cc04301837633edee374506a1b181fbb7"


def main() -> None:
    configure()
    analysis = read_json(ANALYSIS, ANALYSIS_SHA256)
    assert_equal(analysis["status"], "pass", "systems horizon diagnostic status")
    assert_equal(analysis["provenance"], "MEASURED_PHYSICAL_DIAGNOSTIC", "systems horizon evidence class")
    pairs = analysis["pairs"]
    assert_equal(len(pairs), 3, "frozen systems-horizon prompts")
    assert_set([row["workload"] for row in pairs], ["sentinel", "low_hit", "high_hit"], "systems-horizon workloads")
    if not all(row["curve_classification"]["classification"] == "NON_MONOTONIC" for row in pairs):
        raise AssertionError("all frozen horizon curves must retain NON_MONOTONIC classification")

    order = ["sentinel", "low_hit", "high_hit"]
    by_workload = {row["workload"]: row for row in pairs}
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.95), sharey=True)
    for index, (ax, workload) in enumerate(zip(axes, order)):
        row = by_workload[workload]
        points = row["cumulative"]
        horizons = [point["generated_tokens"] for point in points]
        assert_equal(horizons, [16, 32, 64, 128, 256, 512], f"{workload} horizons")
        ax.plot(horizons, [point["exact_hit_ratio"] for point in points], color=ORANGE, marker="s", ms=3, lw=1.2, label="EXACT")
        ax.plot(horizons, [point["s2_hit_ratio"] for point in points], color=BLUE, marker="o", ms=3, lw=1.2, label="S2_P50")
        ax.axvline(64, color="#555555", ls=":", lw=0.9)
        ax.set_xscale("log", base=2)
        ax.set_xticks([16, 64, 256, 512], ["16", "64", "256", "512"])
        ax.set_xlabel("generated-token horizon")
        ax.grid(color="#E5E5E5", lw=0.6)
        ax.text(0.04, 0.95, workload.replace("_", " "), transform=ax.transAxes, va="top", fontsize=7, fontweight="bold")
        ax.text(64, ax.get_ylim()[0], "  primary window", rotation=90, va="bottom", fontsize=5.8, color="#555555")
        panel_label(ax, chr(ord("A") + index))
        if index == 0:
            ax.set_ylabel("cumulative physical hit ratio")
        else:
            ax.tick_params(labelleft=False)
    axes[0].legend(frameon=False, loc="lower left")
    evidence_badge(axes[1], "MEASURED_PHYSICAL_DIAGNOSTIC", y=1.04)
    fig.text(
        0.5,
        0.005,
        "All three frozen curves are non-monotonic; the 64-token window is not evidence for a steady-state trajectory. Systems/locality only, not semantic quality.",
        ha="center",
        fontsize=6.7,
    )
    fig.subplots_adjust(wspace=0.14, bottom=0.20)
    save_figure(fig, "figa3-systems-horizon")


if __name__ == "__main__":
    main()

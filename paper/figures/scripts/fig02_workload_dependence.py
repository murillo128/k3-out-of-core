#!/usr/bin/env python3
"""Figure 2: workload-conditioned physical variation and sentinel drift reference."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, panel_label, require_file, save_figure, short_family


PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"


def main() -> None:
    configure()
    frame = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    assert_equal(len(frame), 184, "physical row count")
    assert_set(frame.source_evidence_class, ["MEASURED_PHYSICAL"], "physical evidence class")
    primary = frame[
        (frame.stage == "STAGE_A") & (frame.case_role == "primary") & (frame.policy == "S2_P50")
    ].copy()
    sentinel = frame[(frame.stage == "STAGE_A_SENTINEL") & (frame.case_role == "sentinel")].copy()
    assert_equal(len(primary), 128, "Stage-A primary rows")
    assert_equal(primary.case_id.nunique(), 128, "Stage-A primary prompts")
    assert_equal(len(sentinel), 8, "sentinel rows")
    assert_equal(primary.semantic_family.nunique(), 16, "semantic families")
    family_levels = primary.groupby("semantic_family").length_level.agg(lambda values: set(values.astype(int)))
    if not all(levels == set(range(1, 9)) for levels in family_levels):
        raise AssertionError("Stage A must contain every family × within-family level cell")

    family_order = list(dict.fromkeys(primary.sort_values("case_id").semantic_family))
    heat = (
        primary.assign(length_level=primary.length_level.astype(int))
        .pivot(index="semantic_family", columns="length_level", values="decode_tok_s")
        .loc[family_order, range(1, 9)]
    )
    prompt_spread = float(np.quantile(primary.decode_tok_s, 0.9) - np.quantile(primary.decode_tok_s, 0.1))
    sentinel_spread = float(np.quantile(sentinel.decode_tok_s, 0.9) - np.quantile(sentinel.decode_tok_s, 0.1))
    spread_ratio = prompt_spread / sentinel_spread
    if not np.isclose(prompt_spread, 0.03223516424368278) or not np.isclose(sentinel_spread, 0.0014276112655599582):
        raise AssertionError("physical prompt/sentinel spread changed from the frozen corpus")
    if not np.isclose(spread_ratio, 22.57979116677749):
        raise AssertionError("prompt/sentinel spread ratio changed")

    fig = plt.figure(figsize=(7.2, 6.15))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.25, 0.9], width_ratios=[1.7, 0.75], hspace=0.48, wspace=0.34)
    ax_a = fig.add_subplot(grid[0, :])
    image = ax_a.pcolormesh(
        np.arange(9) - 0.5,
        np.arange(17) - 0.5,
        heat.values,
        cmap="cividis",
        shading="flat",
        rasterized=False,
    )
    ax_a.set_xlim(-0.5, 7.5)
    ax_a.set_ylim(15.5, -0.5)
    ax_a.set_yticks(np.arange(16), [short_family(value) for value in heat.index])
    ax_a.set_xticks(np.arange(8), [str(value) for value in range(1, 9)])
    ax_a.set_xlabel("within-family length level (ordinal, not an absolute token band)")
    ax_a.set_ylabel("semantic family")
    colorbar = fig.colorbar(image, ax=ax_a, pad=0.015, fraction=0.032)
    colorbar.set_label("S2_P50 physical decode TPS")
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_PHYSICAL · 16 × 8", y=1.04)

    ax_b = fig.add_subplot(grid[1, 0])
    palette = plt.get_cmap("tab20")
    markers = ["o", "s", "^", "D"]
    for index, family in enumerate(family_order):
        group = primary[primary.semantic_family == family].sort_values("templated_prompt_tokens")
        ax_b.plot(
            group.templated_prompt_tokens,
            group.decode_tok_s,
            color=palette(index),
            marker=markers[index % len(markers)],
            ms=2.8,
            lw=0.75,
            alpha=0.78,
        )
    ax_b.set_xlabel("actual templated prompt tokens (quantitative)")
    ax_b.set_ylabel("S2_P50 physical decode TPS")
    ax_b.grid(color="#E5E5E5", lw=0.6)
    ax_b.text(
        0.02,
        0.03,
        "one connected series per family; all 128 prompts retained",
        transform=ax_b.transAxes,
        fontsize=6.7,
    )
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "MEASURED_PHYSICAL", y=1.04)

    ax_c = fig.add_subplot(grid[1, 1])
    ax_c.bar([0, 1], [prompt_spread, sentinel_spread], color=[BLUE, ORANGE], edgecolor="#333333", hatch=["//", ".."])
    ax_c.set_xticks([0, 1], ["128 primary\nprompts", "8 repeated\nsentinels"])
    ax_c.set_ylabel("TPS p90–p10 spread")
    ax_c.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_c.text(
        0.30,
        0.84,
        f"{spread_ratio:.2f}×",
        transform=ax_c.transAxes,
        ha="center",
        va="top",
        fontsize=9,
        fontweight="bold",
        color="#222222",
        bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
    )
    ax_c.text(
        0.36,
        0.62,
        "empirical drift/noise\nreference, not a CI",
        transform=ax_c.transAxes,
        ha="center",
        va="top",
        fontsize=6.5,
        color="#222222",
        bbox={"boxstyle": "round,pad=0.15", "facecolor": "white", "edgecolor": "none", "alpha": 0.88},
    )
    panel_label(ax_c, "C")
    evidence_badge(ax_c, "MEASURED_PHYSICAL", y=1.04)
    save_figure(fig, "fig02-workload-dependence")


if __name__ == "__main__":
    main()

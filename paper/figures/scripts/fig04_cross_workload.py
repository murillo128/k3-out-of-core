#!/usr/bin/env python3
"""Figure 4: physical cross-workload results with every frozen case visible."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from common import BLUE, ORANGE, PURPLE, assert_equal, assert_set, configure, evidence_badge, panel_label, require_file, save_figure


PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"


def short_family(name: str) -> str:
    replacements = {
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
    if name not in replacements:
        raise AssertionError(f"unmapped semantic family: {name}")
    return replacements[name]


def main() -> None:
    configure()
    frame = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    assert_equal(len(frame), 184, "physical row count")
    assert_set(frame["source_evidence_class"].unique(), ["MEASURED_PHYSICAL"], "physical evidence classes")

    stage_a = frame[(frame.stage == "STAGE_A") & (frame.case_role == "primary") & (frame.policy == "S2_P50")].copy()
    assert_equal(len(stage_a), 128, "Stage-A primary prompts")
    assert_equal(stage_a.case_id.nunique(), 128, "Stage-A unique prompts")
    family_counts = stage_a.groupby("semantic_family").length_level.nunique()
    assert_equal(len(family_counts), 16, "Stage-A family count")
    if not (family_counts == 8).all():
        raise AssertionError(f"Stage-A is not a complete 16 × 8 corpus: {family_counts.to_dict()}")
    assert_set(stage_a.length_level.astype(int), range(1, 9), "Stage-A length levels")

    stage_c = frame[(frame.stage == "STAGE_C") & (frame.case_role == "primary")].copy()
    assert_equal(len(stage_c), 48, "Stage-C physical rows")
    assert_equal(stage_c.case_id.nunique(), 24, "Stage-C unique prompts")
    counts = stage_c.groupby("case_id").policy.agg(lambda values: set(values))
    if not all(value == {"EXACT", "KNEE"} for value in counts):
        raise AssertionError("every Stage-C prompt must have EXACT and KNEE")
    s2_by_case = stage_a.set_index("case_id").decode_tok_s
    assert_set(stage_c.case_id, s2_by_case.index.intersection(stage_c.case_id), "Stage-C S2 availability")
    pivot_c = stage_c.pivot(index="case_id", columns="policy", values="decode_tok_s")
    pivot_c["S2_P50"] = s2_by_case.loc[pivot_c.index]
    pivot_c["ratio_exact"] = pivot_c.S2_P50 / pivot_c.EXACT
    pivot_c["ratio_knee"] = pivot_c.S2_P50 / pivot_c.KNEE
    assert_equal(int((pivot_c.ratio_exact > 1).sum()), 24, "S2/EXACT wins")
    assert_equal(int((pivot_c.ratio_knee > 1).sum()), 24, "S2/KNEE wins")

    sentinels = frame[(frame.stage == "STAGE_A_SENTINEL") & (frame.case_role == "sentinel")]
    assert_equal(len(sentinels), 8, "sentinel timing rows")
    sentinel_spread = 100 * (
        np.quantile(sentinels.decode_tok_s, 0.9) - np.quantile(sentinels.decode_tok_s, 0.1)
    ) / np.median(sentinels.decode_tok_s)

    family_order = list(dict.fromkeys(stage_a.sort_values("case_id").semantic_family))
    heat = (
        stage_a.assign(length_level=stage_a.length_level.astype(int))
        .pivot(index="semantic_family", columns="length_level", values="decode_tok_s")
        .loc[family_order, range(1, 9)]
    )

    fig = plt.figure(figsize=(7.2, 6.0))
    grid = fig.add_gridspec(2, 1, height_ratios=[1.15, 1.0], hspace=0.46)
    ax_a = fig.add_subplot(grid[0])
    # pcolormesh keeps the 16 × 8 heatmap as vector cells in SVG/PDF.
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
    ax_a.set_xlabel("within-family length level (ordinal)")
    ax_a.set_ylabel("semantic family")
    colorbar = fig.colorbar(image, ax=ax_a, pad=0.015, fraction=0.035)
    colorbar.set_label("S2_P50 decode TPS")
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_PHYSICAL · n = 128")

    ax_b = fig.add_subplot(grid[1])
    ordered = pivot_c.sort_index()
    x = np.arange(len(ordered))
    ax_b.axhline(1.0, color="#333333", lw=1, ls="--", zorder=1)
    for i, (_, row) in enumerate(ordered.iterrows()):
        ax_b.plot([i, i], [row.ratio_exact, row.ratio_knee], color="#BBBBBB", lw=0.8, zorder=1)
    ax_b.scatter(x, ordered.ratio_exact, s=22, marker="o", facecolors="white", edgecolors=BLUE, lw=1.1, zorder=3)
    ax_b.scatter(x, ordered.ratio_knee, s=25, marker="x", color=ORANGE, lw=1.3, zorder=3)
    ax_b.set_xticks(x, ordered.index, rotation=63, ha="right")
    ax_b.set_ylabel("physical decode-TPS ratio")
    ax_b.set_xlabel("frozen Stage-C prompt (one physical observation per policy/cell)")
    lower = min(0.995, float(ordered[["ratio_exact", "ratio_knee"]].min().min()) - 0.005)
    upper = float(ordered[["ratio_exact", "ratio_knee"]].max().max()) + 0.012
    ax_b.set_ylim(lower, upper)
    ax_b.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_b.legend(
        handles=[
            Line2D([], [], marker="o", mfc="white", mec=BLUE, color="none", label="S2_P50 / EXACT"),
            Line2D([], [], marker="x", color=ORANGE, lw=0, label="S2_P50 / KNEE"),
            Line2D([], [], color="#333333", ls="--", label="reference = 1.0"),
        ],
        ncol=3,
        loc="upper left",
        frameon=False,
    )
    ax_b.text(
        0.99,
        0.04,
        f"24/24 above 1.0 for both contrasts\nsentinel p90–p10 timing spread = {sentinel_spread:.2f}% (reference, not CI)",
        transform=ax_b.transAxes,
        ha="right",
        va="bottom",
        fontsize=7,
    )
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "MEASURED_PHYSICAL · 24 prompts × 3 policies")
    save_figure(fig, "fig04-cross-workload")


if __name__ == "__main__":
    main()

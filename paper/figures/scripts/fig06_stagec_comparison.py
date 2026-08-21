#!/usr/bin/env python3
"""Figure 6: all frozen Stage-C physical comparisons at fixed cache capacity."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from common import BLUE, ORANGE, PURPLE, assert_equal, assert_set, configure, evidence_badge, panel_label, require_file, save_figure


PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"


def main() -> None:
    configure()
    frame = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    assert_equal(len(frame), 184, "physical row count")
    assert_set(frame.source_evidence_class, ["MEASURED_PHYSICAL"], "physical evidence class")
    stage_a = frame[
        (frame.stage == "STAGE_A") & (frame.case_role == "primary") & (frame.policy == "S2_P50")
    ].copy()
    stage_c = frame[(frame.stage == "STAGE_C") & (frame.case_role == "primary")].copy()
    sentinel = frame[(frame.stage == "STAGE_A_SENTINEL") & (frame.case_role == "sentinel")].copy()
    assert_equal(len(stage_a), 128, "Stage-A S2 rows")
    assert_equal(len(stage_c), 48, "Stage-C fresh rows")
    assert_equal(stage_c.case_id.nunique(), 24, "Stage-C prompts")
    policies = stage_c.groupby("case_id").policy.agg(lambda values: set(values))
    if not all(value == {"EXACT", "KNEE"} for value in policies):
        raise AssertionError("each Stage-C prompt must provide fresh EXACT and KNEE rows")
    assert_equal(len(sentinel), 8, "sentinel rows")

    s2 = stage_a.set_index("case_id")[["decode_tok_s", "loads_per_token", "capacity_slots"]]
    assert_set(stage_c.case_id, s2.index.intersection(stage_c.case_id), "frozen Stage-A S2 availability")
    tps = stage_c.pivot(index="case_id", columns="policy", values="decode_tok_s")
    loads = stage_c.pivot(index="case_id", columns="policy", values="loads_per_token")
    tps["S2_P50"] = s2.loc[tps.index, "decode_tok_s"]
    loads["S2_P50"] = s2.loc[loads.index, "loads_per_token"]
    tps["ratio_exact"] = tps.S2_P50 / tps.EXACT
    tps["ratio_knee"] = tps.S2_P50 / tps.KNEE
    loads["delta_exact"] = loads.S2_P50 - loads.EXACT
    loads["delta_knee"] = loads.S2_P50 - loads.KNEE
    assert_equal(int((tps.ratio_exact > 1).sum()), 24, "S2/EXACT observed wins")
    assert_equal(int((tps.ratio_knee > 1).sum()), 24, "S2/KNEE observed wins")
    if not ((loads.delta_exact < 0).all() and (loads.delta_knee < 0).all()):
        raise AssertionError("every Stage-C S2 comparison must reduce backing loads/token")
    capacities = set(stage_c.capacity_slots.astype(int)) | set(s2.loc[tps.index, "capacity_slots"].astype(int))
    assert_equal(capacities, {7849}, "fixed Stage-C capacity")

    order = tps.sort_values("ratio_knee").index
    ordered_tps = tps.loc[order]
    ordered_loads = loads.loc[order]
    x = np.arange(len(order))
    sentinel_spread = float(np.quantile(sentinel.decode_tok_s, 0.9) - np.quantile(sentinel.decode_tok_s, 0.1))

    fig, (ax_a, ax_b) = plt.subplots(2, 1, figsize=(7.2, 5.35), sharex=True, gridspec_kw={"height_ratios": [1.1, 0.9]})
    ax_a.axhline(1.0, color="#333333", lw=1, ls="--")
    for index, row in enumerate(ordered_tps.itertuples()):
        ax_a.plot([index, index], [row.ratio_exact, row.ratio_knee], color="#BBBBBB", lw=0.8)
    ax_a.scatter(x, ordered_tps.ratio_exact, s=23, marker="o", facecolors="white", edgecolors=BLUE, lw=1.1, zorder=3)
    ax_a.scatter(x, ordered_tps.ratio_knee, s=25, marker="x", color=ORANGE, lw=1.3, zorder=3)
    ax_a.set_ylabel("S2_P50 / baseline physical TPS")
    ax_a.set_ylim(min(0.997, float(ordered_tps[["ratio_exact", "ratio_knee"]].min().min()) - 0.004), float(ordered_tps[["ratio_exact", "ratio_knee"]].max().max()) + 0.012)
    ax_a.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_a.legend(
        handles=[
            Line2D([], [], marker="o", mfc="white", mec=BLUE, color="none", label="S2_P50 / EXACT"),
            Line2D([], [], marker="x", color=ORANGE, lw=0, label="S2_P50 / KNEE"),
            Line2D([], [], color="#333333", ls="--", label="reference = 1.0"),
        ],
        ncol=3,
        frameon=False,
        loc="upper left",
    )
    ax_a.text(
        0.99,
        0.04,
        f"24/24 observed above 1.0 for both contrasts\nsentinel TPS p90–p10 = {sentinel_spread:.6f} (drift reference, not CI)",
        transform=ax_a.transAxes,
        ha="right",
        fontsize=6.8,
        color=PURPLE,
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_PHYSICAL", y=1.04)

    ax_b.axhline(0, color="#333333", lw=0.9)
    for index, row in enumerate(ordered_loads.itertuples()):
        ax_b.plot([index, index], [row.delta_exact, row.delta_knee], color="#BBBBBB", lw=0.8)
    ax_b.scatter(x, ordered_loads.delta_exact, s=22, marker="o", facecolors="white", edgecolors=BLUE, lw=1.0)
    ax_b.scatter(x, ordered_loads.delta_knee, s=24, marker="x", color=ORANGE, lw=1.2)
    ax_b.set_ylabel("Δ physical backing loads/token\n(S2_P50 − baseline)")
    ax_b.set_xticks(x, order, rotation=63, ha="right")
    ax_b.set_xlabel("frozen non-random Stage-C prompt (ordered by S2_P50/KNEE TPS ratio)")
    ax_b.grid(axis="y", color="#E5E5E5", lw=0.6)
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "MEASURED_PHYSICAL · fixed 7,849-slot cache", y=1.04)
    fig.text(
        0.99,
        0.005,
        "One physical observation per policy/prompt; frozen Stage-A S2_P50 reused, fresh Stage-C EXACT/KNEE. The selected subset is not IID.",
        ha="right",
        fontsize=6.6,
    )
    fig.subplots_adjust(hspace=0.13, bottom=0.30)
    save_figure(fig, "fig06-stagec-comparison")


if __name__ == "__main__":
    main()

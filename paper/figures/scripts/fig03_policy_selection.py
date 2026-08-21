#!/usr/bin/env python3
"""Figure 3: frozen seven-profile policy-selection surface and confirmations."""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue98 import ensure_issue98_inputs  # noqa: E402
from scripts.common import BLUE, GRAY, ORANGE, PURPLE, RED, assert_equal, assert_set, configure, evidence_badge, panel_label, save_figure  # noqa: E402


PROFILE_ORDER = ["S1_P10", "KNEE", "S1_P50", "S2_P25", "S2_P50", "S4_P25", "S8_P25"]
PROFILE_PARAMETERS = {
    "S1_P10": (1, "p10"),
    "KNEE": (1, "p25"),
    "S1_P50": (1, "p50"),
    "S2_P25": (2, "p25"),
    "S2_P50": (2, "p50"),
    "S4_P25": (4, "p25"),
    "S8_P25": (8, "p25"),
}


def load_rows(root: Path, section: str, expected: int) -> list[dict]:
    paths = sorted((root / section).glob("run-*/validated-summary.json"))
    assert_equal(len(paths), expected, f"{section} release cells")
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert_set([row["status"] for row in rows], ["pass"], f"{section} statuses")
    return rows


def main() -> None:
    configure()
    root = ensure_issue98_inputs()
    final = json.loads((root / "final" / "final-synthesis.json").read_text(encoding="utf-8"))
    screening = load_rows(root, "screening", 21)
    confirmation = load_rows(root, "confirmation", 6)
    assert_set([row["point"] for row in screening], PROFILE_ORDER, "screening profiles")
    counts = {profile: sum(row["point"] == profile for row in screening) for profile in PROFILE_ORDER}
    assert_equal(counts, {profile: 3 for profile in PROFILE_ORDER}, "three screening observations per profile")
    assert_equal(final["screening"]["selection"]["accepted_screening_cells"], 21, "accepted screening cells")
    assert_equal(final["screening"]["selection"]["screen_best_new"]["name"], "S2_P50", "frozen winner")
    assert_equal(len(final["confirmation"]["pairs"]), 3, "paired confirmations")

    final_profiles = {row["name"]: row for row in final["screening"]["profiles"]}
    for profile in PROFILE_ORDER:
        raw = sorted(row["measured"]["decode_tok_s"] for row in screening if row["point"] == profile)
        summary = final_profiles[profile]["measured"]["decode_tok_s"]
        expected = sorted([summary["min"], summary["median"], summary["max"]])
        if not np.allclose(raw, expected, rtol=0, atol=1e-15):
            raise AssertionError(f"{profile} raw screening rows disagree with final synthesis")
        swaps, _ = PROFILE_PARAMETERS[profile]
        assert_equal(final_profiles[profile]["routing"]["max_swaps"], swaps, f"{profile} max_swaps")

    pair_rows = []
    for pair in sorted({int(row["block"]) for row in confirmation}):
        group = [row for row in confirmation if int(row["block"]) == pair]
        assert_equal(len(group), 2, f"confirmation pair {pair} cells")
        assert_set([row["point"] for row in group], ["KNEE", "S2_P50"], f"confirmation pair {pair} policies")
        values = {row["point"]: float(row["measured"]["decode_tok_s"]) for row in group}
        pair_rows.append((pair, values["S2_P50"] / values["KNEE"]))
    final_ratios = [float(row["tps_ratio"]) for row in final["confirmation"]["pairs"]]
    if not np.allclose([value for _, value in pair_rows], final_ratios, rtol=0, atol=1e-15):
        raise AssertionError("raw confirmation ratios disagree with final synthesis")

    labels = [f"{name}  (s={PROFILE_PARAMETERS[name][0]}, {PROFILE_PARAMETERS[name][1]})" for name in PROFILE_ORDER]
    y = np.arange(len(PROFILE_ORDER))
    fig, axes = plt.subplots(1, 3, figsize=(7.2, 3.65), gridspec_kw={"width_ratios": [1.45, 1.25, 0.82]})
    ax_a, ax_b, ax_c = axes
    for index, profile in enumerate(PROFILE_ORDER):
        rows = sorted((row for row in screening if row["point"] == profile), key=lambda row: row["run_ordinal"])
        values = [row["measured"]["decode_tok_s"] for row in rows]
        color = BLUE if profile == "S2_P50" else GRAY
        ax_a.scatter(values, index + np.array([-0.08, 0, 0.08]), s=23, facecolors="white", edgecolors=color, lw=1.0, zorder=3)
        ax_a.scatter(statistics.median(values), index, s=32, marker="D", color=color, edgecolors="white", lw=0.4, zorder=4)
    ax_a.set_yticks(y, labels)
    ax_a.invert_yaxis()
    ax_a.set_xlabel("measured decode TPS\n(all three screening observations shown)")
    ax_a.grid(axis="x", color="#E5E5E5", lw=0.6)
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_PHYSICAL · 21/21 cells", y=1.04)

    for index, profile in enumerate(PROFILE_ORDER):
        rows = [row for row in screening if row["point"] == profile]
        values = [row["measured"]["loads_per_token"] for row in rows]
        color = BLUE if profile == "S2_P50" else ORANGE
        ax_b.scatter(values, index + np.array([-0.08, 0, 0.08]), marker="s", s=20, facecolors="white", edgecolors=color, lw=1.0)
        ax_b.scatter(statistics.median(values), index, marker="D", s=31, color=color, edgecolors="white", lw=0.4)
    ax_b.set_yticks(y, [])
    ax_b.invert_yaxis()
    ax_b.set_xlabel("physical backing loads/token")
    ax_b.grid(axis="x", color="#E5E5E5", lw=0.6)
    ax_b.text(
        0.03,
        0.02,
        "frozen rule: highest median TPS;\nties → lower regret, swaps, stable order",
        transform=ax_b.transAxes,
        fontsize=6.6,
        va="bottom",
    )
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "MEASURED_PHYSICAL", y=1.04)

    pair_x = np.arange(1, 4)
    ratios = [value for _, value in pair_rows]
    ax_c.axhline(1.0, color="#333333", ls="--", lw=0.9)
    ax_c.scatter(pair_x, ratios, marker="o", s=34, facecolors="white", edgecolors=PURPLE, lw=1.2)
    ax_c.plot(pair_x, ratios, color=PURPLE, lw=0.8)
    ax_c.set_xticks(pair_x, ["pair 1", "pair 2", "pair 3"], rotation=35, ha="right")
    ax_c.set_ylabel("S2_P50 / KNEE TPS")
    ax_c.set_ylim(0.995, max(ratios) + 0.012)
    ax_c.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_c.text(
        0.5,
        0.05,
        "three alternated\npaired confirmations\n6/6 cells",
        transform=ax_c.transAxes,
        ha="center",
        fontsize=6.6,
        color=RED,
    )
    panel_label(ax_c, "C")
    evidence_badge(ax_c, "MEASURED_PHYSICAL", y=1.04)
    fig.subplots_adjust(wspace=0.30, bottom=0.18)
    save_figure(fig, "fig03-policy-selection")


if __name__ == "__main__":
    main()

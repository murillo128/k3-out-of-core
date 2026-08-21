#!/usr/bin/env python3
"""Policy evolution: #77 conservative KNEE to #98 physical S2_P50."""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue77 import ensure_issue77_inputs  # noqa: E402
from fetch_issue98 import ensure_issue98_inputs  # noqa: E402
from scripts.common import BLUE, GRAY, GREEN, ORANGE, PURPLE, RED, assert_equal, assert_set, configure, evidence_badge, panel_label, save_figure  # noqa: E402


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

ISSUE77_ORDER = ["Conservative", "Knee", "Aggressive", "Stress"]


def markdown_row(text: str, label: str) -> list[str]:
    prefix = f"| {label} |"
    matches = [line for line in text.splitlines() if line.startswith(prefix)]
    assert_equal(len(matches), 1, f"#77 release row {label}")
    return [cell.strip() for cell in matches[0].strip().strip("|").split("|")]


def parse_percent(value: str) -> float:
    match = re.fullmatch(r"([0-9.]+)%", value)
    if match is None:
        raise AssertionError(f"expected percentage, got {value!r}")
    return float(match.group(1))


def load_rows(root: Path, section: str, expected: int) -> list[dict]:
    paths = sorted((root / section).glob("run-*/validated-summary.json"))
    assert_equal(len(paths), expected, f"{section} release cells")
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    assert_set([row["status"] for row in rows], ["pass"], f"{section} statuses")
    return rows


def main() -> None:
    configure()
    issue77_text = ensure_issue77_inputs().read_text(encoding="utf-8")
    capacity_rows = {name: markdown_row(issue77_text, name) for name in ISSUE77_ORDER}
    loads77 = [parse_percent(capacity_rows[name][3]) for name in ISSUE77_ORDER]
    assert_equal(loads77, [7.954, 11.323, 22.216, 22.899], "#77 96-GiB load-reduction frontier")
    quality = markdown_row(issue77_text, "Mean KL / JS")
    kl77, js77 = [], []
    for cell in quality[1:]:
        kl, js = (float(item.strip()) for item in cell.split("/"))
        kl77.append(kl)
        js77.append(js)
    if not np.allclose(kl77, [0.000507, 0.000783, 0.000860, 0.001641], rtol=0, atol=1e-12):
        raise AssertionError("#77 KL frontier changed")
    if not np.allclose(js77, [0.000125, 0.000192, 0.000213, 0.000407], rtol=0, atol=1e-12):
        raise AssertionError("#77 JS frontier changed")
    assert_equal(markdown_row(issue77_text, "Top-1 agreement")[1:], ["23/24"] * 4, "#77 top-1 agreement")
    assert_equal(
        [markdown_row(issue77_text, label)[2] for label in ["Conservative p10 / max1", "Knee p25 / max1", "Aggressive p50 / max4", "Stress p50 / max16"]],
        ["1", "1", "4", "7"],
        "#77 maximum realized swaps",
    )

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
    fig, axes = plt.subplots(2, 2, figsize=(7.2, 5.8))
    ax_a, ax_b, ax_c, ax_d = axes.flat

    colors77 = [GRAY, BLUE, ORANGE, RED]
    markers77 = ["o", "D", "s", "X"]
    kl_milli = np.array(kl77) * 1000
    ax_a.plot(loads77, kl_milli, color=GRAY, lw=0.9, zorder=1)
    annotations = {
        "Conservative": ("p10/max1", (4, -10), "left"),
        "Knee": ("KNEE · initial recommendation", (5, 5), "left"),
        "Aggressive": ("p50/max4", (-5, -12), "right"),
        "Stress": ("p50/max16 · 7 swaps", (-5, -15), "right"),
    }
    for name, load, kl, color, marker in zip(ISSUE77_ORDER, loads77, kl_milli, colors77, markers77):
        ax_a.scatter(load, kl, s=42, marker=marker, facecolors="white", edgecolors=color, lw=1.2, zorder=3)
        label, offset, align = annotations[name]
        ax_a.annotate(label, (load, kl), xytext=offset, textcoords="offset points", fontsize=6.0, color=color, ha=align)
    ax_a.set_xlabel("#77 free-route replay\nload reduction at 96 GiB (%)")
    ax_a.set_ylabel("teacher-forced mean KL (×10⁻³)")
    ax_a.set_xlim(5.8, 24.4)
    ax_a.set_ylim(0.38, 1.82)
    ax_a.grid(color="#E5E5E5", lw=0.6)
    ax_a.text(
        0.03,
        0.96,
        f"max4→max16: +{loads77[-1] - loads77[-2]:.3f} pp locality; KL {kl77[-1] / kl77[-2]:.2f}×\ntop-1 = 23/24 at every point (not quality neutrality)",
        transform=ax_a.transAxes,
        fontsize=6.0,
        va="top",
    )
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "#77 REPLAY + short-horizon predictive", y=1.11)

    for index, profile in enumerate(PROFILE_ORDER):
        rows = sorted((row for row in screening if row["point"] == profile), key=lambda row: row["run_ordinal"])
        values = [row["measured"]["decode_tok_s"] for row in rows]
        color = BLUE if profile == "S2_P50" else GRAY
        ax_b.scatter(values, index + np.array([-0.08, 0, 0.08]), s=23, facecolors="white", edgecolors=color, lw=1.0, zorder=3)
        ax_b.scatter(statistics.median(values), index, s=32, marker="D", color=color, edgecolors="white", lw=0.4, zorder=4)
    ax_b.set_yticks(y, labels)
    ax_b.invert_yaxis()
    ax_b.set_xlabel("#98 measured decode TPS\n(all three screen observations)")
    ax_b.grid(axis="x", color="#E5E5E5", lw=0.6)
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "#98 MEASURED_PHYSICAL · 21/21 cells", y=1.11)

    for index, profile in enumerate(PROFILE_ORDER):
        rows = [row for row in screening if row["point"] == profile]
        values = [row["measured"]["loads_per_token"] for row in rows]
        color = BLUE if profile == "S2_P50" else ORANGE
        ax_c.scatter(values, index + np.array([-0.08, 0, 0.08]), marker="s", s=20, facecolors="white", edgecolors=color, lw=1.0)
        ax_c.scatter(statistics.median(values), index, marker="D", s=31, color=color, edgecolors="white", lw=0.4)
    ax_c.set_yticks(y, labels)
    ax_c.invert_yaxis()
    ax_c.set_xlabel("#98 physical\nbacking loads/token")
    ax_c.grid(axis="x", color="#E5E5E5", lw=0.6)
    ax_c.text(
        0.03,
        0.02,
        "frozen rule: highest median TPS;\nties → lower regret, swaps, stable order",
        transform=ax_c.transAxes,
        fontsize=5.9,
        va="bottom",
    )
    panel_label(ax_c, "C")
    evidence_badge(ax_c, "#98 MEASURED_PHYSICAL", y=1.08)

    pair_x = np.arange(1, 4)
    ratios = [value for _, value in pair_rows]
    ax_d.axhline(1.0, color="#333333", ls="--", lw=0.9)
    ax_d.scatter(pair_x, ratios, marker="o", s=34, facecolors="white", edgecolors=PURPLE, lw=1.2)
    ax_d.plot(pair_x, ratios, color=PURPLE, lw=0.8)
    ax_d.set_xticks(pair_x, ["pair 1", "pair 2", "pair 3"], rotation=35, ha="right")
    ax_d.set_ylabel("S2_P50 / KNEE TPS")
    ax_d.set_ylim(0.995, max(ratios) + 0.012)
    ax_d.grid(axis="y", color="#E5E5E5", lw=0.6)
    ax_d.text(
        0.5,
        0.05,
        "three alternated\npairs · 6/6 cells",
        transform=ax_d.transAxes,
        ha="center",
        fontsize=5.9,
        color=RED,
    )
    panel_label(ax_d, "D")
    evidence_badge(ax_d, "#98 MEASURED_PHYSICAL", y=1.08)
    fig.text(0.28, 0.985, "#77 conservative frontier → KNEE", ha="center", va="top", fontsize=7.4, color=PURPLE, fontweight="bold")
    fig.text(0.74, 0.985, "new #98 physical question → S2_P50 selected", ha="center", va="top", fontsize=7.4, color=GREEN, fontweight="bold")
    fig.subplots_adjust(wspace=0.55, hspace=0.56, bottom=0.11, top=0.90)
    save_figure(fig, "fig03-policy-selection")


if __name__ == "__main__":
    main()

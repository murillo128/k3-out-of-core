#!/usr/bin/env python3
"""Figure 3: render bounded cache-aware selection without implying semantic equivalence."""

from __future__ import annotations

import matplotlib.pyplot as plt

from common import BLUE, GRAY, GREEN, LIGHT, ORANGE, RED, arrow, assert_equal, configure, draw_box, read_json, save_figure


PREREG = "results/2026-08-17/issue99/checkpoint-a/preregistration.json"
PREREG_SHA256 = "73917df7f533a7a15f8b2de708c03b937613f9a13ded0d1a2c33a7aad19afdba"


def main(stem: str = "fig03-bounded-routing") -> None:
    configure()
    prereg = read_json(PREREG, PREREG_SHA256)
    s2 = prereg["policies"]["S2_P50"]
    assert_equal(s2["candidate_count"], 32, "S2_P50 candidate_count")
    assert_equal(s2["max_swaps"], 2, "S2_P50 max_swaps")
    regret = float(s2["max_score_regret"])

    fig, ax = plt.subplots(figsize=(7.2, 3.55))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Inputs and ranking.
    draw_box(ax, (0.02, 0.68), 0.14, 0.17, "ordinary K3\nprobabilities  pᵢ", edgecolor=BLUE, facecolor="#DDEBF7")
    draw_box(ax, (0.205, 0.68), 0.15, 0.17, "selection score\npᵢ + correction", edgecolor=BLUE, facecolor="#DDEBF7")
    draw_box(ax, (0.40, 0.71), 0.13, 0.11, "exact top-16\nA₀", edgecolor=BLUE, facecolor="white")
    draw_box(ax, (0.40, 0.53), 0.13, 0.11, "candidate top-32\nC", edgecolor=BLUE, facecolor="white")
    arrow(ax, (0.16, 0.765), (0.205, 0.765), color=BLUE)
    arrow(ax, (0.355, 0.765), (0.40, 0.765), color=BLUE)
    arrow(ax, (0.355, 0.735), (0.40, 0.585), color=BLUE)

    # Service-aware gate, with the current state as a separate input.
    draw_box(ax, (0.20, 0.38), 0.16, 0.13, "current residency /\nservice tier", edgecolor=GREEN, facecolor="#DDF3EA")
    draw_box(
        ax,
        (0.59, 0.54),
        0.20,
        0.31,
        "deterministic swap gate\n\n1. candidate is in C\n2. service tier improves\n3. per-swap score regret\n   ≤ hard bound\n4. stable tie breaks",
        edgecolor=ORANGE,
        facecolor="#FFF0CC",
        fontsize=7.4,
    )
    arrow(ax, (0.53, 0.765), (0.59, 0.765))
    arrow(ax, (0.53, 0.585), (0.59, 0.63))
    arrow(ax, (0.36, 0.445), (0.59, 0.60), color=GREEN, linestyle="--")
    ax.text(0.45, 0.51, "read-only state", color=GREEN, fontsize=6.8, ha="center")

    draw_box(
        ax,
        (0.83, 0.62),
        0.15,
        0.17,
        "final membership A*\nexactly 16 experts\nmax_swaps = 2",
        edgecolor=ORANGE,
        facecolor="#FFF0CC",
        fontsize=7.8,
    )
    arrow(ax, (0.79, 0.695), (0.83, 0.705), color=ORANGE)

    draw_box(
        ax,
        (0.56, 0.18),
        0.31,
        0.16,
        "execution weights\nW_K3(p, A*) using original K3 semantics",
        edgecolor=BLUE,
        facecolor="#DDEBF7",
        fontsize=8.1,
    )
    arrow(ax, (0.905, 0.62), (0.78, 0.34), color=ORANGE)

    ax.text(
        0.02,
        0.20,
        f"S2_P50 registered hard per-swap bound: {regret:.7f}",
        fontsize=7.5,
        color=RED,
        fontweight="bold",
    )
    ax.text(
        0.02,
        0.08,
        "The bound limits selection-score regret; it does not guarantee semantic equivalence or quality neutrality.",
        fontsize=7.5,
        color=RED,
        bbox={"boxstyle": "round,pad=0.25", "facecolor": LIGHT, "edgecolor": GRAY, "lw": 0.7},
    )
    save_figure(fig, stem)


if __name__ == "__main__":
    main()

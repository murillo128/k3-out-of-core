#!/usr/bin/env python3
"""Research-decision map: question, evidence, decision, and next question."""

from __future__ import annotations

import matplotlib.pyplot as plt

from common import BLUE, GRAY, GREEN, INK, LIGHT, ORANGE, PURPLE, RED, arrow, configure, draw_box, save_figure


NODES = [
    ("#58 / PR #59", "cc9f4b8e05352efa447fd0d1267a7e4eed3db68d", "MEASURED_PHYSICAL"),
    ("#69/#73 / PR #70/#74", "8d1c09ab… / 817c2468…", "MEASURED + PROFILE"),
    ("#89 / PR #91", "f2f47cb6ce096d53bd1b56f8085345409b531824", "MEASURED_PHYSICAL"),
    ("#77 / PR #82", "9d0433896032055d9e114b61686717ec172e0329", "REPLAY + PREDICTIVE"),
    ("#98 v3", "485819939e9d074f99a646443a2bbab8f1466eb8", "MEASURED_PHYSICAL"),
    ("#102 / PR #103", "0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf", "MEASURED_PHYSICAL"),
    ("#105 / PR #106", "6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468", "POST_HOC + COUNTERFACTUAL"),
    ("#99 / PR #107", "eeaab5fa3f62047e8617ab3ed408ccbddbb56872", "DIRECT + FREE QUALITY"),
]


def main() -> None:
    configure()
    if len(NODES) != 8 or len({node[0] for node in NODES}) != 8:
        raise AssertionError("research journey must retain exactly eight distinct main decisions")
    if any(len(target) < 12 or not evidence for _, target, evidence in NODES):
        raise AssertionError("every journey node must retain target and evidence-class authority")

    fig, ax = plt.subplots(figsize=(7.2, 4.25))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    main_boxes = [
        (0.025, 0.66, "Q  Physical service?\nO  Exposed NVMe wait\nD  Optimize exact path\n#58\nMEASURED_PHYSICAL", BLUE),
        (0.272, 0.66, "Q  Cheaper exact misses?\nO  Feed/overlap gains;\n    service cost remains\nD  Keep service fixes\n#69/#73 · PROFILE", BLUE),
        (0.519, 0.66, "Q  Just add residency?\nO  Reclaim/refault cliff\nD  Safety-derived AUTO cap\n#89\nMEASURED_PHYSICAL", ORANGE),
        (0.766, 0.66, "Q  Can demand move?\nO  Bounded near-tie frontier\nD  Conservative KNEE\n#77\nREPLAY + PREDICTIVE", PURPLE),
        (0.025, 0.29, "Q  Which bounded shape?\nO  High-cache physical screen\nD  S2_P50 replaces KNEE\n#98\nMEASURED_PHYSICAL", GREEN),
        (0.272, 0.29, "Q  Does it generalize?\nO  Full-prompt 16 × 8 grid\nD  Workload-conditioned claim\n#102\nMEASURED_PHYSICAL", GREEN),
        (0.519, 0.29, "Q  Simpler locality rule?\nO  Feature limited; pinning fails\nD  Keep dynamic residency\n#105\nPOST_HOC + COUNTERFACTUAL", RED),
        (0.766, 0.29, "Q  Predictive cost?\nO  ΔNLL + token feedback\nD  No simple risk controller\n#99\nDIRECT + FREE QUALITY", RED),
    ]
    width, height = 0.209, 0.205
    for x, y, label, color in main_boxes:
        draw_box(ax, (x, y), width, height, label, facecolor="white", edgecolor=color, fontsize=5.15, linewidth=1.15)

    for left, right in zip(main_boxes[:3], main_boxes[1:4]):
        arrow(ax, (left[0] + width, left[1] + height / 2), (right[0], right[1] + height / 2), color=INK)
    ax.plot([0.870, 0.870, 0.130], [0.66, 0.61, 0.61], color=INK, lw=1.0)
    arrow(ax, (0.130, 0.61), (0.130, 0.29 + height), color=INK)
    for left, right in zip(main_boxes[4:7], main_boxes[5:8]):
        arrow(ax, (left[0] + width, left[1] + height / 2), (right[0], right[1] + height / 2), color=INK)

    branch_specs = [
        (0.025, "static router geometry\nbalanced silhouette 0.0212\n→ no placement prior · #75", GRAY),
        (0.520, "static core pinning\n308 regress · 196 infeasible\n→ not selected · #105", RED),
        (0.775, "regret-risk controller\nweak/no held-out signal\n→ not justified · #99", RED),
    ]
    for x, label, color in branch_specs:
        draw_box(ax, (x, 0.045), 0.20, 0.105, label, facecolor=LIGHT, edgecolor=color, linestyle="--", fontsize=4.9, linewidth=0.9)
    arrow(ax, (0.132, 0.29), (0.127, 0.15), color=GRAY, linestyle="--")
    arrow(ax, (0.622, 0.29), (0.627, 0.15), color=RED, linestyle="--")
    arrow(ax, (0.867, 0.29), (0.877, 0.15), color=RED, linestyle="--")

    ax.text(0.025, 0.93, "Service-side investigation", color=BLUE, fontsize=8, fontweight="bold")
    ax.text(0.025, 0.555, "Demand-side selection, generalization, and quality", color=GREEN, fontsize=8, fontweight="bold")
    ax.text(0.98, 0.955, "arrows encode decision dependency, not causal strength", ha="right", va="top", fontsize=6.2, color=GRAY)
    save_figure(fig, "research-journey")


if __name__ == "__main__":
    main()

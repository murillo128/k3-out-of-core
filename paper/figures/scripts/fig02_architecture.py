#!/usr/bin/env python3
"""Figure 2: render the implemented out-of-core architecture and ownership boundaries."""

from __future__ import annotations

import matplotlib.patches as patches
import matplotlib.pyplot as plt

from common import BLUE, GRAY, GREEN, INK, LIGHT, ORANGE, RED, arrow, configure, draw_box, save_figure


def main(stem: str = "fig02-architecture") -> None:
    configure()
    fig, ax = plt.subplots(figsize=(7.2, 3.85))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    routing_boundary = patches.FancyBboxPatch(
        (0.02, 0.49), 0.37, 0.48, boxstyle="round,pad=0.012", facecolor="#F7FAFD", edgecolor=BLUE, lw=1.2
    )
    provider_boundary = patches.FancyBboxPatch(
        (0.43, 0.07), 0.55, 0.90, boxstyle="round,pad=0.012", facecolor="#FAFAFA", edgecolor=INK, lw=1.2
    )
    ax.add_patch(routing_boundary)
    ax.add_patch(provider_boundary)
    ax.text(0.035, 0.94, "routing / execution ownership", color=BLUE, fontweight="bold", fontsize=8)
    ax.text(0.445, 0.94, "expert-provider / cache ownership", color=INK, fontweight="bold", fontsize=8)

    draw_box(ax, (0.055, 0.72), 0.14, 0.11, "ordinary K3\nrouter outputs", facecolor="white", edgecolor=BLUE)
    draw_box(ax, (0.225, 0.72), 0.13, 0.11, "bounded\nselection", facecolor="#DDEBF7", edgecolor=BLUE)
    draw_box(ax, (0.14, 0.54), 0.14, 0.11, "expert\nexecution", facecolor="white", edgecolor=BLUE)
    arrow(ax, (0.195, 0.775), (0.225, 0.775), color=BLUE)
    arrow(ax, (0.29, 0.72), (0.22, 0.65), color=BLUE)

    draw_box(ax, (0.47, 0.72), 0.16, 0.11, "provider API\n(get / materialize)", facecolor="#FFF0CC", edgecolor=ORANGE)
    draw_box(ax, (0.70, 0.72), 0.22, 0.11, "residency directory\nslot + generation + state", facecolor="#DDF3EA", edgecolor=GREEN)
    draw_box(ax, (0.48, 0.50), 0.14, 0.11, "resident\ncache slots", facecolor="#DDF3EA", edgecolor=GREEN)
    draw_box(ax, (0.70, 0.50), 0.22, 0.11, "admission / eviction\nmaterialization", facecolor="#FFF0CC", edgecolor=ORANGE)
    draw_box(ax, (0.48, 0.27), 0.14, 0.11, "pinned host /\noptional accel tier", facecolor=LIGHT, edgecolor=GRAY, linestyle="--", fontsize=7.4)
    draw_box(ax, (0.70, 0.27), 0.22, 0.11, "asynchronous backing path\nread → verify → publish", facecolor="#FCE2D7", edgecolor=RED)
    draw_box(ax, (0.70, 0.11), 0.22, 0.09, "immutable backing store", facecolor="white", edgecolor=RED)

    arrow(ax, (0.28, 0.59), (0.47, 0.775), text="selected expert key", text_offset=(0.0, -0.055))
    arrow(ax, (0.63, 0.775), (0.70, 0.775), color=GREEN)
    arrow(ax, (0.55, 0.72), (0.55, 0.61), color=GREEN, text="hit", text_offset=(-0.035, 0.0))
    arrow(ax, (0.81, 0.72), (0.81, 0.61), color=ORANGE)
    arrow(ax, (0.81, 0.50), (0.81, 0.38), color=RED, text="miss", text_offset=(-0.04, 0.0))
    arrow(ax, (0.81, 0.27), (0.81, 0.20), color=RED)
    arrow(ax, (0.70, 0.325), (0.62, 0.555), color=ORANGE, text="publish", text_offset=(-0.03, 0.0))
    arrow(ax, (0.62, 0.555), (0.70, 0.555), color=ORANGE)
    arrow(ax, (0.55, 0.50), (0.55, 0.38), color=GRAY, linestyle="--")

    # A deliberately one-way, read-only control signal: the router never owns cache mutation.
    ax.plot([0.81, 0.81, 0.29], [0.83, 0.87, 0.87], color=GREEN, ls="--", lw=1.3)
    ax.annotate(
        "",
        xy=(0.29, 0.83),
        xytext=(0.29, 0.87),
        arrowprops={"arrowstyle": "-|>", "color": GREEN, "lw": 1.3, "linestyle": "--"},
    )
    ax.text(0.55, 0.88, "read-only residency / service-state signal", ha="center", va="bottom", fontsize=6.8, color=GREEN)
    ax.text(
        0.45,
        0.035,
        "CPU experiments exercise the same provider contract; accelerator residency is an optional tier, not evidence claimed here.",
        fontsize=7,
        color="#555555",
    )
    save_figure(fig, stem)


if __name__ == "__main__":
    main()

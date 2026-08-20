#!/usr/bin/env python3
"""Figure 1: derive the selected-payload service-demand funnel from committed inputs."""

from __future__ import annotations

import matplotlib.pyplot as plt

from common import BLUE, GREEN, INK, LIGHT, ORANGE, RED, arrow, assert_equal, configure, draw_box, read_json, save_figure


MODEL_MANIFEST = "results/2026-08-10/issue73-k3-optimization/checkpoint-a/manifest.json"
MODEL_MANIFEST_SHA256 = "0dd860eb65164c23ce41b171c3ba502ab7a3ef4b97c41d9bd178bdabb0aa8b5f"
ISSUE102_PREREG = "corpus/phase13/issue102-preregistration-v1.json"
ISSUE102_PREREG_SHA256 = "31444069cb1221bbf585288fb39e476d69c7e74a1fb56b2ec23043b3a5cb6149"


def main() -> None:
    configure()
    manifest = read_json(MODEL_MANIFEST, MODEL_MANIFEST_SHA256)
    prereg = read_json(ISSUE102_PREREG, ISSUE102_PREREG_SHA256)
    model = manifest["model"]
    frozen = prereg["frozen_inputs"]
    for key in ("expert_bundle_bytes",):
        assert_equal(model[key], frozen[key], f"#73/#102 {key}")

    experts = int(model["experts_per_layer"])
    selected = int(model["selected_experts"])
    layers = int(model["routed_layers"])
    bundle_bytes = int(model["expert_bundle_bytes"])
    selections = selected * layers
    payload_gb = selections * bundle_bytes / 1e9
    assert_equal((experts, selected, layers, selections), (896, 16, 92, 1472), "model routing constants")
    if not (25.82 < payload_gb < 25.84):
        raise AssertionError(f"unexpected selected payload: {payload_gb} GB/token")

    fig, ax = plt.subplots(figsize=(7.2, 2.75))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    y, width, height = 0.62, 0.145, 0.22
    boxes = [
        (0.015, f"{experts} experts\nper routed layer", LIGHT, INK),
        (0.215, f"exact top-{selected}\nselection", "#DDEBF7", BLUE),
        (0.415, f"{layers} routed\nlayers", "#DDEBF7", BLUE),
        (0.615, f"{selections:,} selected\nExpertBundles/token", "#FFF0CC", ORANGE),
        (0.815, f"~{payload_gb:.2f} GB selected\npayload/token", "#FCE2D7", RED),
    ]
    for x, label, face, edge in boxes:
        draw_box(ax, (x, y), width, height, label, facecolor=face, edgecolor=edge, fontsize=8.3, linewidth=1.2)
    for left, right in zip(boxes[:-1], boxes[1:]):
        arrow(ax, (left[0] + width, y + height / 2), (right[0], y + height / 2))
    ax.text(0.52, 0.54, f"{selected} × {layers}; each bundle = {bundle_bytes / 1e6:.3f} MB", ha="center", fontsize=7, color="#555555")

    split = (0.887, y)
    hit_xy, backing_xy = (0.59, 0.12), (0.80, 0.12)
    draw_box(ax, hit_xy, 0.18, 0.19, "resident reuse /\ncache hit", facecolor="#DDF3EA", edgecolor=GREEN, fontsize=8.2)
    draw_box(ax, backing_xy, 0.185, 0.19, "physical backing\nservice", facecolor="#FCE2D7", edgecolor=RED, fontsize=8.2)
    arrow(ax, split, (hit_xy[0] + 0.09, hit_xy[1] + 0.19), color=GREEN)
    arrow(ax, split, (backing_xy[0] + 0.092, backing_xy[1] + 0.19), color=RED)
    ax.text(
        0.015,
        0.02,
        "Cumulative logical payload demand before reuse/cache — not resident RAM and not bytes physically read.",
        fontsize=8,
        fontweight="bold",
        color=RED,
    )
    save_figure(fig, "fig01-memory-mismatch")


if __name__ == "__main__":
    main()

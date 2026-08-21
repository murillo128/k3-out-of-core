#!/usr/bin/env python3
"""Appendix Figure A1: route overlap, endpoint sensitivity, and working-set limit."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_issue105 import ensure_issue105_inputs  # noqa: E402
from scripts.common import BLUE, ORANGE, PURPLE, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, require_file, save_figure, short_family  # noqa: E402


ROUTES = "results/2026-08-17/issue105/tables/route_features.csv"
ROUTES_SHA256 = "2541019fa05ff2988f209c61206e7917067d1c015cf3317e36be624afcafdcef"
PHYSICAL = "results/2026-08-17/issue105/tables/physical_runs.csv"
PHYSICAL_SHA256 = "47527a419d6ec3d1c9939beb3d6ec6b7776627079db6e4011707e746bb03b64c"
WORKING = "results/2026-08-17/issue105/analysis/working-set-analysis.json"
WORKING_SHA256 = "b8dcc52b3ebfe80dc589da57e0c630c23886024d8a42ab8016b9a5f8a04e3039"


def main() -> None:
    configure()
    released = ensure_issue105_inputs()
    overlap = json.loads(released["family-overlap-matrix.json"].read_text(encoding="utf-8"))
    endpoints = json.loads(released["stage-b2-family-length-route-endpoints.json"].read_text(encoding="utf-8"))
    assert_equal(overlap["status"], "pass", "overlap status")
    assert_equal(overlap["representative_pair_count"], 120, "representative pair count")
    pairs = overlap["representative_decode_pairs"]
    assert_equal(len(pairs), 120, "representative pair rows")
    assert_equal(endpoints["status"], "pass", "endpoint status")
    assert_equal(len(endpoints["within_family_b1_b8"]), 16, "within-family endpoints")
    assert_equal(len(endpoints["between_family_b1"]), 120, "between-family B1 pairs")
    assert_equal(len(endpoints["between_family_b8"]), 120, "between-family B8 pairs")

    routes = pd.read_csv(require_file(ROUTES, ROUTES_SHA256))
    physical = pd.read_csv(require_file(PHYSICAL, PHYSICAL_SHA256))
    working = read_json(WORKING, WORKING_SHA256)
    decode = routes[routes.phase == "DECODE"].copy()
    primary = physical[(physical.stage == "STAGE_A") & (physical.case_role == "primary") & (physical.policy == "S2_P50")]
    joined = decode.merge(primary[["case_id", "hit_ratio"]], on="case_id", validate="one_to_one")
    assert_equal(len(joined), 44, "observer-supported working-set rows")
    assert_set(joined.source_evidence_class, ["MEASURED_OBSERVER"], "route evidence class")
    feature = working["best_hit_ratio_feature"]["feature"]
    assert_equal(feature, "top16_selected_mass_fraction", "best frozen working-set feature")
    assert_equal(working["row_count"], 44, "working-set analysis rows")

    families = sorted({row["left_family"] for row in pairs} | {row["right_family"] for row in pairs})
    assert_equal(len(families), 16, "representative families")
    index = {family: position for position, family in enumerate(families)}
    matrix = np.eye(16)
    for row in pairs:
        left, right = index[row["left_family"]], index[row["right_family"]]
        matrix[left, right] = matrix[right, left] = float(row["cosine_similarity"])

    fig = plt.figure(figsize=(7.2, 6.25))
    grid = fig.add_gridspec(2, 2, height_ratios=[1.35, 0.9], hspace=0.43, wspace=0.33)
    ax_a = fig.add_subplot(grid[0, :])
    image = ax_a.pcolormesh(np.arange(17) - 0.5, np.arange(17) - 0.5, matrix, cmap="cividis", vmin=0, vmax=1, shading="flat", rasterized=False)
    ax_a.set_xlim(-0.5, 15.5)
    ax_a.set_ylim(15.5, -0.5)
    labels = [short_family(family) for family in families]
    ax_a.set_xticks(np.arange(16), labels, rotation=55, ha="right")
    ax_a.set_yticks(np.arange(16), labels)
    colorbar = fig.colorbar(image, ax=ax_a, pad=0.015, fraction=0.032)
    colorbar.set_label("decode route-demand cosine similarity")
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_OBSERVER · 120 pairs", y=1.04)

    ax_b = fig.add_subplot(grid[1, 0])
    groups = [
        [row["cosine_similarity"] for row in endpoints["within_family_b1_b8"]],
        [row["cosine_similarity"] for row in endpoints["between_family_b1"]],
        [row["cosine_similarity"] for row in endpoints["between_family_b8"]],
    ]
    ax_b.boxplot(groups, tick_labels=["within\nB1→B8", "between\nB1", "between\nB8"], showfliers=False, widths=0.55)
    for position, values in enumerate(groups, start=1):
        jitter = np.linspace(-0.14, 0.14, len(values))
        ax_b.scatter(position + jitter, values, s=7, facecolors="white", edgecolors=ORANGE if position == 1 else BLUE, lw=0.45, alpha=0.65)
    ax_b.set_ylabel("route-demand cosine similarity")
    ax_b.grid(axis="y", color="#E5E5E5", lw=0.6)
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "MEASURED_OBSERVER\nCURATED_FROM_MEASURED", y=1.04)

    ax_c = fig.add_subplot(grid[1, 1])
    for role, marker, color, label in [("STAGE_B_REPRESENTATIVE", "o", BLUE, "representative"), ("STAGE_B2_ENDPOINT", "s", ORANGE, "B1/B8 endpoint")]:
        group = joined[joined.selection_role == role]
        ax_c.scatter(group[feature], group.hit_ratio, s=23, marker=marker, facecolors="white", edgecolors=color, lw=1.0, label=label)
    lofo_r2 = working["best_hit_ratio_feature"]["pooled_oof_r_squared"]
    ax_c.set_xlabel("top-16 selected-mass fraction")
    ax_c.set_ylabel("physical S2_P50 hit ratio")
    ax_c.grid(color="#E5E5E5", lw=0.6)
    ax_c.legend(frameon=False, fontsize=6.4, loc="upper left")
    ax_c.text(0.98, 0.04, f"hit-ratio target\nLOFO R² = {lofo_r2:.6f}", transform=ax_c.transAxes, ha="right", fontsize=6.7, color=PURPLE)
    panel_label(ax_c, "C")
    evidence_badge(ax_c, "observer + physical inputs\nPOST_HOC_EXPLORATORY", y=1.04)
    fig.text(
        0.5,
        0.005,
        "Family association coexists with broad overlap; the compact working-set feature predicts hit ratio, not TPS, and leaves substantial variation.",
        ha="center",
        fontsize=6.7,
    )
    fig.subplots_adjust(bottom=0.10)
    save_figure(fig, "figa1-route-structure")


if __name__ == "__main__":
    main()

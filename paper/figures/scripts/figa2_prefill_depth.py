#!/usr/bin/env python3
"""Appendix Figure A2: bounded prefill-depth protocol diagnostic."""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

from common import BLUE, ORANGE, assert_equal, assert_set, configure, evidence_badge, panel_label, read_json, save_figure


CURVE = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/prefill-depth-locality-curve.json"
CURVE_SHA256 = "31d7fc0b4ae809ef296898bab9579b20010588ae0523685ed68453c3fdf9cfb4"
PREFIX = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/prefill-depth-prefix-corpus.json"
PREFIX_SHA256 = "9de09549e794de4b4f40edfde87db47932407dcad423742b1d91388c018c6e58"


def main() -> None:
    configure()
    curve = read_json(CURVE, CURVE_SHA256)
    prefix = read_json(PREFIX, PREFIX_SHA256)
    assert_equal(curve["status"], "pass", "prefill diagnostic status")
    assert_equal(curve["classification"], "bounded_diagnostic_not_stage_a_or_stage_c", "prefill evidence boundary")
    points = curve["points"]
    assert_equal(len(points), 5, "prefill depths")
    depths = [int(row["prefill_tokens"]) for row in points]
    assert_equal(depths, [9, 16, 32, 64, 100], "prefill depth grid")
    assert_equal(curve["identities"]["prefix_corpus_sha256"], PREFIX_SHA256, "prefix corpus binding")
    exact_hit = [row["EXACT"]["metrics"]["hit_ratio"]["median"] for row in points]
    s2_hit = [row["S2_P50"]["metrics"]["hit_ratio"]["median"] for row in points]
    ratios = [row["paired"]["s2_over_exact_tps_ratio"] for row in points]
    if not all(s2 > exact for s2, exact in zip(s2_hit, exact_hit)) or not all(value > 1 for value in ratios):
        raise AssertionError("S2 must retain positive locality/TPS advantage at each measured depth")
    assert_equal(curve["interpretations"]["S2_ADVANTAGE_DECAYS"]["verdict"], "SUPPORTED_OVERALL_NOT_MONOTONIC", "ratio interpretation")
    assert_equal(curve["interpretations"]["NO_PREFILL_DEPTH_EFFECT"]["verdict"], "REJECTED", "depth-effect interpretation")

    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(7.2, 3.15))
    ax_a.plot(depths, exact_hit, color=ORANGE, marker="s", lw=1.4, label="EXACT")
    ax_a.plot(depths, s2_hit, color=BLUE, marker="o", lw=1.4, label="S2_P50")
    ax_a.set_xlabel("prefill tokens consumed before decode")
    ax_a.set_ylabel("physical decode hit ratio")
    ax_a.grid(color="#E5E5E5", lw=0.6)
    ax_a.legend(frameon=False)
    panel_label(ax_a, "A")
    evidence_badge(ax_a, "MEASURED_PHYSICAL_DIAGNOSTIC", y=1.04)

    ax_b.axhline(1.0, color="#333333", ls="--", lw=0.9)
    ax_b.plot(depths, ratios, color=BLUE, marker="D", lw=1.4)
    ax_b.set_xlabel("prefill tokens consumed before decode")
    ax_b.set_ylabel("S2_P50 / EXACT physical TPS")
    ax_b.grid(color="#E5E5E5", lw=0.6)
    ax_b.text(0.98, 0.95, f"N=9: {ratios[0]:.3f}×\nN=100: {ratios[-1]:.3f}×", transform=ax_b.transAxes, ha="right", va="top", fontsize=7)
    panel_label(ax_b, "B")
    evidence_badge(ax_b, "bounded explanatory diagnostic", y=1.04)
    fig.text(
        0.5,
        0.01,
        "Interior points have one clean process per arm; the diagnostic explains protocol choice and is not Stage-A/Stage-C acceptance evidence.",
        ha="center",
        fontsize=6.7,
    )
    fig.subplots_adjust(wspace=0.30, bottom=0.19)
    save_figure(fig, "figa2-prefill-depth")


if __name__ == "__main__":
    main()

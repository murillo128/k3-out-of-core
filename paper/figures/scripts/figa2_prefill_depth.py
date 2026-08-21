#!/usr/bin/env python3
"""Appendix Figure A2: measured diagnostic and pre-outcome protocol evolution."""

from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from common import BLUE, GRAY, GREEN, LIGHT, ORANGE, RED, arrow, assert_equal, assert_set, configure, draw_box, evidence_badge, panel_label, read_json, require_file, save_figure


CURVE = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/prefill-depth-locality-curve.json"
CURVE_SHA256 = "31d7fc0b4ae809ef296898bab9579b20010588ae0523685ed68453c3fdf9cfb4"
PREFIX = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/prefill-depth-prefix-corpus.json"
PREFIX_SHA256 = "9de09549e794de4b4f40edfde87db47932407dcad423742b1d91388c018c6e58"
CORPUS = "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt/issue102-normalized-prompt-level.csv"
CORPUS_SHA256 = "c3cb07d5d9cc90c14e77f4cc14744f7c70c682d7e267aa332095dd9f3b5a82ca"


def main() -> None:
    configure()
    curve = read_json(CURVE, CURVE_SHA256)
    prefix = read_json(PREFIX, PREFIX_SHA256)
    corpus = pd.read_csv(require_file(CORPUS, CORPUS_SHA256))
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
    assert_equal(len(corpus), 128, "frozen #102 corpus rows")
    assert_equal(corpus.case_id.nunique(), 128, "frozen unique case IDs")
    assert_equal(corpus.semantic_family.nunique(), 16, "semantic families")
    assert_set(corpus.length_level, range(1, 9), "ordered length levels")
    assert_equal((int(corpus.templated_prompt_tokens.min()), int(corpus.templated_prompt_tokens.max())), (154, 599), "templated token span")
    for family, group in corpus.sort_values("length_level").groupby("semantic_family"):
        assert_equal(len(group), 8, f"{family} corpus rows")
        tokens = group.templated_prompt_tokens.to_numpy()
        if not np.all(np.diff(tokens) > 0):
            raise AssertionError(f"{family} must retain strict b1→b8 token ordering")

    fig = plt.figure(figsize=(7.2, 6.8))
    grid = fig.add_gridspec(3, 2, height_ratios=[1.0, 0.72, 1.12])
    ax_a = fig.add_subplot(grid[0, 0])
    ax_b = fig.add_subplot(grid[0, 1])
    ax_c = fig.add_subplot(grid[1, :])
    ax_d = fig.add_subplot(grid[2, :])
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

    ax_c.set_xlim(0, 1)
    ax_c.set_ylim(0, 1)
    ax_c.axis("off")
    protocol_boxes = [
        (0.02, "#98 helper\nfixed ~100-token prompt\nn_ctx=256", GRAY, "white"),
        (0.215, "stop prefill at\nfirst cache-full boundary\ntoken 9", ORANGE, "white"),
        (0.41, "#102 gate: BLOCKED\nbefore primary outcomes\ninvalid for generalization", RED, LIGHT),
        (0.605, "full templated prompt\nunder EXACT\npreserve cache state", GREEN, "white"),
        (0.80, "activate policy at decode\nreset counters · 64 forwards\nn_ctx=768", GREEN, "white"),
    ]
    for x, label, edge, face in protocol_boxes:
        draw_box(ax_c, (x, 0.30), 0.18, 0.40, label, facecolor=face, edgecolor=edge, fontsize=5.8, linewidth=1.05)
    for left, right in zip(protocol_boxes[:-1], protocol_boxes[1:]):
        arrow(ax_c, (left[0] + 0.18, 0.50), (right[0], 0.50), color=GREEN if right[0] >= 0.61 else GRAY)
    ax_c.text(0.71, 0.17, "superseding full-prompt protocol qualified before outcome inspection", color=GREEN, fontsize=6.2, ha="center")
    panel_label(ax_c, "C")
    evidence_badge(ax_c, "PROTOCOL / DESIGN QUALIFICATION", y=0.98)

    for _, group in corpus.sort_values("length_level").groupby("semantic_family"):
        ax_d.plot(group.length_level, group.templated_prompt_tokens, color=BLUE, alpha=0.28, lw=0.8, marker="o", ms=2.0)
    median = corpus.groupby("length_level", sort=True).templated_prompt_tokens.median()
    ax_d.plot(median.index, median.values, color=ORANGE, marker="D", lw=1.6, ms=4, label="cross-family median")
    ax_d.set_xlabel("ordered within-family length level")
    ax_d.set_ylabel("actual templated prompt tokens")
    ax_d.set_xticks(range(1, 9), [f"b{i}" for i in range(1, 9)])
    ax_d.grid(color="#E5E5E5", lw=0.6)
    ax_d.legend(frameon=False, loc="upper left")
    ax_d.text(
        0.985,
        0.07,
        "guessed absolute bands failed 128/128\nretained: 16 families × 8 strict ordered levels\nactual token count kept separately · span 154..599",
        transform=ax_d.transAxes,
        ha="right",
        va="bottom",
        fontsize=6.2,
        color=RED,
    )
    panel_label(ax_d, "D")
    evidence_badge(ax_d, "FROZEN TOKENIZER PREFLIGHT / CORPUS", y=1.04)
    fig.text(0.5, 0.008, "Panels A/B are a bounded physical diagnostic; Panels C/D document pre-outcome protocol and corpus qualification, not performance outcomes.", ha="center", fontsize=6.5)
    fig.subplots_adjust(wspace=0.30, hspace=0.55, bottom=0.07, top=0.95)
    save_figure(fig, "figa2-prefill-depth")


if __name__ == "__main__":
    main()

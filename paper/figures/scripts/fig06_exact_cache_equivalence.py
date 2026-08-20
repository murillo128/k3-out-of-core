#!/usr/bin/env python3
"""Figure 6: discrete EXACT-replay cache brackets matching physical S2 locality."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from common import BLUE, GRAY, ORANGE, RED, assert_equal, assert_set, configure, evidence_badge, require_file, save_figure


CURVES = "results/2026-08-17/issue105/tables/capacity_curves.parquet"
CURVES_SHA256 = "de97464ea1f10b3a1439ba0f52a51861cbbb1007bf19d3bcb84d64bd8bd1b0ba"
VIRTUAL = "results/2026-08-17/issue105/analysis/virtual-cache-capacity.csv"
VIRTUAL_SHA256 = "311464cbffb6385b1e62df1038fb1c832b57d3659c2653a06534b0fc2c00d80f"
SUMMARY = "results/2026-08-17/issue105/analysis/virtual-cache-capacity-summary.json"
SUMMARY_SHA256 = "9abe39c246f9ee930369936ed2b2f420a71a0263aa935034dced1ecb6e71f865"


def main() -> None:
    configure()
    curves = pd.read_parquet(require_file(CURVES, CURVES_SHA256))
    virtual = pd.read_csv(require_file(VIRTUAL, VIRTUAL_SHA256))
    with require_file(SUMMARY, SUMMARY_SHA256).open(encoding="utf-8") as handle:
        summary = json.load(handle)
    assert_equal(len(curves), 3816, "capacity-curve rows")
    expected_curve_classes = {
        ("EXACT_LRU", "EXACT_REPLAY", "EXACT_REPLAY"): 1584,
        ("S2_P50_FIXED_ROUTE", "FIXED_ROUTE_COUNTERFACTUAL", "FIXED_ROUTE_COUNTERFACTUAL"): 792,
        ("COMMITTEE_PIN_FIXED_ROUTE", "FIXED_ROUTE_COUNTERFACTUAL", "FIXED_ROUTE_COUNTERFACTUAL"): 1440,
    }
    actual_curve_classes = curves.groupby(
        ["policy_result_class", "source_evidence_class", "derived_evidence_class"]
    ).size().to_dict()
    assert_equal(actual_curve_classes, expected_curve_classes, "capacity-curve evidence-class counts")
    assert_equal(len(virtual), 44, "virtual-capacity rows")
    assert_equal(virtual.case_id.nunique(), 44, "virtual-capacity unique prompts")
    assert_set(virtual.physical_target_evidence_class, ["MEASURED_PHYSICAL"], "S2 reference evidence")
    assert_set(virtual.exact_evidence_class, ["EXACT_REPLAY"], "EXACT evidence")
    assert_equal(int(summary["row_count"]), 44, "virtual-capacity summary row count")

    records = []
    for row in virtual.itertuples(index=False):
        interval = json.loads(row.hit_derived_intervals_json)
        exact = interval.get("exact_capacity", {})
        status = interval.get("status", "INCONCLUSIVE")
        if status == "BRACKETED":
            lower, upper = exact["slots"]
            records.append(
                {
                    "case_id": row.case_id,
                    "role": row.selection_role,
                    "level": int(row.length_level),
                    "lower": lower / row.physical_reference_slots,
                    "upper": upper / row.physical_reference_slots,
                    "status": status,
                }
            )
        else:
            records.append(
                {"case_id": row.case_id, "role": row.selection_role, "level": int(row.length_level), "lower": np.nan, "upper": np.nan, "status": "INCONCLUSIVE"}
            )
    records = sorted(records, key=lambda item: (np.nan_to_num(item["upper"], nan=99), item["case_id"]))
    assert_equal(len(records), 44, "rendered virtual-capacity records")

    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    y = np.arange(len(records))
    role_styles = {
        "STAGE_B_REPRESENTATIVE": ("o", BLUE),
        "STAGE_B2_ENDPOINT": ("s", ORANGE),
    }
    for index, item in enumerate(records):
        marker, color = role_styles.get(item["role"], ("D", RED))
        if item["status"] == "BRACKETED":
            # Interval semantics are (lower, upper]; do not interpolate a threshold.
            ax.plot([item["lower"], item["upper"]], [index, index], color=color, lw=1.2)
            ax.scatter(item["lower"], index, marker=marker, s=18, facecolors="white", edgecolors=color, lw=0.8, zorder=3)
            ax.scatter(item["upper"], index, marker=marker, s=18, color=color, lw=0.8, zorder=3)
        else:
            ax.scatter(0.98, index, marker="x", s=22, color=RED, lw=1.2)
            ax.text(1.02, index, "INCONCLUSIVE", va="center", fontsize=6, color=RED)

    ax.axvline(1.0, color="#222222", ls="--", lw=1.1)
    ax.set_yticks(y, [item["case_id"] for item in records])
    ax.set_xlabel("EXACT replay cache capacity / physical S2_P50 cache capacity")
    ax.set_ylabel("frozen observer prompt")
    ax.grid(axis="x", color="#E5E5E5", lw=0.6)
    ax.legend(
        handles=[
            Line2D([], [], marker="o", mfc="white", mec=BLUE, color=BLUE, label="representative: (lower, upper]"),
            Line2D([], [], marker="s", mfc="white", mec=ORANGE, color=ORANGE, label="endpoint: (lower, upper]"),
            Line2D([], [], color="#222222", ls="--", label="physical S2_P50 capacity = 1.0"),
        ],
        loc="upper left",
        frameon=False,
    )
    fig.text(
        0.99,
        0.015,
        "Open marker = failing lower tested capacity; filled marker = first passing tested capacity.\nDiscrete brackets only; no interpolated threshold or measured-RAM-saving claim.",
        ha="right",
        va="bottom",
        fontsize=6.8,
    )
    evidence_badge(ax, "target: MEASURED_PHYSICAL\nlarger capacity: EXACT_REPLAY")
    fig.subplots_adjust(bottom=0.12)
    save_figure(fig, "fig06-exact-cache-equivalence")


if __name__ == "__main__":
    main()

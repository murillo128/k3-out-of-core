#!/usr/bin/env python3
"""Normalize independent WASTE sampled baselines against project and MIN rows."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity  # noqa: E402


def index(rows: list[dict[str, Any]], policy: str | None = None) -> dict[tuple[Any, ...], dict[str, Any]]:
    result = {}
    for row in rows:
        if policy is not None and row.get("config") != policy: continue
        key = (row["case"], row["role"], row["budget_slots"], row["hot_slots"], row["cold_slots"])
        result[key] = row
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text())
    waste = replay["baselines"]["waste"]
    min_rows = index(replay["baselines"]["belady_min"])
    project_lru = index(replay["rows"], "LRU-GLOBAL")
    project_lfru = index(replay["rows"], "LFRU-GLOBAL")
    comparisons = []
    aggregates: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in waste["rows"]:
        key = (row["case"], row["role"], row["budget_slots"], row["hot_slots"], row["cold_slots"])
        project = project_lru[key] if row["policy"] == "waste_sampled_lru" else project_lfru[key]
        minimum = min_rows[key]
        observed = row["summary"]
        base = project["summary"]
        comparison = {"case": row["case"], "role": row["role"], "budget_slots": row["budget_slots"],
                      "hot_slots": row["hot_slots"], "cold_slots": row["cold_slots"], "policy": row["policy"],
                      "hit_delta": observed[f"{row['role']}_hits"] - base[f"{row['role']}_hits"],
                      "managed_byte_delta": (observed["backing_store_bytes"] + observed["cold_bytes"] + observed["hot_bytes"]) -
                                            (base["backing_store_bytes"] + base["cold_bytes"] + base["hot_bytes"]),
                      "victim_regret_vs_full_scan": row[f"{row['role']}_evictions"] - project["tier_counters"]["evictions"],
                      "backing_store_byte_regret_vs_min": observed["backing_store_bytes"] - minimum["summary"]["backing_store_bytes"]}
        comparisons.append(comparison)
        for field in ("hit_delta", "managed_byte_delta", "victim_regret_vs_full_scan", "backing_store_byte_regret_vs_min"):
            aggregates[row["policy"]][field] += comparison[field]
    output = {
        "schema_version": "phase9-waste-comparison-v1", "status": "pass",
        "input": file_identity(args.replay), "attribution": waste["attribution"],
        "semantics": {"sample_width": 16, "with_replacement": True, "initial_rng": "0x9e3779b9",
                      "implementation": "independent project-side replay; no WASTE source imported"},
        "comparisons": comparisons, "aggregates": {key: dict(value) for key, value in sorted(aggregates.items())},
        "interpretation": {
            "transferability": "corroborating replay baseline only; not a default-selection authority",
            "cache_floor_claim": "not transferable without equal model topology, representation, record layout, trunk/KV, hardware, OS, transport, and backend",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "output": str(args.output), "rows": len(comparisons)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


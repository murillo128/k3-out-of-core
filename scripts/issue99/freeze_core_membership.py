#!/usr/bin/env python3
"""Freeze the #105-consumed DECODE core memberships used by issue #99."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from protocol import CORE_GAMMAS, ISSUE105_ANALYSIS_CODE, ISSUE105_RELEASE, ISSUE105_TARGET, atomic_json, file_identity


EXPECTED_SOURCE_SHA256 = "b7a23e73108c1612c3db9d822c9d82ff065764a4682ef037cdc277ba5d730566"
EXPECTED_COUNTS = {"1.0": 1422, "0.8": 6939}


def freeze(source: Path) -> dict[str, object]:
    identity = file_identity(source)
    if identity["sha256"] != EXPECTED_SOURCE_SHA256:
        raise ValueError("frozen core source hash mismatch")
    with source.open() as stream:
        value = json.load(stream)
    if value.get("schema_version") != "phase13-6pg-standing-committee-core-periphery-v1" or \
            value.get("status") != "pass":
        raise ValueError("invalid frozen core source")
    selected = {}
    for group in value["phases"]["DECODE"]["gamma_sensitivity"]:
        gamma = f"{float(group['gamma']):.1f}"
        if float(group["gamma"]) not in CORE_GAMMAS:
            continue
        layers = {str(int(row["layer"])): sorted(map(int, row["core_experts"])) for row in group["layers"]}
        if len(layers) != 92 or sum(map(len, layers.values())) != EXPECTED_COUNTS[gamma]:
            raise ValueError(f"core membership count mismatch at gamma={gamma}")
        selected[gamma] = {
            "gamma": float(group["gamma"]),
            "threshold_family_count": int(group["threshold_family_count"]),
            "expert_key_count": sum(map(len, layers.values())),
            "layers": layers,
        }
    if set(selected) != set(EXPECTED_COUNTS):
        raise ValueError("required core gamma definitions are absent")
    return {
        "schema_version": "issue99-frozen-core-membership-v1",
        "status": "pass",
        "evidence_class": "FROZEN_ISSUE105_EFFECT_MODIFIER",
        "primary_gamma": 1.0,
        "sensitivity_gamma": 0.8,
        "source": identity,
        "source_catalog_authority": {
            "release": ISSUE105_RELEASE,
            "final_reviewed_target": ISSUE105_TARGET,
            "analysis_code": ISSUE105_ANALYSIS_CODE,
            "catalog_disposition": "CURATED_FROM_MEASURED",
        },
        "semantic_guard": "frequency-defined membership only; no semantic-function claim",
        "definitions": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(
        "/mnt/nvme1/issue102/stage-b-analysis-v1/standing-committee-core-periphery.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = freeze(args.source)
    atomic_json(args.output, value)
    print(f"ISSUE99_CORE_FREEZE status=pass output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

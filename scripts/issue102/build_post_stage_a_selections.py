#!/usr/bin/env python3
"""Freeze issue-102 Stage-B, Stage-B2, and Stage-C prompt selections."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-a-checkpoint", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": row["case_id"],
        "semantic_family": row["semantic_family"],
        "length_level": row["length_level"],
        "actual_templated_prompt_tokens": row["templated_prompt_tokens"],
        "stage_a_hit_ratio": row["hit_ratio"],
        "stage_a_ordinal": row["ordinal"],
        "stage_a_result_sha256": row["result_sha256"],
        "stage_a_envelope_sha256": row["envelope_sha256"],
    }


def main() -> int:
    args = arguments()
    checkpoint_path = args.stage_a_checkpoint.resolve()
    output_path = args.output.resolve()
    checkpoint = json.loads(checkpoint_path.read_text())
    rows = checkpoint["primary_rows"]
    if checkpoint["status"] != "pass" or len(rows) != 128:
        raise ValueError("final Stage-A checkpoint is not a complete pass")

    families = sorted({row["semantic_family"] for row in rows})
    if len(families) != 16:
        raise ValueError("Stage A does not contain exactly 16 semantic families")
    if any(
        sorted(row["length_level"] for row in rows if row["semantic_family"] == family)
        != list(range(1, 9))
        for family in families
    ):
        raise ValueError("Stage A does not contain an exact 16x8 family/length grid")

    representative_audits: list[dict[str, Any]] = []
    representatives: list[dict[str, Any]] = []
    for family in families:
        candidates = [row for row in rows if row["semantic_family"] == family]
        median = statistics.median(row["hit_ratio"] for row in candidates)
        ranked = sorted(
            candidates,
            key=lambda row: (abs(row["hit_ratio"] - median), row["length_level"], row["case_id"]),
        )
        chosen = selected_row(ranked[0])
        chosen["family_median_hit_ratio"] = median
        chosen["absolute_distance_to_family_median"] = abs(ranked[0]["hit_ratio"] - median)
        representatives.append(chosen)
        representative_audits.append({
            "semantic_family": family,
            "family_median_hit_ratio": median,
            "ranking": [
                {
                    "case_id": row["case_id"],
                    "length_level": row["length_level"],
                    "hit_ratio": row["hit_ratio"],
                    "absolute_distance_to_median": abs(row["hit_ratio"] - median),
                }
                for row in ranked
            ],
            "selected_case_id": ranked[0]["case_id"],
        })

    representative_ids = {row["case_id"] for row in representatives}
    if len(representative_ids) != 16:
        raise ValueError("Stage-B representative IDs are not unique")

    stage_b2_endpoints = [
        selected_row(row)
        for row in sorted(rows, key=lambda item: (item["semantic_family"], item["length_level"], item["case_id"]))
        if row["length_level"] in (1, 8)
    ]
    if len(stage_b2_endpoints) != 32:
        raise ValueError("Stage-B2 endpoint selection is not exactly 32 prompts")

    non_representatives = [row for row in rows if row["case_id"] not in representative_ids]
    low_extremes = sorted(non_representatives, key=lambda row: (row["hit_ratio"], row["case_id"]))[:4]
    low_ids = {row["case_id"] for row in low_extremes}
    high_candidates = [row for row in non_representatives if row["case_id"] not in low_ids]
    high_extremes = sorted(high_candidates, key=lambda row: (-row["hit_ratio"], row["case_id"]))[:4]
    stage_c_rows = (
        [{**row, "selection_role": "FAMILY_REPRESENTATIVE"} for row in representatives]
        + [{**selected_row(row), "selection_role": "ADDITIONAL_LOW_HIT"} for row in low_extremes]
        + [{**selected_row(row), "selection_role": "ADDITIONAL_HIGH_HIT"} for row in high_extremes]
    )
    stage_c_ids = {row["case_id"] for row in stage_c_rows}
    if len(stage_c_rows) != 24 or len(stage_c_ids) != 24:
        raise ValueError("Stage-C selection is not exactly 24 unique prompts")

    b2_ids = {row["case_id"] for row in stage_b2_endpoints}
    output: dict[str, Any] = {
        "schema_version": "phase13-6pg-post-stage-a-selections-v1",
        "status": "pass",
        "provenance": "FROZEN_STAGE_A_DERIVATION",
        "input": {
            "stage_a_checkpoint": {
                "path": str(checkpoint_path),
                "sha256": sha256(checkpoint_path),
                "schema_version": checkpoint["schema_version"],
            },
            "project_sha": checkpoint["identities"]["project_at_checkpoint"],
            "nested_llama_cpp_sha": checkpoint["identities"]["nested_llama_cpp"],
        },
        "rules": {
            "stage_b": (
                "Per family, choose the Stage-A S2_P50 hit ratio closest to the family median; "
                "tie-break by lower length_level, then stable case_id."
            ),
            "stage_b2": "For every family choose the fixed length_level=1 and length_level=8 endpoints.",
            "stage_c": (
                "Use all 16 Stage-B representatives, then four lowest-hit and four highest-hit "
                "Stage-A prompts not already selected; stable case_id breaks equal-hit ties."
            ),
        },
        "stage_b": {
            "count": len(representatives),
            "representatives": representatives,
            "selection_audit": representative_audits,
        },
        "stage_b2": {
            "count": len(stage_b2_endpoints),
            "endpoints": stage_b2_endpoints,
            "representative_overlap_count": len(representative_ids & b2_ids),
            "unique_exact_observer_prompt_count_after_deduplication": len(representative_ids | b2_ids),
        },
        "stage_c": {
            "count": len(stage_c_rows),
            "prompts": sorted(stage_c_rows, key=lambda row: (row["selection_role"], row["case_id"])),
            "additional_low_hit_case_ids": [row["case_id"] for row in low_extremes],
            "additional_high_hit_case_ids": [row["case_id"] for row in high_extremes],
        },
        "capture_constraints": {
            "stage_b_policy": "EXACT",
            "stage_b_timing_evidence": False,
            "stage_b_fresh_process_per_capture": True,
            "stage_b_full_prompt_plus_decode_forwards": 64,
            "stage_c_policies": ["EXACT", "KNEE"],
            "stage_c_pair_order": "ALTERNATE_BY_FROZEN_PROMPT_ORDER",
        },
        "disposition": "POST_STAGE_A_SELECTIONS_FROZEN",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output_path), "sha256": sha256(output_path), "status": output["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

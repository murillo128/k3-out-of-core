#!/usr/bin/env python3
"""Build the outcome-free issue-102 Stage-B/B2 observer preregistration."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-manifest", type=pathlib.Path, required=True)
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--helper-source", type=pathlib.Path, required=True)
    parser.add_argument("--helper-binary", type=pathlib.Path, required=True)
    parser.add_argument("--runner", type=pathlib.Path, required=True)
    parser.add_argument("--model-identity", type=pathlib.Path, required=True)
    parser.add_argument("--build-fingerprint", type=pathlib.Path, required=True)
    parser.add_argument("--runtime-project-sha", required=True)
    parser.add_argument("--nested-sha", required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve()
    value: dict[str, Any] = {"path": str(resolved), "sha256": sha256(resolved)}
    if resolved.suffix == ".json":
        document = json.loads(resolved.read_text())
        if "schema_version" in document:
            value["schema_version"] = document["schema_version"]
    return value


def main() -> int:
    args = arguments()
    selection_path = args.selection_manifest.resolve()
    selection = json.loads(selection_path.read_text())
    if selection["status"] != "pass" or selection["disposition"] != "POST_STAGE_A_SELECTIONS_FROZEN":
        raise ValueError("post-Stage-A selection manifest is not frozen")

    representatives = sorted(selection["stage_b"]["representatives"], key=lambda row: row["case_id"])
    representative_ids = {row["case_id"] for row in representatives}
    remaining_endpoints = sorted(
        (row for row in selection["stage_b2"]["endpoints"] if row["case_id"] not in representative_ids),
        key=lambda row: row["case_id"],
    )
    ordered = [
        ("STAGE_B_REPRESENTATIVE", row) for row in representatives
    ] + [
        ("STAGE_B2_ENDPOINT", row) for row in remaining_endpoints
    ]
    if len(representatives) != 16 or len(remaining_endpoints) != 28 or len(ordered) != 44:
        raise ValueError("observer capture plan is not the frozen 16 + 28 deduplicated set")

    capture_plan = []
    for ordinal, (role, row) in enumerate(ordered, 1):
        capture_plan.append({
            "ordinal": ordinal,
            "selection_role": role,
            "case_id": row["case_id"],
            "semantic_family": row["semantic_family"],
            "length_level": row["length_level"],
            "actual_templated_prompt_tokens": row["actual_templated_prompt_tokens"],
            "output_directory": str(args.output_root.resolve() / f"run-{ordinal:03d}-{row['case_id']}"),
        })

    output_path = args.output.resolve()
    output: dict[str, Any] = {
        "schema_version": "phase13-6pg-stage-b-observer-preregistration-v1",
        "status": "frozen",
        "provenance": "PREREGISTERED_MEASURED_OBSERVER",
        "outcome_inspection": "NONE",
        "inputs": {
            "selection_manifest": identity(selection_path),
            "corpus": identity(args.corpus),
            "model_identity": identity(args.model_identity),
            "build_fingerprint": identity(args.build_fingerprint),
        },
        "runtime": {
            "project_source_target": args.runtime_project_sha,
            "nested_llama_cpp": args.nested_sha,
            "helper_source": identity(args.helper_source),
            "helper_binary": identity(args.helper_binary),
            "runner": identity(args.runner),
            "build_type": "Release",
            "native_optimization": True,
        },
        "configuration": {
            "policy": "EXACT",
            "protocol": "full-prompt",
            "fresh_process_per_capture": True,
            "managed_cache_start_occupancy": 0,
            "cache_slots": 7849,
            "cache_bytes": 137728475136,
            "n_ctx": 768,
            "threads": 32,
            "backend": "CPU-only Mode-P/BATCHED",
            "io": "native io_uring + O_DIRECT",
            "candidate_count": 32,
            "prefill_capture": "complete sequential templated prompt",
            "decode_forwards": 64,
            "selected_experts": 16,
            "routed_layers": 92,
            "performance_evidence": False,
            "observer_serialization": "post-run JSON",
        },
        "capture_order_rule": (
            "All 16 Stage-B representatives in stable case_id order, followed by the 28 Stage-B2 "
            "b1/b8 endpoints not already captured, also in stable case_id order."
        ),
        "capture_count": len(capture_plan),
        "capture_plan": capture_plan,
        "validation": {
            "expected_records_per_capture": "(actual_templated_prompt_tokens + 64) * 92",
            "expected_selected_occurrences_per_record": 16,
            "expected_candidate_occurrences_per_record": 32,
            "exact_selected_route_must_equal_candidate_prefix": True,
            "host_safety_envelope_required": True,
            "physical_timing_claim_allowed": False,
            "failed_capture_replacement_allowed": False,
        },
        "disposition": "READY_FOR_STAGE_B_OBSERVER_CAPTURE",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output_path), "sha256": sha256(output_path), "status": output["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

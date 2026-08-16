#!/usr/bin/env python3
"""Freeze the issue-102 Stage-C EXACT/KNEE execution contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
from typing import Any


EXPECTED_SELECTION_SHA256 = "cd4abec601244a9b187347c57bf4a5516970f3b615141070488f424b9a1db96c"
EXPECTED_STAGE_A_SHA256 = "baf8d7583175aa55eca8c7183608259bb2d30f350a8a643f8b40d4831a362786"
EXPECTED_HANDOFF_SHA256 = "e560db9dfc1c05c8aa163d88b44073bebbfbafefb4c86dfbf78f075c3b403eca"
EXPECTED_HYGIENE_SHA256 = "0caab39228615412f86ba69f4a58680b0480f99c05673f1e22ab16f55730bf27"
EXPECTED_CORPUS_SHA256 = "e0f1746f987f888d68a36261a2822f4754a413b7799c6252aae8a4ded2a900d8"
EXPECTED_BINARY_SHA256 = "c35cdc52d3669b080972e1c1ac68df6b88290e79d46c92edde2f48eae3733975"
EXPECTED_RUNNER_SHA256 = "0e09960035666f15bfc82cef2a8dd81358f744a848f3f1f633d27d420afeca92"
EXPECTED_MODEL_IDENTITY_SHA256 = "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2"
EXPECTED_BUILD_FINGERPRINT_SHA256 = "d150d179f41ebd2deab49b663e64c909b7d8fa6b4546c716aee889479f633a10"
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
EXPECTED_CACHE_BYTES = 137728475136
EXPECTED_CACHE_SLOTS = 7849


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-project-sha", required=True)
    parser.add_argument("--selection", type=pathlib.Path, required=True)
    parser.add_argument("--stage-a", type=pathlib.Path, required=True)
    parser.add_argument("--handoff", type=pathlib.Path, required=True)
    parser.add_argument("--hygiene", type=pathlib.Path, required=True)
    parser.add_argument("--corpus", type=pathlib.Path, required=True)
    parser.add_argument("--binary", type=pathlib.Path, required=True)
    parser.add_argument("--runner", type=pathlib.Path, required=True)
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--model-identity", type=pathlib.Path, required=True)
    parser.add_argument("--build-fingerprint", type=pathlib.Path, required=True)
    parser.add_argument("--checkpoint-b-url", required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path, expected_sha256: str | None = None) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    result = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if expected_sha256 is not None and result["sha256"] != expected_sha256:
        raise ValueError(f"input identity changed: {resolved}")
    if resolved.suffix == ".json":
        with resolved.open() as stream:
            document = json.load(stream)
        if "schema_version" in document:
            result["schema_version"] = document["schema_version"]
    return result


def main() -> None:
    args = arguments()
    selection_id = identity(args.selection, EXPECTED_SELECTION_SHA256)
    stage_a_id = identity(args.stage_a, EXPECTED_STAGE_A_SHA256)
    handoff_id = identity(args.handoff, EXPECTED_HANDOFF_SHA256)
    hygiene_id = identity(args.hygiene, EXPECTED_HYGIENE_SHA256)
    corpus_id = identity(args.corpus, EXPECTED_CORPUS_SHA256)
    binary_id = identity(args.binary, EXPECTED_BINARY_SHA256)
    runner_id = identity(args.runner, EXPECTED_RUNNER_SHA256)
    model_id = identity(args.model_identity, EXPECTED_MODEL_IDENTITY_SHA256)
    fingerprint_id = identity(args.build_fingerprint, EXPECTED_BUILD_FINGERPRINT_SHA256)
    controller_id = identity(pathlib.Path(__file__).with_name("run_stage_c_campaign.py"))
    generator_id = identity(pathlib.Path(__file__))

    with args.selection.open() as stream:
        selection = json.load(stream)
    with args.stage_a.open() as stream:
        stage_a = json.load(stream)
    with args.handoff.open() as stream:
        handoff = json.load(stream)
    with args.hygiene.open() as stream:
        hygiene = json.load(stream)
    if (
        selection.get("status") != "pass"
        or stage_a.get("status") != "pass"
        or handoff.get("status") != "pass"
        or not handoff["checkpoint_b_readiness"]["safe_to_request_independent_checkpoint_b_review"]
        or hygiene.get("status") != "pass"
        or not all(hygiene.get("gate", {}).values())
        or hygiene["disposition"] != "READY_FOR_STAGE_C_PREFLIGHT"
        or hygiene["files"]["resident_bytes_after"] != 0
        or hygiene["operation"]["model_or_runtime_file_touched"] is not False
    ):
        raise ValueError("Stage-C prerequisite evidence is not a clean PASS")
    if (
        handoff["execution_target"]["nested_llama_cpp_sha"] != EXPECTED_NESTED_SHA
        or stage_a["identities"]["nested_llama_cpp"] != EXPECTED_NESTED_SHA
        or stage_a["identities"]["cache_bytes"] != EXPECTED_CACHE_BYTES
        or stage_a["identities"]["cache_slots"] != EXPECTED_CACHE_SLOTS
    ):
        raise ValueError("frozen nested/capacity identity changed")

    prompts = selection["stage_c"]["prompts"]
    if len(prompts) != 24 or len({row["case_id"] for row in prompts}) != 24:
        raise ValueError("Stage-C selection is not 24 unique frozen prompts")
    stage_a_rows = {row["case_id"]: row for row in stage_a["primary_rows"]}
    plan = []
    run_ordinal = 0
    for prompt_ordinal, selected in enumerate(prompts, 1):
        stage_a_row = stage_a_rows[selected["case_id"]]
        if (
            stage_a_row["result_sha256"] != selected["stage_a_result_sha256"]
            or stage_a_row["envelope_sha256"] != selected["stage_a_envelope_sha256"]
            or stage_a_row["hit_ratio"] != selected["stage_a_hit_ratio"]
        ):
            raise ValueError(f"Stage-A selection evidence changed: {selected['case_id']}")
        pair_order = ["EXACT", "KNEE"] if prompt_ordinal % 2 == 1 else ["KNEE", "EXACT"]
        for pair_position, point in enumerate(pair_order, 1):
            run_ordinal += 1
            plan.append({
                "run_ordinal": run_ordinal,
                "prompt_ordinal": prompt_ordinal,
                "pair_position": pair_position,
                "point": point,
                "case_id": selected["case_id"],
                "semantic_family": selected["semantic_family"],
                "length_level": selected["length_level"],
                "prompt_tokens": selected["actual_templated_prompt_tokens"],
                "selection_role": selected["selection_role"],
                "stage_a_s2_p50": stage_a_row,
                "output_name": (
                    f"run-{run_ordinal:03d}-{selected['case_id']}-{point.lower()}"
                ),
            })
    if run_ordinal != 48:
        raise AssertionError("Stage-C plan did not produce 48 runs")

    output_root = args.output_root.resolve()
    progress_path = args.progress.resolve()
    if output_root.exists() or progress_path.exists():
        raise ValueError("Stage-C outcome path exists before preregistration")
    output = {
        "schema_version": "phase13-6pg-stage-c-preregistration-v1",
        "status": "frozen",
        "provenance": "PREREGISTERED_BEFORE_STAGE_C_OUTCOMES",
        "execution_project_sha": args.execution_project_sha,
        "nested_llama_cpp_sha": EXPECTED_NESTED_SHA,
        "inputs": {
            "selection": selection_id,
            "stage_a_final_checkpoint": stage_a_id,
            "stage_b_capacity_handoff": handoff_id,
            "final_observer_cache_hygiene": hygiene_id,
            "corpus": corpus_id,
            "model_identity": model_id,
            "build_fingerprint": fingerprint_id,
            "checkpoint_b": {
                "url": args.checkpoint_b_url,
                "verdict": "PASS",
                "safe_to_proceed": True,
                "final_capable": False,
            },
        },
        "runtime": {
            "helper_binary": binary_id,
            "runner": runner_id,
            "controller": controller_id,
            "generator": generator_id,
            "model_first_shard": str(args.model.resolve(strict=True)),
            "build_type": "Release",
            "native_optimization": True,
        },
        "configuration": {
            "backend": "CPU-only Mode-P/BATCHED",
            "protocol": "full-prompt",
            "fresh_process_per_cell": True,
            "serial_execution": True,
            "threads": 32,
            "n_ctx": 768,
            "decode_forwards": 64,
            "cache_slots": EXPECTED_CACHE_SLOTS,
            "cache_bytes": EXPECTED_CACHE_BYTES,
            "exact": {
                "enabled": False,
                "candidate_count": 0,
                "max_swaps": 0,
                "max_score_regret": 0.0,
            },
            "knee": {
                "enabled": True,
                "candidate_count": 32,
                "max_swaps": 1,
                "max_score_regret": 0.0030885785818099976,
            },
            "pair_order": "EXACT,KNEE for odd frozen prompt ordinal; KNEE,EXACT for even",
            "retry_budget": 0,
        },
        "output": {
            "root": str(output_root),
            "progress": str(progress_path),
            "root_existed_at_freeze": False,
            "progress_existed_at_freeze": False,
        },
        "plan": plan,
        "outcome_inspection": {
            "stage_c_processes_started": 0,
            "stage_c_outcomes_inspected": 0,
            "policy_or_selection_retuning_after_outcomes": False,
        },
        "failure_policy": {
            "preserve_failed_attempt": True,
            "replacement_allowed": False,
            "retry_allowed": False,
            "more_than_four_cells_or_family_loss": "STOP_BEFORE_HEADLINE_SYNTHESIS_AND_RETURN_TO_DESIGN",
        },
        "authority": {
            "stage_a_s2_p50_reused_not_rerun": True,
            "free_generation_feedback_is_trajectory_only_not_quality": True,
            "offline_physical_anchor_validation_pending_exact_cells": True,
            "larger_capacity_curve_authoritative_before_validation": False,
        },
        "disposition": "READY_AFTER_PUBLICATION_FOR_VERIFY_ONLY_THEN_SERIAL_STAGE_C",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({"status": "pass", "output": identity(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from common import (
    CHECKPOINT_A_COMMENT,
    CHECKPOINT_A_LLAMA,
    CHECKPOINT_A_PROJECT,
    CHECKPOINT_B_COMMENT,
    CHECKPOINT_B_PROJECT,
    LLAMA_BASE,
    LLAMA_CANDIDATE,
    MAIN_POLICY,
    PHASE6_MANIFEST,
    PROJECT_BASE,
    environment,
    git,
    identity,
    write,
)

ARTIFACTS = [
    "scripts/phase7/common.py",
    "scripts/phase7/capture_runtime_matrix.py",
    "scripts/phase7/capture_validation.py",
    "scripts/phase7/build_phase7_manifest.py",
    "scripts/phase7/verify_phase7.py",
    "schemas/phase7/phase7-manifest-v1.schema.json",
    "tests/phase7/test_phase7_evidence.py",
    "results/2026-07-31/skynet/phase7-async-runtime/checkpoint-b-final-correction.json",
    "results/2026-07-31/skynet/phase7-async-runtime/checkpoint-b-placement-correction.json",
    "results/2026-07-31/skynet/phase7-async-runtime/runtime-matrix.json",
    "results/2026-07-31/skynet/phase7-async-runtime/validation-results.json",
    "results/2026-07-31/skynet/phase7-async-runtime/PHASE7.md",
    "PLAN.md",
    "docs/STATUS.md",
    "docs/plan/07-async-runtime.md",
    "docs/DECISIONS.md",
    "docs/MODELS_AND_VALIDATION.md",
    "docs/REPOSITORIES_AND_ARTIFACTS.md"
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--evidence-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    results = args.results_root.resolve()
    if git(root, "rev-parse", "HEAD") != args.evidence_head:
        raise RuntimeError("evidence head must be the current committed project head")
    if git(nested, "rev-parse", "HEAD") != LLAMA_CANDIDATE or git(root, "rev-parse", "HEAD:llama.cpp") != LLAMA_CANDIDATE:
        raise RuntimeError("accepted nested head/gitlink changed")

    checkpoint_final = json.loads((results / "checkpoint-b-final-correction.json").read_text())
    checkpoint_placement = json.loads((results / "checkpoint-b-placement-correction.json").read_text())
    runtime = json.loads((results / "runtime-matrix.json").read_text())
    validation = json.loads((results / "validation-results.json").read_text())
    if checkpoint_final.get("provider_post_h2d_cancellation", {}).get("status") != "PASS":
        raise RuntimeError("checkpoint cancellation evidence is not passing")
    if checkpoint_final.get("controlled_cross_flight_overlap", {}).get("status") != "PASS":
        raise RuntimeError("checkpoint overlap evidence is not passing")
    if checkpoint_placement.get("placement", {}).get("status") != "PASS":
        raise RuntimeError("checkpoint placement evidence is not passing")
    if runtime.get("status") != "pass" or validation.get("status") != "pass":
        raise RuntimeError("closeout evidence is not passing")

    cases = runtime["cases"]
    original_models = [case["model"] for case in cases if case["name"].endswith("_original")]
    runtime_identity = identity(root, results / "runtime-matrix.json")
    split_lineage = {
        representation: {
            "count": runtime["split_lineage"][representation]["count"],
            "source_model": runtime["split_lineage"][representation]["source_model"],
            "tool_head": runtime["split_lineage"][representation]["tool_head"],
            "mode": runtime["split_lineage"][representation]["mode"],
        }
        for representation in ("f16", "mxfp4")
    }
    split_lineage["runtime_evidence"] = runtime_identity
    overlap = checkpoint_final["controlled_cross_flight_overlap"]
    direct = runtime["direct_io"]["diagnostics"]
    representative = {case["name"]: {
        key: case["cold_twenty_step_a"].get(key)
        for key in (
            "prompt_tokens_per_second", "decode_tokens_per_second", "ttft_us", "token_p50_us", "token_p95_us",
            "token_p99_us", "storage_read_bytes", "io_bytes", "ring_h2d_bytes", "cold_hits", "cold_misses",
            "cold_evictions", "hot_hits", "hot_misses", "hot_admissions", "hot_evictions", "cold_actual_bytes",
            "ring_actual_bytes", "ring_pinned_bytes", "scheduler_flights", "io_peak_sq_occupancy", "io_peak_cq_occupancy",
        )
    } for case in cases}

    manifest = {
        "schema_version": "phase7-manifest-v1",
        "closeout_state": "final-review-candidate",
        "execution_profile": "STANDARD",
        "issue": {"repository": "murillo128/k3-out-of-core", "number": 24, "pull_request": 25},
        "revisions": {
            "branch": "codex/phase7-async-runtime",
            "project_execution_base": PROJECT_BASE,
            "project_checkpoint_b_head": CHECKPOINT_B_PROJECT,
            "project_evidence_head": args.evidence_head,
            "llama_cpp_base": LLAMA_BASE,
            "llama_cpp_candidate": LLAMA_CANDIDATE,
            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp"),
            "current_main_policy": MAIN_POLICY,
        },
        "checkpoint_a": {
            "comment_id": CHECKPOINT_A_COMMENT,
            "verdict": "PASS",
            "safety_to_proceed": "YES",
            "project_head": CHECKPOINT_A_PROJECT,
            "llama_cpp_head": CHECKPOINT_A_LLAMA,
            "independent_read_only": True,
        },
        "checkpoint_b": {
            "comment_id": CHECKPOINT_B_COMMENT,
            "verdict": "PASS",
            "safety_to_proceed": "YES",
            "project_head": CHECKPOINT_B_PROJECT,
            "llama_cpp_head": LLAMA_CANDIDATE,
            "independent_read_only": True,
        },
        "final_review": None,
        "inputs": {
            "phase6_manifest": identity(root, root / PHASE6_MANIFEST),
            "models": original_models,
            "split_lineage": split_lineage,
        },
        "environment": environment(root, results),
        "evidence": {
            "checkpoint_b_final_correction": identity(root, results / "checkpoint-b-final-correction.json"),
            "checkpoint_b_placement_correction": identity(root, results / "checkpoint-b-placement-correction.json"),
            "runtime_matrix": runtime_identity,
            "validation": identity(root, results / "validation-results.json"),
        },
        "validation": validation["commands"],
        "capabilities": {
            "linux_buffered_io_uring": True,
            "direct_io_requested": True,
            "direct_sources": direct.get("storage_direct_sources", 0),
            "direct_unsupported_sources": direct.get("storage_direct_unsupported", 0),
            "direct_operations": direct.get("io_direct_operations", 0),
            "buffered_fallback_operations": direct.get("io_buffered_fallback_operations", 0),
            "dedicated_transfer_backend": True,
            "native_events": True,
            "pageable_synchronous_fallback": True,
            "priority_classes": 4,
            "production_prefetch_enabled": False,
            "single_active_cached_request": True,
        },
        "metrics": {
            "runtime_cases": representative,
            "controlled_disk_h2d_overlap_us": overlap["union_overlap_us"],
            "controlled_disk_h2d_read_bytes": overlap["unique_read_bytes"],
            "controlled_disk_h2d_transfer_bytes": overlap["unique_h2d_bytes"],
            "controlled_h2d_compute_overlap_us": overlap["h2d_compute_overlap_us"],
            "controlled_h2d_compute_overlap_bytes": overlap["h2d_compute_overlap_bytes"],
            "controlled_h2d_compute_overlap_work": overlap["h2d_compute_overlap_work"],
            "production_overlap_observation": "zero on the deterministic tiny demand-only capture; positive overlap is established by the controlled native capture",
        },
        "gates": {
            "async_transport_and_fallback": True,
            "direct_io_opt_in_and_fallback": True,
            "single_flight_priority_and_saturation": True,
            "cancellation_retry_and_unload": True,
            "native_event_lifetime": True,
            "disk_h2d_overlap": True,
            "h2d_compute_overlap": True,
            "exact_original_split_parity": True,
            "repeated_warm_execution": True,
            "prior_modes_and_cached_placement": True,
            "bounded_resources_and_complete_traces": True,
            "tail_latency_and_resource_metrics": True,
            "sanitizers_and_thread_safety": True,
            "checkpoint_a_accepted": True,
            "checkpoint_b_accepted": True,
        },
        "deferred": [
            "Phase 8 CPU_FALLBACK and AUTO miss execution",
            "Phase 9 production cache-policy selection",
            "Phase 10 speculative prefetch",
            "multi-request concurrency, multi-GPU, UMA, and full-size Phase 14 conclusions",
        ],
        "carried_notes": [
            "Fixed ordinary-prompt tokenizer limitation remains visible.",
            "Phase 3 standing performance remains a raw 22/24 pass under D-018.",
            "The tiny fixtures validate mechanism and tails but are not full-size K3 performance evidence.",
            "Default-ASLR TSan may fail before test code with the documented runtime mapping limitation; the ASLR-disabled suite is authoritative.",
            "Production demand-only overlap was honestly zero; the controlled native trace establishes positive disk/H2D and H2D/compute overlap.",
        ],
        "artifacts": [identity(root, root / path) for path in ARTIFACTS],
    }
    schema = json.loads((root / "schemas/phase7/phase7-manifest-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    write(args.output, manifest)
    print("PASS: Phase 7 manifest written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

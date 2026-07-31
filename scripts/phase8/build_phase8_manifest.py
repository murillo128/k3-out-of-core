#!/usr/bin/env python3
"""Build the single authoritative Phase 8 closeout manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_B_COMMENT, CHECKPOINT_C_COMMENT,
                    LLAMA_BASE, LLAMA_CHECKPOINT_B, LLAMA_FINAL,
                    PHASE7_FINAL_REVIEW_COMMENT, PHASE7_MANIFEST, PHASE8_START,
                    PROJECT_BASE, environment, git, identity, write)

ARTIFACTS = [
    "scripts/phase8/common.py", "scripts/phase8/evaluate_auto_cost.py",
    "scripts/phase8/verify_checkpoint_b.py", "scripts/phase8/capture_miss_policy_parity.py",
    "scripts/phase8/capture_hybrid_overlap.py", "scripts/phase8/measure_miss_policies.py",
    "scripts/phase8/capture_validation.py", "scripts/phase8/build_phase8_manifest.py",
    "scripts/phase8/verify_phase8.py", "schemas/phase8/phase8-manifest-v1.schema.json",
    "tests/phase8/test_auto_evaluator.py", "tests/phase8/test_verify_checkpoint_b.py",
    "tests/phase8/test_phase8_evidence.py", "PLAN.md", "docs/STATUS.md",
    "docs/plan/07-async-runtime.md", "docs/DECISIONS.md", "docs/MODELS_AND_VALIDATION.md",
    "docs/REPOSITORIES_AND_ARTIFACTS.md",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--capture-head", required=True)
    parser.add_argument("--evidence-head", required=True)
    parser.add_argument("--public-source-revision", required=True)
    parser.add_argument("--public-config", type=Path, required=True)
    parser.add_argument("--public-tokenizer", type=Path, required=True)
    parser.add_argument("--public-gguf", type=Path, required=True)
    parser.add_argument("--public-conversion-command", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root, nested, results = args.project_root.resolve(), args.project_root.resolve() / "llama.cpp", args.results_root.resolve()
    if git(root, "rev-parse", "HEAD") != args.evidence_head:
        raise RuntimeError("evidence head must be the current committed project head")
    if subprocess.run(["git", "merge-base", "--is-ancestor", args.capture_head, args.evidence_head], cwd=root).returncode:
        raise RuntimeError("capture head must be an ancestor of the evidence head")
    if git(nested, "rev-parse", "HEAD") != LLAMA_FINAL or git(root, "rev-parse", "HEAD:llama.cpp") != LLAMA_FINAL:
        raise RuntimeError("accepted nested head/gitlink changed")
    evidence_names = ["checkpoint-b-probe.json", "miss-policy-parity.json", "hybrid-overlap.json",
                      "miss-policy-benchmarks.json", "synthetic-store.json", "validation-results.json"]
    evidence_values = {name.removesuffix(".json").replace("-", "_"): identity(root, results / name)
                       for name in evidence_names}
    parity = json.loads((results / "miss-policy-parity.json").read_text())
    overlap = json.loads((results / "hybrid-overlap.json").read_text())
    benchmarks = json.loads((results / "miss-policy-benchmarks.json").read_text())
    validation = json.loads((results / "validation-results.json").read_text())
    if any(value.get("status") != "pass" for value in (parity, overlap, benchmarks, validation)):
        raise RuntimeError("one or more closeout inputs are not passing")
    phase7 = root / PHASE7_MANIFEST
    manifest = {
        "schema_version": "phase8-manifest-v1", "closeout_state": "final-review-candidate",
        "execution_profile": "STANDARD",
        "issue": {"repository": "murillo128/k3-out-of-core", "number": 26, "pull_request": 27},
        "revisions": {"branch": "codex/phase8-miss-execution", "project_execution_base": PROJECT_BASE,
                      "project_phase8_4_start": PHASE8_START, "project_capture_head": args.capture_head,
                      "project_evidence_head": args.evidence_head,
                      "llama_cpp_base": LLAMA_BASE, "llama_cpp_final": LLAMA_FINAL,
                      "gitlink": git(root, "rev-parse", "HEAD:llama.cpp")},
        "checkpoint_a": {"comment_id": CHECKPOINT_A_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES",
                         "project_head": "07da45728b38b2d7c6a3a1b156dffcea6b94ec54",
                         "llama_cpp_head": "4cfee48aacb6b33ebcbda796b26106b69440e633", "independent_read_only": True},
        "checkpoint_b": {"comment_id": CHECKPOINT_B_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES",
                         "project_head": "30013880641fd2f10a1952b5b9619e6d872e233b",
                         "llama_cpp_head": LLAMA_CHECKPOINT_B, "independent_read_only": True},
        "checkpoint_c": {"comment_id": CHECKPOINT_C_COMMENT, "verdict": "PASS_WITH_NOTES",
                         "safety_to_proceed": "YES", "project_head": PHASE8_START,
                         "llama_cpp_head": LLAMA_FINAL, "independent_read_only": True},
        "final_review": None,
        "inputs": {"phase7_manifest": identity(root, phase7),
                   "phase7_final_review_comment": PHASE7_FINAL_REVIEW_COMMENT,
                   "k3_models": parity["cases"][:4],
                   "larger_public_moe": {"repository": "Qwen/Qwen1.5-MoE-A2.7B-Chat",
                       "source_revision": args.public_source_revision,
                       "previous_bootstrap_failure_comment": 5145455677,
                       "bootstrap_correction_comment": 5145774054,
                       "checkpoint_c_comment": CHECKPOINT_C_COMMENT,
                       "config": identity(root, args.public_config.resolve(), external=True),
                       "tokenizer": identity(root, args.public_tokenizer.resolve(), external=True),
                       "gguf": identity(root, args.public_gguf.resolve(), external=True),
                       "converter_head": LLAMA_CHECKPOINT_B, "runtime_head": LLAMA_FINAL,
                       "conversion_command": args.public_conversion_command},
                   "synthetic_store": evidence_values["synthetic_store"]},
        "environment": environment(root, results), "evidence": evidence_values,
        "validation": validation["commands"],
        "policies": {"default": "PROMOTE_AND_GPU", "cpu_fallback_explicit": True,
                     "auto_explicit_version": 1, "background_promotion_default": False,
                     "cost_model_digests_recorded": True},
        "metrics": {"parity_cases": len(parity["cases"]), "overlap_repetitions": len(overlap["samples"]),
                    "minimum_overlap_us": overlap["overlap_us"]["minimum"],
                    "benchmark_cells": len(benchmarks["matrix"]),
                    "peak_device_memory_used_mib": parity["resource_observation"]["peak_device_memory_used_mib"],
                    "minimum_device_memory_free_mib": parity["resource_observation"]["minimum_device_memory_free_mib"],
                    "controlled_regimes": benchmarks["controlled_regimes"],
                    "storage_read_ns": benchmarks["storage_read_ns"]},
        "gates": {"original_split_k3_numerical": True, "larger_public_moe": True,
                  "exact_size_synthetic_store": True, "mixed_cpu_gpu_overlap": True,
                  "auto_independent_evaluator": True, "cpu_and_gpu_favorable_regimes": True,
                  "background_bounded_and_truthful": True, "failure_and_unload_drain": True,
                  "default_and_prior_modes": True, "tails_and_resources": True,
                  "sanitizers_and_thread_safety": True, "checkpoint_a_accepted": True,
                  "checkpoint_b_accepted": True, "checkpoint_c_accepted": True,
                  "scope_lineage_and_whitespace": True},
        "carried_notes": [
            "Tiny K3 and sparse exact-size synthetic results are mechanism/crossover evidence, not full-model quality claims.",
            "VRAM is sampled device-wide telemetry and the OS page cache was not flushed.",
            "The accepted Phase 3 22/24 performance result remains unchanged.",
            "Default-ASLR TSan may fail before test code; the ASLR-disabled native suite is authoritative.",
            "Checkpoint C carried the unchanged callback-free fixture teardown race as non-material; corrected paths are clean.",
        ],
        "deferred": ["Phase 9 cache policy", "Phase 10 speculative prefetch", "Phase 12 multi-request fairness",
                     "multi-GPU, UMA, GDS, and full production K3 quality/performance"],
        "artifacts": [identity(root, root / path) for path in ARTIFACTS] +
                     [identity(root, results / "PHASE8.md")],
    }
    schema = json.loads((root / "schemas/phase8/phase8-manifest-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    write(args.output, manifest)
    print("PASS: Phase 8 manifest written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

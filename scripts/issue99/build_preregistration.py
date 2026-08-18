#!/usr/bin/env python3
"""Build and validate the outcome-blind Checkpoint-A preregistration for issue #99."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from protocol import (
    BRIDGE_CASES, BRIDGE_CHECKPOINTS, BROAD_CASES, BROAD_CHECKPOINTS,
    CANDIDATE_COUNT, CORPUS_PATH, EVIDENCE_ROOT, EXPERT_BUNDLE_BYTES,
    FROZEN_BINARY, ISSUE102_EVIDENCE_TARGET, ISSUE102_EXECUTION_CODE,
    ISSUE102_RELEASE, ISSUE102_RELEASE_SHA256, ISSUE105_ANALYSIS_CODE,
    ISSUE105_RELEASE, ISSUE105_RELEASE_SHA256, ISSUE105_ROOT, ISSUE105_TARGET,
    LOW_BRIDGE_CACHE_BYTES, LOW_BRIDGE_CACHE_SLOTS, MODEL_MANIFEST_SHA256,
    MODEL_PATH, MODEL_SOURCE, N_CTX, NESTED_BASELINE, POLICIES, PROFILE,
    PROJECT_BASELINE, QUALITY_MAX_ACTIVE_OUTPUT_BYTES, QUALITY_MAX_ROUTE_BYTES,
    QUALITY_MAX_TRACE_BYTES, QUALITY_OUTPUT_RESIDENCY_RESERVE_BYTES,
    QUALITY_OUTPUT_RESIDENCY_RESERVE_SLOTS, ROUTED_LAYERS, SELECTED_EXPERTS,
    TARGET_CACHE_BYTES, TARGET_CACHE_SLOTS, THREADS, atomic_json,
    expected_cell_count, file_identity,
)


CAPACITY_STABILITY_RESERVE_SLOTS = 64


SOURCE_FILES = (
    "scripts/issue99/CMakeLists.txt",
    "scripts/issue99/PROVENANCE.md",
    "scripts/issue99/analysis-requirements.txt",
    "scripts/issue99/analysis-requirements-lock.txt",
    "scripts/issue99/quality_probe.cpp",
    "scripts/issue99/protocol.py",
    "scripts/issue99/build_preregistration.py",
    "scripts/issue99/freeze_core_membership.py",
    "scripts/issue99/analyze_pair.py",
    "scripts/issue99/run_campaign.py",
    "scripts/issue99/qualify_instrumentation.py",
    "scripts/issue99/qualify_long_horizon_capacity.py",
    "scripts/issue99/sample_quality_capacity.py",
    "scripts/issue99/analyze_campaign.py",
    "scripts/issue99/reproduce_release.py",
    "tests/issue99/test_issue99_tools.py",
    "schemas/issue99/checkpoint-a-preregistration-v1.schema.json",
    "schemas/issue99/reference-sequence-v1.schema.json",
    "schemas/issue99/pair-summary-v1.schema.json",
    "schemas/issue99/output-cache-allowlist-v1.schema.json",
    "schemas/issue99/analysis-v1.schema.json",
    "schemas/issue99/reproduction-v1.schema.json",
)


def load(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def command(*items: str) -> str:
    return subprocess.run(items, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()


def host_snapshot() -> dict[str, Any]:
    meminfo = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        meminfo[key] = value.strip()
    cpu_model = next(
        line.split(":", 1)[1].strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name")
    )
    return {
        "hostname": platform.node(),
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu_model": cpu_model,
        "online_logical_cpus": os.cpu_count(),
        "mem_total": meminfo.get("MemTotal"),
        "swap_total": meminfo.get("SwapTotal"),
        "decision_host_mounts": [
            json.loads(command("findmnt", "-J", "-o", "TARGET,SOURCE,FSTYPE", "--target", target))
            for target in ("/mnt/nvme0", "/mnt/nvme1")
        ],
    }


def validate_qualification(root: Path, quality_root: Path, low_root: Path) -> dict[str, Any]:
    target = load(root / "high-target-sample-01" / "disposition.json")
    if target.get("status") != "admission_rejected" or target.get("requested_slots") != TARGET_CACHE_SLOTS:
        raise ValueError("the explicit high target was not cleanly rejected")
    auto_paths = sorted(quality_root.glob("auto-sample-*/disposition.json"))
    if len(auto_paths) != 3:
        raise ValueError("quality-helper qualification must contain exactly three AUTO samples")
    auto = [load(path) for path in auto_paths]
    if any(row.get("status") != "admitted_cleanly" or row.get("mode") != "AUTO" or
           row.get("changed_policy_outcomes_created") for row in auto):
        raise ValueError("quality-helper AUTO qualification did not produce three clean admissions")
    slots = [int(row["selected_slots"]) for row in auto]
    minimum_auto_slots = min(slots)
    total_reserve_slots = CAPACITY_STABILITY_RESERVE_SLOTS + QUALITY_OUTPUT_RESIDENCY_RESERVE_SLOTS
    if QUALITY_MAX_ACTIVE_OUTPUT_BYTES > QUALITY_OUTPUT_RESIDENCY_RESERVE_BYTES:
        raise ValueError("active output bound exceeds its registered residency reserve")
    if minimum_auto_slots <= total_reserve_slots:
        raise ValueError("AUTO capacity cannot cover the preregistered stability/output reserves")
    resolved_slots = minimum_auto_slots - total_reserve_slots
    if resolved_slots < LOW_BRIDGE_CACHE_SLOTS:
        raise ValueError("resolved capacity is below the fail-closed floor")
    if any(int(row["selected_bytes"]) != slots[index] * EXPERT_BUNDLE_BYTES
           for index, row in enumerate(auto)):
        raise ValueError("AUTO sample contains non-whole-expert capacity")
    low_paths = sorted(low_root.glob("low-bridge-explicit-*/disposition.json"))
    if len(low_paths) != 1:
        raise ValueError("quality-helper qualification must identify exactly one low bridge sample")
    low_path = low_paths[0]
    low = load(low_path)
    low_enabled = low.get("status") == "admitted_cleanly" and \
        low.get("selected_slots") == LOW_BRIDGE_CACHE_SLOTS and \
        low.get("selected_bytes") == LOW_BRIDGE_CACHE_BYTES and low.get("mode") == "EXPLICIT"
    identities = {}
    identities["generic-high-target-rejection"] = {
        "disposition": file_identity(root / "high-target-sample-01" / "disposition.json"),
        "envelope": file_identity(root / "high-target-sample-01" / "envelope.json"),
    }
    for disposition_path in (*auto_paths, low_path):
        directory = disposition_path.parent.name
        identities[directory] = {
            "disposition": file_identity(disposition_path),
            "envelope": file_identity(disposition_path.with_name("envelope.json")),
        }
    return {
        "target_attempt": "admission_rejected",
        "target_slots": TARGET_CACHE_SLOTS,
        "target_bytes": TARGET_CACHE_BYTES,
        "quality_helper_auto_selected_slots": slots,
        "minimum_auto_slots_before_stability_reserve": minimum_auto_slots,
        "stability_reserve_slots": CAPACITY_STABILITY_RESERVE_SLOTS,
        "stability_reserve_basis": "one additional >=1-GiB whole-expert reserve after the instantaneous AUTO ceiling failed the next fresh explicit admission",
        "active_output_residency_reserve_bytes": QUALITY_OUTPUT_RESIDENCY_RESERVE_BYTES,
        "active_output_residency_reserve_slots": QUALITY_OUTPUT_RESIDENCY_RESERVE_SLOTS,
        "active_output_residency_reserve_basis": "format-derived worst-1024-token trace and conservative 4-KiB-per-route-record bound, rounded up to a separate 6-GiB reserve after a 492-token exact capture crossed the runtime cgroup guard",
        "maximum_active_quality_trace_bytes": QUALITY_MAX_TRACE_BYTES,
        "maximum_active_route_stream_bytes": QUALITY_MAX_ROUTE_BYTES,
        "maximum_active_output_bytes": QUALITY_MAX_ACTIVE_OUTPUT_BYTES,
        "issue99_cache_slots": resolved_slots,
        "issue99_cache_bytes": resolved_slots * EXPERT_BUNDLE_BYTES,
        "capacity_relation": "LOWER_DUE_TO_CONTEXT_BUDGET",
        "minimum_floor_slots": LOW_BRIDGE_CACHE_SLOTS,
        "low_bridge_enabled": low_enabled,
        "low_bridge_slots": LOW_BRIDGE_CACHE_SLOTS,
        "low_bridge_bytes": LOW_BRIDGE_CACHE_BYTES,
        "low_bridge_disposition": "ADMITTED_CLEANLY" if low_enabled else "UNAVAILABLE_BY_ADMISSION",
        "performance_or_locality_inspected": False,
        "sample_artifacts": identities,
        "generic_probe_context_only": "three generic-probe AUTO samples selected 6841; superseded for execution by actual quality-helper overhead",
    }


def validate_corpus(path: Path) -> dict[str, Any]:
    corpus = load(path)
    cases = {row["id"]: row for row in corpus["cases"]}
    cases[corpus["sentinel"]["id"]] = corpus["sentinel"]
    requested = set(BROAD_CASES) | set(BRIDGE_CASES)
    if not requested <= set(cases):
        raise ValueError(f"frozen corpus is missing cases: {sorted(requested - set(cases))}")
    selected = {}
    for case_id in sorted(requested):
        row = cases[case_id]
        tokens = int(row["observed_templated_prompt_tokens"])
        horizon = 1024 if case_id in BRIDGE_CASES else 512
        if tokens + horizon > N_CTX:
            raise ValueError(f"{case_id}: prompt plus horizon exceeds n_ctx")
        selected[case_id] = {
            "semantic_family": row.get("semantic_family", "sentinel"),
            "length_level": row.get("length_level", 0),
            "templated_prompt_tokens": tokens,
        }
    return {"identity": file_identity(path), "selected_cases": selected}


def planned_cells(capacity: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    order = 0
    for case_id in BROAD_CASES:
        for policy, intervention in (
            ("EXACT", "FREE_TRAJECTORY"),
            ("KNEE", "DIRECT_FIXED_CONTEXT"),
            ("S2_P50", "DIRECT_FIXED_CONTEXT"),
        ):
            order += 1
            rows.append({"order": order, "cohort": "broad", "case_id": case_id, "policy": policy,
                         "intervention": intervention, "cache_regime": "high-cache", "horizon": 512})
    for case_id in BRIDGE_CASES:
        for policy, intervention in (
            ("EXACT", "FREE_TRAJECTORY"),
            ("KNEE", "DIRECT_FIXED_CONTEXT"),
            ("S2_P50", "DIRECT_FIXED_CONTEXT"),
            ("KNEE", "FREE_TRAJECTORY"),
            ("S2_P50", "FREE_TRAJECTORY"),
        ):
            order += 1
            rows.append({"order": order, "cohort": "bridge", "case_id": case_id, "policy": policy,
                         "intervention": intervention, "cache_regime": "high-cache", "horizon": 1024})
    if capacity["low_bridge_enabled"]:
        for case_id in BRIDGE_CASES:
            for policy in ("EXACT", "KNEE", "S2_P50"):
                order += 1
                rows.append({"order": order, "cohort": "low-bridge", "case_id": case_id, "policy": policy,
                             "intervention": "CAPACITY_FIXED_CONTEXT", "cache_regime": "96-gib-bridge",
                             "horizon": 512})
    if len(rows) != expected_cell_count(capacity["low_bridge_enabled"]):
        raise ValueError("planned cell count mismatch")
    return rows


def analysis_registration() -> dict[str, Any]:
    return {
        "outcome_blinding": "no changed-policy issue99 quality outcome exists or was inspected at freeze",
        "primary_metric": "DIRECT_FIXED_CONTEXT reference-token delta NLL versus EXACT",
        "checkpoint_estimand": "cumulative arithmetic mean delta NLL through each available checkpoint",
        "instantaneous_checkpoint_delta_nll": "secondary",
        "unavailable_checkpoint_rule": "record unavailable after EOG; never extrapolate or replace case",
        "policy_comparisons": ["KNEE_vs_EXACT", "S2_P50_vs_EXACT", "S2_P50_minus_KNEE_paired_by_prompt"],
        "trajectory_grid": {"broad": list(BROAD_CHECKPOINTS), "bridge": list(BRIDGE_CHECKPOINTS)},
        "trend_tests": {
            "domain": "available checkpoints 64 through 512; bridge feedback additionally through 1024",
            "linear": "prompt-fixed-effect OLS of cumulative mean delta NLL on log2(position)",
            "monotonic": "per-prompt Spearman rho summarized with prompt-cluster bootstrap",
            "bounded_breakpoints": {"broad_candidates": [128, 256], "bridge_candidates": [128, 256, 512]},
            "selection": "training-fold minimum squared error; compare held-out squared error with unbroken linear model",
            "claim_guard": "no universal quality-knee claim from two changed policies",
        },
        "predictor_models": {
            "target": "checkpoint cumulative mean delta NLL; KL/JS/hidden divergence secondary",
            "P0": ["cumulative_max_corrected_regret_per_swap", "cumulative_mean_corrected_regret_per_swap"],
            "P1": ["P0", "cumulative_corrected_regret"],
            "P2": ["P1", "cumulative_raw_regret_signed"],
            "P3": ["P2", "changed_slot_fraction", "perturbed_layer_fraction"],
            "P4": ["P3", "cumulative_regret_weighted_mean_normalized_depth",
                   "cumulative_first_perturbed_normalized_depth"],
            "fit": "training-fold standardization plus OLS Moore-Penrose pseudoinverse; no tuning",
            "split": "leave one prompt/semantic family out; the broad cohort has one frozen representative per family",
            "score": "pooled out-of-fold RMSE and R2, retaining all checkpoints for a held-out prompt as one cluster",
        },
        "uncertainty": {
            "bootstrap_repetitions": 10_000,
            "seed": 990_105,
            "unit": "prompt cluster with all policies/checkpoints retained",
            "interval": "two-sided percentile 95%",
            "added_signal": {
                "supported": "prior-minus-new held-out squared-error interval is strictly above zero",
                "weak": "point improvement is positive but interval includes zero",
                "no": "point improvement is zero/adverse",
                "inconclusive": "fewer than 8 valid broad prompts or rank/coverage failure",
            },
        },
        "feedback_increment": {
            "definition": "FREE_TRAJECTORY_EFFECT minus DIRECT_FIXED_CONTEXT_EFFECT",
            "scope": "token-mediated feedback increment under the controlled intervention",
            "metrics": ["hidden_relative_l2", "route_divergence", "cache_misses", "backing_loads",
                        "cumulative_corrected_regret", "cumulative_raw_regret_signed"],
            "generated_token_rule": "common-prefix/equality is trajectory evidence, never semantic-quality proof",
        },
        "depth": {
            "normalization": "routed-layer ordinal / 91 over sorted 92-layer decode set",
            "third": "min(2, floor(3 * ordinal / 92)) => early/middle/late",
            "P4_primary": True,
        },
        "core_effect_modifier": {
            "primary_gamma": 1.0,
            "mandatory_sensitivity_gamma": 0.8,
            "classes": ["core_to_core", "core_to_peripheral", "peripheral_to_core",
                        "peripheral_to_peripheral"],
            "sparse_rule": "do not post-hoc pool; fewer than 8 prompt clusters is inconclusive",
            "model": "P1 plus three nonreference transition fractions and their cumulative-regret interactions; peripheral_to_peripheral is reference",
            "evaluation": "same LOFO and prompt-cluster prior-minus-new squared-error rule as P0-P4",
        },
        "generation_phase": {
            "boundary_literal": "<|close|>think<|sep|><|open|>response<|sep|>",
            "rule": "tokenize exact literal; only an exact contiguous token match creates transition/final labels",
            "otherwise": "phase-specific analysis unavailable; no prose-semantic inference",
        },
        "systems_quality_joins": {
            "key": "immutable case_id and compatible protocol identity",
            "measured_views": ["S2 load reduction/hit improvement", "S2 TPS gain"],
            "derived_views": ["physical-reference amplification lower endpoint with upper sensitivity",
                              "physical-reference saving lower endpoint with upper sensitivity",
                              "fixed-route interval endpoints as separate evidence class"],
            "association": "Spearman effect with prompt-cluster bootstrap; endpoints must agree for a directional label",
            "ratio_guard": "no primary quality-per-TPS ratio",
        },
        "classification_guards": {
            "association_positive": "both mandatory endpoint point estimates positive and 95% intervals exclude zero",
            "association_inverse": "both mandatory endpoint point estimates negative and 95% intervals exclude zero",
            "association_heterogeneous": "endpoint or registered subgroup directions conflict materially",
            "association_no_clear": "valid coverage but intervals include zero without registered heterogeneity",
            "s2_acceptable_yes": "S2-vs-KNEE paired damage upper CI <= 0 and S2-vs-EXACT damage upper CI <= 0",
            "s2_acceptable_no": "both corresponding lower CIs are > 0",
            "s2_acceptable_otherwise": "inconclusive",
        },
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    qualification = validate_qualification(
        args.qualification_root, args.quality_qualification_root,
        args.low_bridge_qualification_root or args.quality_qualification_root)
    long_horizon = load(args.long_horizon_qualification)
    if long_horizon.get("schema_version") != "issue99-long-horizon-capacity-qualification-v1" or \
            long_horizon.get("status") != "pass" or long_horizon.get("changed_policy_outcomes_created") or \
            long_horizon.get("capacity_bytes") != qualification["issue99_cache_bytes"] or \
            long_horizon.get("horizon") != 1024 or long_horizon.get("fresh_processes", 0) < 3 or \
            long_horizon.get("bounds", {}).get("maximum_active_output_bytes") != QUALITY_MAX_ACTIVE_OUTPUT_BYTES:
        raise ValueError("long-horizon capacity qualification is absent, outcome-bearing, or mismatched")
    qualification["long_horizon_qualification"] = file_identity(args.long_horizon_qualification)
    qualification["long_horizon_qualification_processes"] = long_horizon["fresh_processes"]
    instrumentation = load(args.instrumentation_qualification)
    if instrumentation.get("schema_version") != "issue99-checkpoint-a-instrumentation-qualification-v1" or \
            instrumentation.get("status") != "pass" or instrumentation.get("changed_policy_outcomes_created") or \
            instrumentation.get("capacity_bytes") != qualification["issue99_cache_bytes"]:
        raise ValueError("instrumentation qualification is absent, changed-policy-bearing, or capacity-mismatched")
    core = load(args.core_membership)
    if core.get("schema_version") != "issue99-frozen-core-membership-v1" or core.get("status") != "pass":
        raise ValueError("core membership is not frozen")
    corpus = validate_corpus(CORPUS_PATH)
    sources = {path: file_identity(Path(path)) for path in SOURCE_FILES}
    return {
        "schema_version": "issue99-checkpoint-a-preregistration-v1",
        "status": "frozen-outcome-blind",
        "issue": 99,
        "profile": PROFILE,
        "authority": "issue #99 body plus latest additive/superseding design-authority amendment",
        "baseline": {"project_main": PROJECT_BASELINE, "nested_llama_cpp": NESTED_BASELINE,
                     "implementation_parent": args.implementation_parent},
        "host": host_snapshot(),
        "runtime": {
            "n_ctx": N_CTX, "threads": THREADS, "n_batch": 1, "n_ubatch": 1,
            "backend": "CPU", "n_gpu_layers": 0, "load_mode": "DIRECT_IO",
            "runtime_mode": "PERFORMANCE", "issue_mode": "BATCHED",
            "full_prompt_prefill_policy": "EXACT", "changed_routing_boundary": "decode only",
            "binary": file_identity(FROZEN_BINARY),
            "build_fingerprint": file_identity(Path("/mnt/nvme1/issue98/identity/build-fingerprint.json")),
            "helper_cmake_cache": file_identity(Path("/mnt/nvme1/issue99/build/CMakeCache.txt")),
        },
        "model": {"source": MODEL_SOURCE, "manifest_sha256": MODEL_MANIFEST_SHA256,
                  "first_shard": file_identity(MODEL_PATH, hash_payload=False), "shards": 33},
        "corpus": corpus,
        "capacity": qualification,
        "policies": POLICIES,
        "cohorts": {"broad": list(BROAD_CASES), "bridge": list(BRIDGE_CASES)},
        "cells": planned_cells(qualification),
        "repeat_count": 1,
        "execution_order": "listed cell order; fresh process per cell; no parallel K3 cells",
        "instrumentation": {
            "routed_layers": ROUTED_LAYERS, "selected_experts": SELECTED_EXPERTS,
            "candidate_count": CANDIDATE_COUNT,
            "tensor_density": "MoE output + routed-layer hidden state for every decode token/layer; logits every token",
            "route_density": "top-16 IDs/weights and top-32 IDs/corrected scores/raw probabilities every token/layer",
            "raw_tensor_retention": "ephemeral; immediate paired scalarization; at most 2 live files",
            "persistent_datasets": ["tokens", "layers", "routes", "substitution_events"],
            "default_off": True,
            "qualification": file_identity(args.instrumentation_qualification),
            "qualification_assertions": instrumentation["assertions"],
            "qualification_processes": instrumentation["processes"],
            "qualification_horizon": instrumentation["horizon"],
        },
        "core_membership": file_identity(args.core_membership),
        "imported_authorities": {
            "issue102": {"release": ISSUE102_RELEASE, "release_sha256": ISSUE102_RELEASE_SHA256,
                         "evidence_target": ISSUE102_EVIDENCE_TARGET, "execution_code": ISSUE102_EXECUTION_CODE},
            "issue105": {"release": ISSUE105_RELEASE, "release_sha256": ISSUE105_RELEASE_SHA256,
                         "final_reviewed_target": ISSUE105_TARGET, "analysis_code": ISSUE105_ANALYSIS_CODE,
                         "virtual_capacity": file_identity(ISSUE105_ROOT / "analysis/virtual-cache-capacity.csv"),
                         "physical_runs": file_identity(ISSUE105_ROOT / "tables/physical_runs.csv")},
        },
        "analysis": analysis_registration(),
        "evidence_hygiene": {
            "allowed": "targeted POSIX_FADV_DONTNEED on exact issue99-generated closed validated hashed outputs",
            "allowlist_fields": ["canonical_path", "device", "inode", "size_bytes", "sha256", "role",
                                 "why_not_next_k3_input", "resident_bytes_before", "resident_bytes_after"],
            "forbidden": ["global drop_caches", "model/corpus/reference/executable/library advice", "swap tricks",
                          "cgroup/sysctl changes", "pressure allocation", "admission bypass"],
        },
        "resource_limits": {
            "maximum_k3_processes": expected_cell_count(qualification["low_bridge_enabled"]),
            "maximum_live_raw_tensor_files": 2,
            "maximum_live_raw_tensor_bytes": 16 * 1024**3,
            "maximum_persistent_issue99_evidence_bytes": 500 * 1024**3,
            "stop_before_limit": True,
        },
        "failure_policy": {
            "no_retry_until_green": True,
            "hard_failures": ["NaN/Inf", "invalid expert ID", "stale generation", "missing projection/scale",
                              "cache metadata/content disagreement", "use-after-free", "async-order nondeterminism",
                              "unexplained tokenization/EOS change", "unbounded memory growth",
                              "observer/route/tensor coverage mismatch"],
            "bridge_102_parity": "not required because capacity relation is lower due to n_ctx budget",
            "capacity": "fixed explicit bytes in every decision cell; AUTO forbidden",
        },
        "source_files": sources,
        "outcome_inspection": {"changed_policy_issue99_outcomes_present": False, "inspected": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qualification-root", type=Path, default=EVIDENCE_ROOT / "qualification")
    parser.add_argument("--quality-qualification-root", type=Path,
                        default=EVIDENCE_ROOT / "quality-capacity-qualification")
    parser.add_argument("--low-bridge-qualification-root", type=Path)
    parser.add_argument("--core-membership", type=Path, required=True)
    parser.add_argument("--instrumentation-qualification", type=Path, required=True)
    parser.add_argument("--long-horizon-qualification", type=Path, required=True)
    parser.add_argument("--implementation-parent", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = build(args)
    atomic_json(args.output, value)
    print(f"ISSUE99_PREREG status=frozen cells={len(value['cells'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Strict deterministic verifier for the issue #13 Phase 3 manifest."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from common import LLAMA_BASE, MODELS, PROJECT_BASE, PUBLISHED_CORPUS, PUBLISHED_GGUF, git, sha256
from phase3_disposition import (
    CAPTURE_RELATIVE,
    CAPTURE_SHA256,
    DESIGN_AUTHORITY_COMMENT_IDS,
    LLAMA_CPP_CANDIDATE,
    validate_disposition,
)


ALLOWED_NESTED_PATHS = {
    "include/llama.h", "src/CMakeLists.txt", "src/llama-context.cpp", "src/llama-context.h",
    "src/llama-expert-weight-provider.cpp", "src/llama-expert-weight-provider.h",
    "src/llama-graph.cpp", "src/llama-graph.h", "src/llama-model.cpp", "src/llama-model.h",
    "src/llama.cpp", "tests/CMakeLists.txt", "tests/test-expert-weight-provider.cpp",
}
ALLOWED_PROJECT_PREFIXES = (
    "PLAN.md", "docs/", "results/2026-07-29/skynet/phase3-resident-provider/",
    "schemas/phase3/", "scripts/phase2/capture_route_observer.py",
    "scripts/phase2/overhead_probe.cpp", "scripts/phase2/route_probe.cpp",
    "scripts/phase3/", "tests/phase3/",
)
REQUIRED_PERFORMANCE_TELEMETRY = (
    "load_seconds", "context_create_seconds", "token_latency_p50_seconds", "token_latency_p95_seconds",
    "token_latency_p99_seconds", "peak_rss_kib", "gpu_memory_bytes", "graphs_reused",
)
PROVIDER_COUNTERS = (
    "provider_objects", "provider_bind_calls", "provider_prepare_calls",
    "provider_handles_acquired", "provider_handles_released", "provider_allocations",
    "provider_callbacks", "provider_tensor_copies", "provider_synchronizations",
    "provider_bundle_registrations", "provider_bundle_full_validations",
    "provider_bundle_fast_path_hits",
)
GATED_PERFORMANCE_METRICS = (
    "decode_tokens_per_second", "prompt_tokens_per_second", "ttft_seconds",
)
FINAL_CAPTURE_RULE = "single-complete-post-optimization-capture-v2"
FINAL_CAPTURE_APPROVAL_COMMENT_ID = 5127774849
FINAL_CAPTURE_NAME = "provider-overhead-post-optimization.json"
HISTORICAL_CAPTURE_NAME = "provider-overhead.json"
HISTORICAL_CAPTURE_SHA256 = "df0fa1f05c6a57838e54f9b9da7a8d66f6ef826adf83fe3c19ccf771d04540a5"
V2_CANDIDATE_STATE = "checkpoint-b-candidate-with-performance-notes"
V2_COMPLETE_STATE = "complete-with-performance-notes"
ATTESTATION_ONLY_PATHS = {
    "results/2026-07-29/skynet/phase3-resident-provider/checkpoint-b-review.json",
    "results/2026-07-29/skynet/phase3-resident-provider/phase3-manifest.json",
    "results/2026-07-29/skynet/phase3-resident-provider/verification-result.json",
}


def validate_reviewed_project_head(root: Path, project_head: str, errors: list[str]) -> None:
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", project_head, "HEAD"], cwd=root, check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode != 0:
        errors.append("Checkpoint B attestation is for a different project head")
        return
    post_review_paths = set(git(root, "diff", "--name-only", f"{project_head}..HEAD").splitlines())
    if post_review_paths - ATTESTATION_ONLY_PATHS:
        errors.append("project changes after the reviewed Checkpoint B head are not attestation-only")


def validate_performance_sample(sample: dict[str, Any], errors: list[str]) -> None:
    metric = sample.get("metric", {})
    missing = [name for name in REQUIRED_PERFORMANCE_TELEMETRY if name not in metric]
    if missing:
        errors.append(f"performance sample omits required telemetry: {missing}")
    for name in REQUIRED_PERFORMANCE_TELEMETRY:
        if name in metric and not isinstance(metric[name], (int, float)):
            errors.append(f"performance telemetry is not numeric: {name}")
    availability = sample.get("provider_counter_availability")
    if sample.get("label") == "isolated-baseline":
        if availability != "unavailable-pinned-baseline":
            errors.append("isolated baseline provider-counter availability is not explicit")
        if any(name not in metric or metric[name] is not None for name in PROVIDER_COUNTERS):
            errors.append("isolated baseline provider counters are not explicit null values")
    else:
        if availability != "reported":
            errors.append("candidate provider counters are not marked reported")
        if any(not isinstance(metric.get(name), (int, float)) for name in PROVIDER_COUNTERS):
            errors.append("candidate provider counters are missing or non-numeric")


def validate_final_capture_contract(overhead: dict[str, Any], errors: list[str]) -> None:
    expected = {
        "rule": FINAL_CAPTURE_RULE,
        "approval_comment_id": FINAL_CAPTURE_APPROVAL_COMMENT_ID,
        "complete_capture_count": 1,
        "retry_or_cross_attempt_selection": "forbidden",
        "result_stands": True,
        "artifact": FINAL_CAPTURE_NAME,
        "historical_capture": {
            "artifact": HISTORICAL_CAPTURE_NAME,
            "sha256": HISTORICAL_CAPTURE_SHA256,
            "disposition": "immutable-non-authoritative-history",
        },
    }
    if overhead.get("validation_contract") != expected:
        errors.append("provider overhead does not bind the approved standing final-capture contract")
    if "composition" in overhead:
        errors.append("provider overhead contains forbidden cross-attempt composition")


def validate_checkpoint_b(root: Path, manifest: dict[str, Any], errors: list[str]) -> bool:
    result_root = root / "results/2026-07-29/skynet/phase3-resident-provider"
    path = result_root / "checkpoint-b-review.json"
    if not path.is_file():
        if manifest.get("closeout_state") == V2_COMPLETE_STATE:
            errors.append("complete-with-performance-notes manifest omits Checkpoint B attestation")
        return False
    try:
        checkpoint = json.loads(path.read_text())
        schema = json.loads((root / "schemas/phase3/checkpoint-b-review-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(checkpoint)
    except Exception as error:
        errors.append(f"Checkpoint B attestation schema validation failed: {error}")
        return False

    project_head = checkpoint.get("project_head", "")
    expected = {
        "repository": "murillo128/k3-out-of-core",
        "issue": 13,
        "execution_profile": "STANDARD",
        "checkpoint": "B",
        "llama_cpp_head": LLAMA_CPP_CANDIDATE,
        "project_range": f"{PROJECT_BASE}..{project_head}",
        "llama_cpp_range": f"{LLAMA_BASE}..{LLAMA_CPP_CANDIDATE}",
        "independent_read_only": True,
    }
    for name, value in expected.items():
        if checkpoint.get(name) != value:
            errors.append(f"Checkpoint B attestation differs: {name}")
    if checkpoint.get("verdict") not in {"PASS", "PASS_WITH_NOTES"}:
        errors.append("Checkpoint B verdict is not accepted")
    if checkpoint.get("safety_to_proceed") != "YES":
        errors.append("Checkpoint B safety_to_proceed is not YES")
    expected_url = (
        "https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-"
        f"{checkpoint.get('comment_id')}"
    )
    if checkpoint.get("url") != expected_url:
        errors.append("Checkpoint B comment URL does not bind its comment ID")
    validate_reviewed_project_head(root, project_head, errors)
    manifest_review = next(
        (review for review in manifest.get("reviews", []) if review.get("checkpoint") == "B"), None
    )
    if manifest_review != checkpoint:
        errors.append("manifest Checkpoint B review does not exactly match its structured attestation")
    if manifest.get("revisions", {}).get("project_checkpoint_b_head") != project_head:
        errors.append("manifest Checkpoint B revision differs from its structured attestation")
    return not any(error.startswith("Checkpoint B") or "reviewed Checkpoint B" in error for error in errors)


def validate_evidence(root: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    result_root = root / "results/2026-07-29/skynet/phase3-resident-provider"
    try:
        parity = json.loads((result_root / "provider-parity-post-optimization.json").read_text())
        lifecycle = json.loads((result_root / "lifecycle-and-failures-post-optimization.json").read_text())
        administration = json.loads((result_root / "provider-admin-fast-path.json").read_text())
        prerequisites = json.loads((result_root / "corrective-prerequisites.json").read_text())
        overhead = json.loads((result_root / FINAL_CAPTURE_NAME).read_text())
        disposition = json.loads((result_root / "phase3-disposition.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"Phase 3 evidence cannot be loaded: {error}")
        return

    if parity.get("status") != "pass" or len(parity.get("cases", [])) != 4:
        errors.append("provider parity is not a four-combination pass")
    for case in parity.get("cases", []):
        if not all(case.get("checks", {}).values()):
            errors.append(f"provider parity check failed: {case.get('artifact')}/{case.get('backend')}")
        disabled = case.get("provider_stats", {}).get("disabled", {})
        resident = case.get("provider_stats", {}).get("resident", {})
        if any(disabled.get(name) != 0 for name in (
            "objects", "bind_calls", "prepare_calls", "handles_acquired", "handles_released",
            "allocations", "callbacks", "tensor_copies", "synchronizations", "failures", "cancellations",
        )):
            errors.append("disabled provider counters are not structurally zero")
        if resident.get("handles_acquired") != resident.get("handles_released"):
            errors.append("resident handles are not balanced")
        if resident.get("handles_acquired") != resident.get("prepare_calls"):
            errors.append("resident lease count is not exactly one per nonempty ubatch")
        if resident.get("bundle_registrations") != resident.get("bundle_full_validations") or resident.get("bundle_registrations") != 7:
            errors.append("resident bundle registry did not validate each routed layer exactly once")
        if resident.get("bundle_fast_path_hits", 0) <= 0:
            errors.append("resident bundle fast path was not observed")

    coverage = lifecycle.get("coverage", {})
    boolean_coverage = [value for value in coverage.values() if isinstance(value, bool)]
    if lifecycle.get("status") != "pass" or not all(boolean_coverage):
        errors.append("lifecycle/failure evidence is incomplete")
    if coverage.get("cpu_model_load_decode_unload_cycles", 0) < 20:
        errors.append("CPU load stress is below 20 cycles")
    if coverage.get("cuda_model_load_decode_unload_cycles", 0) < 10:
        errors.append("CUDA load stress is below 10 cycles")
    if administration.get("status") != "pass" or not all(administration.get("checks", {}).values()):
        errors.append("resident administrative diagnostic is incomplete")
    if prerequisites.get("status") != "pass" or not all(prerequisites.get("checks", {}).values()):
        errors.append("corrective prerequisite attestation is incomplete")
    historical = result_root / HISTORICAL_CAPTURE_NAME
    if not historical.is_file() or sha256(historical) != HISTORICAL_CAPTURE_SHA256:
        errors.append("historical provider overhead evidence identity differs")

    combinations = overhead.get("combinations", [])
    validate_final_capture_contract(overhead, errors)
    if len(combinations) != 4:
        errors.append("provider overhead does not contain four combinations")
    derived_combination_results = []
    for combination in combinations:
        comparisons = combination.get("comparisons", [])
        if len(comparisons) != 2:
            errors.append(f"overhead comparison count differs: {combination.get('artifact')}/{combination.get('backend')}")
        derived_comparison_results = []
        for comparison in comparisons:
            if len(comparison.get("runs", [])) != 20 or comparison.get("order") != list(("a", "b", "b", "a")*5):
                errors.append("overhead ABBA raw sample protocol differs")
            analyses = comparison.get("analysis", {})
            if set(analyses) != set(GATED_PERFORMANCE_METRICS):
                errors.append("overhead gated metric set differs")
            for analysis in analyses.values():
                if len(analysis.get("pairs", [])) != 10 or analysis.get("degrees_of_freedom") != 9:
                    errors.append("overhead pairing/confidence metadata differs")
                calculated_pass = analysis.get("one_sided_95_percent_upper_bound", 1) <= analysis.get("fixed_budget", 0)
                if analysis.get("passed") != calculated_pass:
                    errors.append("overhead confidence-gate result is inconsistent")
            derived_comparison_pass = all(
                analysis.get("passed") is True for analysis in analyses.values()
            ) and set(analyses) == set(GATED_PERFORMANCE_METRICS)
            if comparison.get("passed") != derived_comparison_pass:
                errors.append("overhead comparison result is inconsistent")
            derived_comparison_results.append(derived_comparison_pass)
            for sample in comparison.get("warmups", []) + comparison.get("runs", []):
                validate_performance_sample(sample, errors)
            reported = comparison.get("reported_non_gated_means", {})
            if set(reported) != set(REQUIRED_PERFORMANCE_TELEMETRY):
                errors.append("non-gated performance summary omits required telemetry")
            elif any(set(values) != {"a", "b"} for values in reported.values()):
                errors.append("non-gated performance summary omits a comparison side")
        derived_combination_pass = all(derived_comparison_results) and len(derived_comparison_results) == 2
        if combination.get("passed") != derived_combination_pass:
            errors.append("overhead combination result is inconsistent")
        derived_combination_results.append(derived_combination_pass)

    derived_status = "pass" if all(derived_combination_results) and len(derived_combination_results) == 4 else "fail"
    if overhead.get("status") != derived_status:
        errors.append("provider overhead standing status is inconsistent")
    validation = next(
        (item for item in manifest.get("validation", []) if item.get("name") == "provider-overhead-post-optimization"), {}
    )
    if validation.get("status") != derived_status:
        errors.append("manifest provider-overhead status differs from standing capture")
    if manifest.get("schema_version") == "phase3-manifest-v1":
        expected_state = "checkpoint-b-candidate" if derived_status == "pass" else "performance-gate-failed"
        allowed_states = {expected_state, "complete"} if derived_status == "pass" else {expected_state}
        if manifest.get("closeout_state") not in allowed_states:
            errors.append("manifest closeout state differs from standing performance result")
        return

    validate_disposition(root, disposition, errors)
    try:
        disposition_schema = json.loads((root / "schemas/phase3/phase3-disposition-v1.schema.json").read_text())
        Draft202012Validator.check_schema(disposition_schema)
        Draft202012Validator(disposition_schema).validate(disposition)
    except Exception as error:
        errors.append(f"Phase 3 disposition schema validation failed: {error}")
    raw_gate = manifest.get("raw_performance_gate", {})
    if raw_gate != {
        "status": "fail", "passed_cells": 22, "total_cells": 24,
        "capture_path": CAPTURE_RELATIVE, "capture_sha256": CAPTURE_SHA256,
    }:
        errors.append("manifest raw performance gate differs from the immutable 22/24 failure")
    disposition_path = result_root / "phase3-disposition.json"
    expected_disposition_identity = {
        "status": "accepted-with-notes", "progression_scope": "phase3-only",
        "path": str(disposition_path.relative_to(root)), "size": disposition_path.stat().st_size,
        "sha256": sha256(disposition_path), "comment_ids": DESIGN_AUTHORITY_COMMENT_IDS,
    }
    if manifest.get("design_disposition") != expected_disposition_identity:
        errors.append("manifest design disposition identity differs")
    if derived_status != "fail":
        errors.append("accepted-with-notes representation relabels the raw capture as passing")
    if manifest.get("closeout_state") not in {V2_CANDIDATE_STATE, V2_COMPLETE_STATE}:
        errors.append("v2 manifest has an invalid accepted-with-notes closeout state")

def validate_git(root: Path, manifest: dict[str, Any], strict: bool, output: Path, errors: list[str]) -> None:
    nested = root / "llama.cpp"
    revisions = manifest.get("revisions", {})
    candidate = revisions.get("llama_cpp_candidate", "")
    if git(nested, "rev-parse", "HEAD") != candidate:
        errors.append("nested llama.cpp head differs from manifest")
    if subprocess.run(["git", "merge-base", "--is-ancestor", PROJECT_BASE, "HEAD"], cwd=root, check=False).returncode != 0:
        errors.append("project head does not descend from immutable execution base")
    evidence_head = revisions.get("project_evidence_head", "")
    if subprocess.run(["git", "merge-base", "--is-ancestor", evidence_head, "HEAD"], cwd=root, check=False).returncode != 0:
        errors.append("manifest evidence head is not an ancestor of current project head")

    nested_paths = set(git(nested, "diff", "--name-only", f"{LLAMA_BASE}..{candidate}").splitlines())
    unexpected_nested = nested_paths - ALLOWED_NESTED_PATHS
    if unexpected_nested:
        errors.append(f"nested changes exceed issue scope: {sorted(unexpected_nested)}")
    if any(path.startswith(("ggml/", "src/ggml")) for path in nested_paths):
        errors.append("provider work reached a GGML backend/kernel path")

    changed = git(root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD").splitlines()
    for relative in changed:
        if relative == "llama.cpp":
            continue
        if not any(relative == prefix or relative.startswith(prefix) for prefix in ALLOWED_PROJECT_PREFIXES):
            errors.append(f"project change exceeds issue scope: {relative}")
    protected = git(
        root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD", "--",
        "results/2026-07-29/skynet/phase2-observability", "schemas/phase2", "tests/fixtures/phase2",
    )
    if protected:
        errors.append(f"immutable Phase 2 evidence changed: {protected.splitlines()}")

    for repository in (root, nested):
        whitespace = subprocess.run(
            ["git", "diff", "--check", f"{PROJECT_BASE if repository == root else LLAMA_BASE}..HEAD"],
            cwd=repository, text=True, capture_output=True, check=False,
        )
        if whitespace.returncode != 0:
            errors.append(f"whitespace check failed in {repository.name}: {whitespace.stdout}")
    if strict:
        project_status = git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
        output_relative = str(output.relative_to(root)) if output.is_relative_to(root) else None
        allowed_dirty = {output_relative} if output_relative else set()
        if manifest.get("schema_version") == "phase3-manifest-v2":
            allowed_dirty.update(artifact.get("path") for artifact in manifest.get("artifacts", []))
            allowed_dirty.add(str((root / "results/2026-07-29/skynet/phase3-resident-provider/phase3-manifest.json").relative_to(root)))
        def status_path(line: str) -> str:
            return line[3:] if len(line) > 2 and line[2] == " " else line[2:]
        project_status = [line for line in project_status if status_path(line) not in allowed_dirty]
        if project_status:
            errors.append(f"project worktree is not clean: {project_status}")
        if git(nested, "status", "--porcelain", "--untracked-files=all"):
            errors.append("nested llama.cpp worktree is not clean")

    plan = (root / "docs/plan/00-foundation.md").read_text()
    phase3 = plan.split("## Phase 3", 1)[1].split("## Phase 4", 1)[0]
    unchecked = [line for line in phase3.splitlines() if line.startswith("- [ ]")]
    failed_gate = "- [ ] Resident-provider performance regression is within the predeclared noise budget."
    if manifest.get("schema_version") == "phase3-manifest-v2":
        if unchecked != [failed_gate]:
            errors.append("Phase 3 plan must preserve the original failed 24-cell performance gate")
    elif manifest.get("closeout_state") == "performance-gate-failed":
        if unchecked != [failed_gate]:
            errors.append("Phase 3 plan does not identify only the standing failed performance gate")
    elif unchecked:
        errors.append("Phase 3 foundation plan still contains unchecked tasks")
    reviews = {review["checkpoint"] for review in manifest.get("reviews", [])}
    if "A" not in reviews:
        errors.append("accepted Checkpoint A is not bound")
    if manifest.get("closeout_state") in {"complete", V2_COMPLETE_STATE} and "B" not in reviews:
        errors.append("complete manifest does not bind accepted Checkpoint B")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest_path = args.manifest.resolve()
    output = args.output.resolve() if args.output else manifest_path.parent / "verification-result.json"
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text())
        schema_name = {
            "phase3-manifest-v1": "phase3-manifest-v1.schema.json",
            "phase3-manifest-v2": "phase3-manifest-v2.schema.json",
        }.get(manifest.get("schema_version"))
        if schema_name is None:
            raise ValueError("unsupported Phase 3 manifest schema version")
        schema = json.loads((root / "schemas/phase3" / schema_name).read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(manifest)
    except Exception as error:
        manifest = {}
        errors.append(f"manifest schema validation failed: {error}")

    expected_revisions = {
        "project_execution_base": PROJECT_BASE, "llama_cpp_base": LLAMA_BASE,
        "published_gguf": PUBLISHED_GGUF, "published_corpus": PUBLISHED_CORPUS,
    }
    for name, expected in expected_revisions.items():
        if manifest.get("revisions", {}).get(name) != expected:
            errors.append(f"revision differs: {name}")

    paths = []
    for artifact in manifest.get("artifacts", []):
        path = root / artifact.get("path", "")
        paths.append(artifact.get("path"))
        if not path.is_file() or path.stat().st_size != artifact.get("size") or sha256(path) != artifact.get("sha256"):
            errors.append(f"manifest artifact identity differs: {artifact.get('path')}")
    if len(paths) != len(set(paths)):
        errors.append("manifest artifact paths are not unique")

    phase2 = manifest.get("phase2_input", {})
    phase2_path = root / phase2.get("path", "")
    if not phase2_path.is_file() or phase2_path.stat().st_size != phase2.get("size") or sha256(phase2_path) != phase2.get("sha256"):
        errors.append("Phase 2 input manifest identity differs")
    for model in manifest.get("models", []):
        path = args.models_dir.resolve() / model.get("name", "")
        if not path.is_file() or path.stat().st_size != model.get("size") or sha256(path) != model.get("sha256"):
            errors.append(f"model identity differs: {model.get('name')}")

    validate_evidence(root, manifest, errors)
    validate_git(root, manifest, args.strict, output, errors)
    is_v2 = manifest.get("schema_version") == "phase3-manifest-v2"
    checkpoint_b_bound = validate_checkpoint_b(root, manifest, errors) if is_v2 else False
    checkpoint_b_eligible = is_v2 and not errors
    closeout_eligible = (
        checkpoint_b_eligible and checkpoint_b_bound and manifest.get("closeout_state") == V2_COMPLETE_STATE
    ) if is_v2 else manifest.get("closeout_state") in {"checkpoint-b-candidate", "complete"}
    result = {
        "schema_version": "phase3-verification-result-v2" if is_v2 else "phase3-verification-result-v1",
        "status": "pass" if not errors else "fail",
        "strict": args.strict,
        "manifest": str(manifest_path.relative_to(root)),
        "manifest_sha256": sha256(manifest_path) if manifest_path.is_file() else None,
        "artifact_count": len(manifest.get("artifacts", [])),
        "closeout_state": manifest.get("closeout_state"),
        "raw_performance_gate": manifest.get("raw_performance_gate", {}).get("status"),
        "design_disposition": manifest.get("design_disposition", {}).get("status"),
        "checkpoint_b_eligible": checkpoint_b_eligible,
        "checkpoint_b_bound": checkpoint_b_bound,
        "closeout_eligible": closeout_eligible,
        "evidence_integrity_errors": len(errors),
        "errors": errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

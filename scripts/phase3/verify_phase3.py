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
    "load_seconds", "token_latency_p50_seconds", "token_latency_p95_seconds",
    "token_latency_p99_seconds", "peak_rss_kib", "gpu_memory_bytes", "graphs_reused",
)
PROVIDER_COUNTERS = (
    "provider_objects", "provider_bind_calls", "provider_prepare_calls",
    "provider_handles_acquired", "provider_handles_released", "provider_allocations",
    "provider_callbacks", "provider_tensor_copies", "provider_synchronizations",
)
FINAL_CAPTURE_RULE = "single-complete-standing-capture-v1"
FINAL_CAPTURE_APPROVAL_COMMENT_ID = 5127588494


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
    }
    if overhead.get("validation_contract") != expected:
        errors.append("provider overhead does not bind the approved standing final-capture contract")
    if "composition" in overhead:
        errors.append("provider overhead contains forbidden cross-attempt composition")


def validate_evidence(root: Path, errors: list[str]) -> None:
    result_root = root / "results/2026-07-29/skynet/phase3-resident-provider"
    try:
        parity = json.loads((result_root / "provider-parity.json").read_text())
        lifecycle = json.loads((result_root / "lifecycle-and-failures.json").read_text())
        overhead = json.loads((result_root / "provider-overhead.json").read_text())
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

    coverage = lifecycle.get("coverage", {})
    boolean_coverage = [value for value in coverage.values() if isinstance(value, bool)]
    if lifecycle.get("status") != "pass" or not all(boolean_coverage):
        errors.append("lifecycle/failure evidence is incomplete")
    if coverage.get("cpu_model_load_decode_unload_cycles", 0) < 20:
        errors.append("CPU load stress is below 20 cycles")
    if coverage.get("cuda_model_load_decode_unload_cycles", 0) < 10:
        errors.append("CUDA load stress is below 10 cycles")

    combinations = overhead.get("combinations", [])
    validate_final_capture_contract(overhead, errors)
    if overhead.get("status") != "pass" or len(combinations) != 4:
        errors.append("provider overhead is not a four-combination pass")
    for combination in combinations:
        if len(combination.get("comparisons", [])) != 2 or not combination.get("passed"):
            errors.append(f"overhead comparison failed: {combination.get('artifact')}/{combination.get('backend')}")
        for comparison in combination.get("comparisons", []):
            if len(comparison.get("runs", [])) != 20 or comparison.get("order") != list(("a", "b", "b", "a")*5):
                errors.append("overhead ABBA raw sample protocol differs")
            for analysis in comparison.get("analysis", {}).values():
                if len(analysis.get("pairs", [])) != 10 or analysis.get("degrees_of_freedom") != 9:
                    errors.append("overhead pairing/confidence metadata differs")
                if not analysis.get("passed") or analysis.get("one_sided_95_percent_upper_bound", 1) > analysis.get("fixed_budget", 0):
                    errors.append("overhead confidence gate did not pass")
            for sample in comparison.get("warmups", []) + comparison.get("runs", []):
                validate_performance_sample(sample, errors)
            reported = comparison.get("reported_non_gated_means", {})
            if set(reported) != set(REQUIRED_PERFORMANCE_TELEMETRY):
                errors.append("non-gated performance summary omits required telemetry")
            elif any(set(values) != {"a", "b"} for values in reported.values()):
                errors.append("non-gated performance summary omits a comparison side")

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
        project_status = [line for line in project_status if output_relative is None or line[3:] != output_relative]
        if project_status:
            errors.append(f"project worktree is not clean: {project_status}")
        if git(nested, "status", "--porcelain", "--untracked-files=all"):
            errors.append("nested llama.cpp worktree is not clean")

    plan = (root / "docs/plan/00-foundation.md").read_text()
    phase3 = plan.split("## Phase 3", 1)[1].split("## Phase 4", 1)[0]
    if "- [ ]" in phase3:
        errors.append("Phase 3 foundation plan still contains unchecked tasks")
    reviews = {review["checkpoint"] for review in manifest.get("reviews", [])}
    if "A" not in reviews:
        errors.append("accepted Checkpoint A is not bound")
    if manifest.get("closeout_state") == "complete" and "B" not in reviews:
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
        schema = json.loads((root / "schemas/phase3/phase3-manifest-v1.schema.json").read_text())
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

    validate_evidence(root, errors)
    validate_git(root, manifest, args.strict, output, errors)
    result = {
        "schema_version": "phase3-verification-result-v1",
        "status": "pass" if not errors else "fail",
        "strict": args.strict,
        "manifest": str(manifest_path.relative_to(root)),
        "manifest_sha256": sha256(manifest_path) if manifest_path.is_file() else None,
        "artifact_count": len(manifest.get("artifacts", [])),
        "errors": errors,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

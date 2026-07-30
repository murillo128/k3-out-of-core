#!/usr/bin/env python3
"""Strict deterministic verifier for issue #17 Phase 4 evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_A_LLAMA, CHECKPOINT_A_PROJECT,
                    LLAMA_BASE, MODELS, PROJECT_BASE, git, json_write, sha256)


ALLOWED_NESTED = {
    "include/llama.h", "src/CMakeLists.txt", "src/llama-context.cpp", "src/llama-context.h", "src/llama-expert-weight-provider.cpp",
    "src/llama-expert-weight-provider.h", "src/llama-graph.cpp", "src/llama-model.cpp", "src/llama-model.h",
    "src/llama.cpp", "tests/CMakeLists.txt", "tests/test-expert-weight-provider.cpp", "tests/test-hot-expert-cache.cpp",
    "tests/phase4-hot-cache-probe.cpp",
}
ALLOWED_PROJECT_PREFIXES = ("llama.cpp", "PLAN.md", "docs/", "scripts/phase4/", "schemas/phase4/",
                            "tests/phase4/", "results/2026-07-30/skynet/phase4-hot-cache/")
PROTECTED_PHASE3 = "results/2026-07-29/skynet/phase3-resident-provider"


def validate_evidence(root: Path, manifest: dict, errors: list[str]) -> None:
    result_root = root / "results/2026-07-30/skynet/phase4-hot-cache"
    try:
        parity = json.loads((result_root / "hot-cache-parity.json").read_text())
        lifecycle = json.loads((result_root / "lifecycle-and-failures.json").read_text())
    except Exception as error:
        errors.append(f"cannot load evidence: {error}")
        return
    if parity.get("status") != "pass" or len(parity.get("cases", [])) != 4:
        errors.append("parity is not a four-case pass")
    for case in parity.get("cases", []):
        if not all(case.get("checks", {}).values()): errors.append("parity contains a failed check")
        hot, disabled = case.get("hot", {}), case.get("disabled", {})
        final = hot.get("final_hot", {})
        if hot.get("routes_sha256") != disabled.get("routes_sha256") or hot.get("logits_sha256") != disabled.get("logits_sha256"):
            errors.append("disabled/hot route or logit identity differs")
        if final.get("current_pins") != 0 or final.get("pin_acquires") != final.get("pin_releases"):
            errors.append("hot-cache pins are unbalanced")
        if case.get("capacity_class") == "all-experts" and final.get("hits", 0) <= 0:
            errors.append("all-experts case has no true hit")
    coverage = lifecycle.get("coverage", {})
    if lifecycle.get("status") != "pass" or any(value is not True for value in coverage.values() if isinstance(value, bool)):
        errors.append("lifecycle coverage is incomplete")
    if coverage.get("warm_epochs", 0) < 20 or len(lifecycle.get("warm_runs", [])) != 2:
        errors.append("warm lifecycle evidence is incomplete")
    phase3 = manifest.get("phase3_input", {})
    phase3_path = root / phase3.get("path", "")
    if not phase3_path.is_file() or phase3_path.stat().st_size != phase3.get("size") or sha256(phase3_path) != phase3.get("sha256"):
        errors.append("immutable Phase 3 manifest identity differs")


def validate_git(root: Path, manifest: dict, strict: bool, errors: list[str]) -> None:
    nested = root / "llama.cpp"
    revisions = manifest.get("revisions", {})
    candidate = revisions.get("llama_cpp_candidate", "")
    if git(nested, "rev-parse", "HEAD") != candidate or git(root, "rev-parse", "HEAD:llama.cpp") != candidate:
        errors.append("nested head/gitlink differs from manifest")
    for repository, base in ((root, PROJECT_BASE), (nested, LLAMA_BASE)):
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=repository).returncode != 0:
            errors.append(f"{repository.name} does not descend from execution base")
        check = subprocess.run(["git", "diff", "--check", f"{base}..HEAD"], cwd=repository, text=True, capture_output=True)
        if check.returncode != 0: errors.append(f"whitespace failure in {repository.name}: {check.stdout}")
    unexpected_nested = set(git(nested, "diff", "--name-only", f"{LLAMA_BASE}..{candidate}").splitlines()) - ALLOWED_NESTED
    if unexpected_nested: errors.append(f"nested scope differs: {sorted(unexpected_nested)}")
    for path in git(root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD").splitlines():
        if not any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_PROJECT_PREFIXES):
            errors.append(f"project scope differs: {path}")
    if git(root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD", "--", PROTECTED_PHASE3):
        errors.append("immutable Phase 3 evidence changed")
    if strict and git(root, "status", "--porcelain", "--untracked-files=all"):
        errors.append("project worktree is not clean")
    if strict and git(nested, "status", "--porcelain", "--untracked-files=all"):
        errors.append("nested worktree is not clean")


def validate_artifacts(root: Path, manifest: dict, errors: list[str]) -> None:
    seen = set()
    for artifact in manifest.get("artifacts", []):
        path = root / artifact.get("path", "")
        if artifact.get("path") in seen: errors.append("duplicate artifact path")
        seen.add(artifact.get("path"))
        if not path.is_file() or path.stat().st_size != artifact.get("size") or sha256(path) != artifact.get("sha256"):
            errors.append(f"artifact identity differs: {artifact.get('path')}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(args.manifest.read_text())
        schema = json.loads((root / "schemas/phase4/phase4-manifest-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(manifest)
    except Exception as error:
        print(f"FAIL: schema/manifest: {error}")
        return 1
    expected_review = {"comment_id": CHECKPOINT_A_COMMENT, "verdict": "PASS_WITH_NOTES", "safety_to_proceed": "YES",
                       "project_head": CHECKPOINT_A_PROJECT, "llama_cpp_head": CHECKPOINT_A_LLAMA}
    review = manifest.get("checkpoint_a", {})
    if any(review.get(key) != value for key, value in expected_review.items()): errors.append("Checkpoint A binding differs")
    validate_evidence(root, manifest, errors)
    validate_artifacts(root, manifest, errors)
    validate_git(root, manifest, args.strict, errors)
    models = {"f16": args.models_dir / MODELS["f16"]["name"], "mxfp4": args.models_dir / MODELS["mxfp4"]["name"]}
    for name, path in models.items():
        if not path.is_file() or path.stat().st_size != MODELS[name]["size"] or sha256(path) != MODELS[name]["sha256"]:
            errors.append(f"model identity differs: {name}")
    result_path = args.manifest.parent / "verification-result.json"
    result = {"schema_version": "phase4-verification-v1", "status": "pass" if not errors else "fail",
              "manifest_sha256": sha256(args.manifest), "errors": errors}
    json_write(result_path, result)
    if errors:
        for error in errors: print(f"FAIL: {error}")
        return 1
    print(f"PASS: strict={args.strict} manifest={args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

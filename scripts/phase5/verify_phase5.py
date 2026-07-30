#!/usr/bin/env python3
"""Strict deterministic verifier for issue #20 Phase 5 evidence."""

from __future__ import annotations
import argparse, json, subprocess
from pathlib import Path
from jsonschema import Draft202012Validator
from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_A_LLAMA, CHECKPOINT_A_PROJECT,
                    LLAMA_BASE, MODELS, PHASE4_MANIFEST, PROJECT_BASE, git, json_write, sha256)
from capture_validation_results import COMMANDS

ALLOWED_NESTED = {"include/llama.h", "src/CMakeLists.txt", "src/llama-cold-expert-cache.cpp", "src/llama-cold-expert-cache.h",
    "src/llama-context.cpp",
    "src/llama-expert-transfer-ring.cpp", "src/llama-expert-transfer-ring.h", "src/llama-expert-weight-provider.cpp",
    "src/llama-expert-weight-provider.h", "src/llama-model.cpp", "tests/CMakeLists.txt", "tests/phase5-cold-cache-probe.cpp",
    "tests/test-cold-expert-cache.cpp", "tests/test-expert-transfer-ring.cpp", "tests/test-expert-weight-provider.cpp", "tests/test-hot-expert-cache.cpp"}
ALLOWED_PROJECT_PREFIXES = ("llama.cpp", "PLAN.md", "docs/", "scripts/phase5/", "schemas/phase5/", "tests/phase5/",
                            "results/2026-07-30/skynet/phase5-cold-cache/")
PROTECTED_PHASE4 = "results/2026-07-30/skynet/phase4-hot-cache"

def validate_evidence(root, manifest, errors):
    result_root = root / "results/2026-07-30/skynet/phase5-cold-cache"
    try:
        transfer = json.loads((result_root / "transfer-ring.json").read_text())
        parity = json.loads((result_root / "cold-cache-parity.json").read_text())
        lifecycle = json.loads((result_root / "lifecycle-and-failures.json").read_text())
        validation = json.loads((result_root / "validation-results.json").read_text())
    except Exception as error: errors.append(f"cannot load evidence: {error}"); return
    if transfer.get("status") != "pass" or len(transfer.get("cases", [])) != 2: errors.append("transfer evidence incomplete")
    for case in transfer.get("cases", []):
        if not all(case.get("checks", {}).values()): errors.append("transfer check failed")
    if parity.get("status") != "pass" or len(parity.get("cases", [])) != 4: errors.append("parity evidence incomplete")
    for case in parity.get("cases", []):
        if not all(case.get("checks", {}).values()): errors.append("parity check failed")
        b, p, f = (case.get(name, {}).get("diagnostics", {}) for name in ("baseline", "pinned", "pageable_fallback"))
        for key in ("prompt_ids", "tokens", "route_hash", "route_records", "logits_hash"):
            if b.get(key) != p.get(key) or b.get(key) != f.get(key): errors.append(f"parity identity differs: {key}")
        if p.get("cold_actual_bytes", 1) > p.get("cold_requested_bytes", 0) or p.get("ring_actual_bytes", 1) > p.get("ring_requested_bytes", 0): errors.append("resource budget exceeded")
        if p.get("source_pinned_bytes") != 0 or p.get("source_pageable") != 1: errors.append("source is not provably pageable")
        if p.get("ring_pinned_bytes", 1) > p.get("ring_actual_bytes", 0): errors.append("pinned bytes exceed ring")
        if f.get("ring_pinned_bytes") != 0 or f.get("ring_async_enqueues") != 0 or f.get("ring_fallback") != 1: errors.append("fallback telemetry false")
        if any(p.get(key) != 0 for key in ("cold_transfer_refs", "cold_request_refs", "cold_failed_copies", "cold_failed_cleanups")): errors.append("pinned case not quiescent")
    coverage = lifecycle.get("coverage", {})
    if lifecycle.get("status") != "pass" or any(value is not True for value in coverage.values() if isinstance(value, bool)): errors.append("lifecycle coverage incomplete")
    if coverage.get("warm_epochs", 0) < 20 or len(lifecycle.get("warm_runs", [])) != 2 or len(lifecycle.get("rejected_load_modes", [])) != 4: errors.append("warm/rejection lifecycle incomplete")
    validate_commands(validation, manifest, errors)
    expected_phase4 = manifest.get("phase4_input", {}); phase4 = root / PHASE4_MANIFEST
    if not phase4.is_file() or phase4.stat().st_size != expected_phase4.get("size") or sha256(phase4) != expected_phase4.get("sha256"): errors.append("Phase 4 manifest identity differs")

def validate_commands(results, manifest, errors):
    expected = {name: command for name, command in COMMANDS}; records = results.get("commands", [])
    if results.get("status") != "pass" or len(records) != len(expected): errors.append("validation command set incomplete"); return
    by_name = {record.get("name"): record for record in records}
    if set(by_name) != set(expected): errors.append("validation command names differ")
    for name, command in expected.items():
        record = by_name.get(name, {})
        if record.get("command") != command or record.get("exit_code") != 0: errors.append(f"validation differs: {name}")
        if name.startswith(("ctest-", "unittest-")) and (record.get("total") in (None, 0) or record.get("passed") != record.get("total")): errors.append(f"test count differs: {name}")
        if name.startswith("status-") and record.get("stdout_bytes") != 0: errors.append(f"dirty validation tree: {name}")
    revisions = manifest.get("revisions", {})
    if results.get("project_head") != revisions.get("project_evidence_head") or results.get("llama_cpp_head") != revisions.get("llama_cpp_candidate"): errors.append("validation heads differ")
    if manifest.get("validation") != records: errors.append("manifest validation records differ")

def validate_git(root, manifest, strict, errors):
    nested = root / "llama.cpp"; revisions = manifest.get("revisions", {}); candidate = revisions.get("llama_cpp_candidate", "")
    if git(nested, "rev-parse", "HEAD") != candidate or git(root, "rev-parse", "HEAD:llama.cpp") != candidate: errors.append("nested head/gitlink mismatch")
    for repository, base in ((root, PROJECT_BASE), (nested, LLAMA_BASE)):
        if subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], cwd=repository).returncode: errors.append("execution base is not ancestor")
        if subprocess.run(["git", "diff", "--check", f"{base}..HEAD"], cwd=repository).returncode: errors.append("diff check failed")
    unexpected = set(git(nested, "diff", "--name-only", f"{LLAMA_BASE}..{candidate}").splitlines()) - ALLOWED_NESTED
    if unexpected: errors.append(f"nested scope differs: {sorted(unexpected)}")
    for path in git(root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD").splitlines():
        if not any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_PROJECT_PREFIXES): errors.append(f"project scope differs: {path}")
    if git(root, "diff", "--name-only", f"{PROJECT_BASE}..HEAD", "--", PROTECTED_PHASE4): errors.append("immutable Phase 4 evidence changed")
    if strict and git(root, "status", "--porcelain", "--untracked-files=all"): errors.append("project worktree is not clean")
    if strict and git(nested, "status", "--porcelain", "--untracked-files=all"): errors.append("nested worktree is not clean")

def validate_artifacts(root, manifest, errors):
    seen = set()
    for artifact in manifest.get("artifacts", []):
        relative = artifact.get("path", ""); path = root / relative
        if relative in seen: errors.append("duplicate artifact")
        seen.add(relative)
        if not path.is_file() or path.stat().st_size != artifact.get("size") or sha256(path) != artifact.get("sha256"): errors.append(f"artifact differs: {relative}")

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--manifest", type=Path, required=True); parser.add_argument("--models-dir", type=Path, required=True); parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(); root = args.project_root.resolve(); errors = []
    try:
        manifest = json.loads(args.manifest.read_text()); schema = json.loads((root / "schemas/phase5/phase5-manifest-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema); Draft202012Validator(schema).validate(manifest)
    except Exception as error: print(f"FAIL: schema/manifest: {error}"); return 1
    expected = {"comment_id": CHECKPOINT_A_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES", "project_head": CHECKPOINT_A_PROJECT, "llama_cpp_head": CHECKPOINT_A_LLAMA}
    if any(manifest.get("checkpoint_a", {}).get(key) != value for key, value in expected.items()): errors.append("Checkpoint A binding differs")
    validate_evidence(root, manifest, errors); validate_artifacts(root, manifest, errors); validate_git(root, manifest, args.strict, errors)
    for name, expected_model in MODELS.items():
        path = args.models_dir / expected_model["name"]
        if not path.is_file() or path.stat().st_size != expected_model["size"] or sha256(path) != expected_model["sha256"]: errors.append(f"model differs: {name}")
    result = {"schema_version": "phase5-verification-v1", "status": "pass" if not errors else "fail", "manifest_sha256": sha256(args.manifest), "errors": errors}
    json_write(args.manifest.parent / "verification-result.json", result)
    for error in errors: print(f"FAIL: {error}")
    if errors: return 1
    print(f"PASS: strict={args.strict} manifest={args.manifest}"); return 0

if __name__ == "__main__": raise SystemExit(main())

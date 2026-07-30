#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jsonschema import Draft202012Validator

from common import *


ALLOWED_NESTED = {
    "include/llama.h", "src/CMakeLists.txt", "src/llama-cold-expert-cache.cpp",
    "src/llama-cold-expert-cache.h", "src/llama-context.cpp", "src/llama-expert-storage.cpp",
    "src/llama-expert-storage.h", "src/llama-expert-transfer-ring.cpp",
    "src/llama-expert-transfer-ring.h", "src/llama-expert-weight-provider.cpp",
    "src/llama-expert-weight-provider.h", "src/llama-mmap.cpp", "src/llama-mmap.h",
    "src/llama-model-loader.cpp", "src/llama-model-loader.h", "src/llama-model.cpp",
    "src/llama-model.h", "src/llama-quant.cpp", "src/llama.cpp", "tests/CMakeLists.txt",
    "tests/phase5-cold-cache-probe.cpp", "tests/phase6-gguf-storage-probe.cpp",
    "tests/test-cold-expert-cache.cpp", "tests/test-expert-storage.cpp",
    "tests/test-hot-expert-cache.cpp",
}
ALLOWED_CLOSEOUT = {
    "results/2026-07-30/skynet/phase6-gguf-storage/phase6-manifest.json",
    "results/2026-07-30/skynet/phase6-gguf-storage/verification-result.json",
}
ALLOWED_CAPTURE_OUTPUTS = {
    "results/2026-07-30/skynet/phase6-gguf-storage/storage-layout.json",
    "results/2026-07-30/skynet/phase6-gguf-storage/gguf-demand-parity.json",
    "results/2026-07-30/skynet/phase6-gguf-storage/lifecycle-and-failures.json",
    "results/2026-07-30/skynet/phase6-gguf-storage/validation-results.json",
}
EXPECTED_VALIDATION = {
    "build-cpu", "build-cuda", "ctest-cpu", "ctest-cuda", "unittest-phase5",
    "unittest-phase6", "diff-nested", "diff-project",
}
REPRESENTATIONS = {"f16", "mxfp4"}
KINDS = {"original", "split"}


def load_identity(root: Path, item: dict, errors: list[str]) -> None:
    path = root/item["path"]
    if not path.is_file() or path.stat().st_size != item["size"] or sha256(path) != item["sha256"]:
        errors.append(f"identity mismatch: {item['path']}")


def all_checks(record: dict) -> bool:
    return bool(record.get("checks")) and all(value is True for value in record["checks"].values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root/"llama.cpp"
    errors: list[str] = []
    if args.models_dir.resolve() != (root/"models/gguf").resolve():
        errors.append("unexpected models directory")
    try:
        manifest = json.loads(args.manifest.read_text())
        schema = json.loads((root/"schemas/phase6/phase6-manifest-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(manifest)
    except Exception as error:
        print(f"FAIL: {error}")
        return 1

    revisions = manifest["revisions"]
    project_head = git(root, "rev-parse", "HEAD")
    nested_head = git(nested, "rev-parse", "HEAD")
    if nested_head != revisions["llama_cpp_candidate"] or git(root, "rev-parse", "HEAD:llama.cpp") != revisions["gitlink"]:
        errors.append("nested head/gitlink mismatch")
    if revisions["project_execution_base"] != PROJECT_BASE or revisions["llama_cpp_base"] != LLAMA_BASE:
        errors.append("execution base mismatch")
    evidence_head = revisions["project_evidence_head"]
    if subprocess.run(["git", "merge-base", "--is-ancestor", evidence_head, project_head], cwd=root).returncode:
        errors.append("project evidence head is not an ancestor of current HEAD")
    elif set(git(root, "diff", "--name-only", f"{evidence_head}..{project_head}").splitlines()) - ALLOWED_CLOSEOUT:
        errors.append("project changed outside manifest closeout after evidence head")
    if set(git(nested, "diff", "--name-only", f"{LLAMA_BASE}..HEAD").splitlines()) - ALLOWED_NESTED:
        errors.append("nested scope mismatch")
    expected_checkpoint = {"comment_id": CHECKPOINT_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES",
        "project_head": CHECKPOINT_PROJECT, "llama_cpp_head": CHECKPOINT_LLAMA, "independent_read_only": True}
    if manifest["checkpoint_a"] != expected_checkpoint:
        errors.append("checkpoint binding mismatch")

    for item in manifest["models"] + list(manifest["evidence"].values()) + manifest["artifacts"] + [manifest["phase5_input"]]:
        load_identity(root, item, errors)
    if any(value is not True for value in manifest["gates"].values()):
        errors.append("one or more manifest gates are not true")

    evidence = {name: json.loads((root/item["path"]).read_text()) for name, item in manifest["evidence"].items()}
    layout = evidence["storage_layout"]
    if layout.get("status") != "pass" or layout.get("generated_files_retained") is not False:
        errors.append("storage layout status/retention mismatch")
    command = layout.get("split_command", {})
    if command != {"tool": "llama-gguf-split", "tool_head": nested_head, "mode": "split", "max_tensors": 1}:
        errors.append("split command binding mismatch")
    if layout.get("bundle_projection_spans") != 3:
        errors.append("bundle projection-span evidence mismatch")
    model_by_rep = dict(zip(("f16", "mxfp4"), manifest["models"]))
    for representation in REPRESENTATIONS:
        records = manifest["generated_splits"].get(representation, [])
        numbers = [item.get("number") for item in records]
        if len(records) != 218 or numbers != list(range(1, 219)) or any(item.get("count") != 218 for item in records):
            errors.append(f"{representation} split sequence mismatch")
        if len({item.get("path") for item in records}) != 218 or len({item.get("sha256") for item in records}) != 218:
            errors.append(f"{representation} split identity is not unique")
        if any(item.get("source_model") != model_by_rep[representation] for item in records):
            errors.append(f"{representation} source-model lineage mismatch")
    layout_cases = layout.get("cases", [])
    if {(item.get("representation"), item.get("kind")) for item in layout_cases} != {(r, k) for r in REPRESENTATIONS for k in KINDS}:
        errors.append("storage layout case matrix mismatch")
    for item in layout_cases:
        diagnostics = item.get("diagnostics", {})
        expected_files = 1 if item.get("kind") == "original" else 218
        if not all_checks(item) or diagnostics.get("storage_entries") != 56 or diagnostics.get("storage_spans") != 168 or diagnostics.get("storage_files") != expected_files:
            errors.append(f"invalid storage layout case: {item.get('representation')}/{item.get('kind')}")
        if any(diagnostics.get(key) != 0 for key in ("deferred_allocated_bytes", "deferred_mmap_bound_bytes", "deferred_prefetch_bytes", "storage_reads")):
            errors.append(f"non-metadata-only layout case: {item.get('representation')}/{item.get('kind')}")

    parity = evidence["demand_parity"]
    if parity.get("status") != "pass" or parity.get("warm_epochs", 0) < 20:
        errors.append("demand parity status/warm epochs mismatch")
    parity_cases = parity.get("cases", [])
    hierarchy = parity.get("hierarchy", [])
    expected_matrix = {(r, k) for r in REPRESENTATIONS for k in KINDS}
    if {(item.get("representation"), item.get("kind")) for item in parity_cases} != expected_matrix:
        errors.append("demand parity case matrix mismatch")
    if {(item.get("representation"), item.get("kind")) for item in hierarchy} != expected_matrix:
        errors.append("cold hierarchy case matrix mismatch")
    for item in hierarchy:
        diagnostics = item.get("diagnostics", {})
        if not all_checks(item) or diagnostics.get("cold_hits", 0) <= 0 or diagnostics.get("storage_read_requests") != diagnostics.get("cold_misses"):
            errors.append(f"invalid cold hierarchy case: {item.get('representation')}/{item.get('kind')}")
        if diagnostics.get("storage_integrity_checks") != diagnostics.get("storage_read_requests") or diagnostics.get("storage_integrity_mismatches") != 0:
            errors.append(f"cold hierarchy integrity mismatch: {item.get('representation')}/{item.get('kind')}")
    for item in parity_cases:
        baseline = item.get("baseline", {})
        if item.get("baseline_exit_code") != 0 or baseline.get("mode") != "disabled" or baseline.get("route_records", 0) <= 0:
            errors.append(f"invalid baseline: {item.get('representation')}/{item.get('kind')}")
        captures = item.get("captures", [])
        if len(captures) != 2:
            errors.append(f"capture count mismatch: {item.get('representation')}/{item.get('kind')}")
        for capture in captures:
            diagnostics = capture.get("diagnostics", {})
            if not all_checks(capture) or diagnostics.get("cold_evictions", 0) <= 0 or diagnostics.get("storage_read_requests") != diagnostics.get("cold_misses"):
                errors.append(f"invalid eviction capture: {item.get('representation')}/{item.get('kind')}")
            if diagnostics.get("storage_integrity_checks") != diagnostics.get("storage_read_requests") or diagnostics.get("storage_integrity_mismatches") != 0:
                errors.append(f"eviction capture integrity mismatch: {item.get('representation')}/{item.get('kind')}")

    lifecycle = evidence["lifecycle"]
    lifecycle_cases = lifecycle.get("cases", [])
    if lifecycle.get("status") != "pass" or {item.get("representation") for item in lifecycle_cases} != REPRESENTATIONS:
        errors.append("lifecycle case matrix mismatch")
    if any(not all_checks(item) for item in lifecycle_cases) or lifecycle.get("coverage", {}).get("hard_integrity_poison") != "test-expert-storage":
        errors.append("lifecycle checks/coverage mismatch")

    validation = manifest["validation"]
    if {item.get("name") for item in validation} != EXPECTED_VALIDATION or len(validation) != len(EXPECTED_VALIDATION):
        errors.append("validation command matrix mismatch")
    if any(item.get("exit_code") != 0 for item in validation):
        errors.append("validation command failed")
    validation_record = evidence["validation"]
    if validation_record.get("status") != "pass" or validation_record.get("commands") != validation:
        errors.append("validation evidence mismatch")
    validation_head = validation_record.get("project_head", "")
    if validation_record.get("llama_cpp_head") != nested_head or subprocess.run(
            ["git", "merge-base", "--is-ancestor", validation_head, evidence_head], cwd=root).returncode:
        errors.append("validation revision binding mismatch")
    elif set(git(root, "diff", "--name-only", f"{validation_head}..{evidence_head}").splitlines()) - ALLOWED_CAPTURE_OUTPUTS:
        errors.append("project changed outside capture outputs after validation head")

    if subprocess.run(["git", "diff", "--check", f"{PROJECT_BASE}..HEAD"], cwd=root).returncode or subprocess.run(["git", "diff", "--check", f"{LLAMA_BASE}..HEAD"], cwd=nested).returncode:
        errors.append("diff check failed")
    if args.strict and (git(root, "status", "--porcelain", "--untracked-files=all") or git(nested, "status", "--porcelain", "--untracked-files=all")):
        errors.append("worktree not clean")
    write(args.manifest.parent/"verification-result.json", {"schema_version": "phase6-verification-v1",
        "status": "pass" if not errors else "fail", "manifest_sha256": sha256(args.manifest), "errors": errors})
    for error in errors:
        print("FAIL:", error)
    if not errors:
        print("PASS: strict Phase 6 evidence verified")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

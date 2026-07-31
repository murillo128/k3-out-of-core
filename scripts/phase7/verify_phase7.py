#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
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
    PROJECT_BASE,
    git,
    sha256,
    write,
)

ALLOWED_CLOSEOUT = {
    "docs/STATUS.md",
    "results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json",
    "results/2026-07-31/skynet/phase7-async-runtime/verification-result.json",
}
ALLOWED_NESTED = {
    "include/llama.h", "src/CMakeLists.txt", "src/llama-cold-expert-cache.cpp", "src/llama-cold-expert-cache.h",
    "src/llama-context.cpp", "src/llama-context.h", "src/llama-expert-async-io.cpp", "src/llama-expert-async-io.h",
    "src/llama-expert-scheduler.cpp", "src/llama-expert-scheduler.h", "src/llama-expert-storage.cpp",
    "src/llama-expert-storage.h", "src/llama-expert-transfer-ring.cpp", "src/llama-expert-transfer-ring.h",
    "src/llama-expert-weight-provider.cpp", "src/llama-expert-weight-provider.h", "src/llama-graph.cpp",
    "src/llama-mmap.cpp", "src/llama-mmap.h", "src/llama-model.cpp", "src/llama-model.h", "src/llama.cpp",
    "tests/CMakeLists.txt", "tests/phase5-cold-cache-probe.cpp", "tests/test-cold-expert-cache.cpp",
    "tests/test-expert-async-io.cpp", "tests/test-expert-scheduler.cpp", "tests/test-expert-storage.cpp",
    "tests/test-expert-transfer-ring.cpp",
}
EXPECTED_VALIDATION = {
    "build-cpu", "ctest-cpu", "build-cuda", "ctest-cuda", "configure-asan-ubsan", "build-asan-ubsan",
    "ctest-asan-ubsan", "configure-tsan", "build-tsan", "ctest-tsan-default-aslr", "ctest-tsan-aslr-disabled",
    "phase5-evidence-tests", "phase6-evidence-tests", "phase7-evidence-tests", "phase6-verifier",
}


def load_identity(root: Path, item: dict, errors: list[str]) -> None:
    path = root / item.get("path", "")
    if not path.is_file():
        errors.append(f"missing artifact: {item.get('path')}")
    elif path.stat().st_size != item.get("size") or sha256(path) != item.get("sha256"):
        errors.append(f"artifact identity mismatch: {item.get('path')}")


def checks_true(value: dict) -> bool:
    return bool(value) and all(item is True for item in value.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    manifest_path = args.manifest.resolve()
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text())
        schema = json.loads((root / "schemas/phase7/phase7-manifest-v1.schema.json").read_text())
        Draft202012Validator(schema).validate(manifest)
    except Exception as error:
        print(f"FAIL: {error}")
        return 1

    revisions = manifest["revisions"]
    expected_revisions = {
        "branch": "codex/phase7-async-runtime",
        "project_execution_base": PROJECT_BASE,
        "project_checkpoint_b_head": CHECKPOINT_B_PROJECT,
        "llama_cpp_base": LLAMA_BASE,
        "llama_cpp_candidate": LLAMA_CANDIDATE,
        "gitlink": LLAMA_CANDIDATE,
        "current_main_policy": MAIN_POLICY,
    }
    if any(revisions.get(key) != value for key, value in expected_revisions.items()):
        errors.append("revision binding mismatch")
    if git(nested, "rev-parse", "HEAD") != LLAMA_CANDIDATE or git(root, "rev-parse", "HEAD:llama.cpp") != LLAMA_CANDIDATE:
        errors.append("accepted nested head/gitlink changed")
    evidence_head = revisions["project_evidence_head"]
    if subprocess.run(["git", "merge-base", "--is-ancestor", evidence_head, "HEAD"], cwd=root).returncode:
        errors.append("project evidence head is not an ancestor of current HEAD")
    elif set(git(root, "diff", "--name-only", f"{evidence_head}..HEAD").splitlines()) - ALLOWED_CLOSEOUT:
        errors.append("project changed outside closeout allowlist after evidence head")
    if set(git(nested, "diff", "--name-only", f"{LLAMA_BASE}..HEAD").splitlines()) - ALLOWED_NESTED:
        errors.append("nested scope mismatch")

    expected_a = {
        "comment_id": CHECKPOINT_A_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES",
        "project_head": CHECKPOINT_A_PROJECT, "llama_cpp_head": CHECKPOINT_A_LLAMA, "independent_read_only": True,
    }
    expected_b = {
        "comment_id": CHECKPOINT_B_COMMENT, "verdict": "PASS", "safety_to_proceed": "YES",
        "project_head": CHECKPOINT_B_PROJECT, "llama_cpp_head": LLAMA_CANDIDATE, "independent_read_only": True,
    }
    if manifest["checkpoint_a"] != expected_a or manifest["checkpoint_b"] != expected_b:
        errors.append("checkpoint binding mismatch")
    if manifest["closeout_state"] == "final-review-candidate" and manifest["final_review"] is not None:
        errors.append("candidate manifest must keep final review pending")

    for item in list(manifest["evidence"].values()) + manifest["artifacts"] + [manifest["inputs"]["phase6_manifest"]] + manifest["inputs"]["models"]:
        load_identity(root, item, errors)
    if any(value is not True for value in manifest["gates"].values()):
        errors.append("one or more Phase 7 gates are not true")

    runtime = json.loads((root / manifest["evidence"]["runtime_matrix"]["path"]).read_text())
    if runtime.get("status") != "pass" or len(runtime.get("cases", [])) != 4:
        errors.append("runtime matrix status/case count mismatch")
    elif any(not checks_true(case.get("checks", {})) for case in runtime["cases"]):
        errors.append("runtime matrix parity/warm gate mismatch")
    if not checks_true(runtime.get("placement", {}).get("checks", {})) or not checks_true(runtime.get("direct_io", {}).get("checks", {})):
        errors.append("placement/direct-I/O gate mismatch")
    if not checks_true(runtime.get("mechanism", {}).get("checks", {})):
        errors.append("runtime mechanism gate mismatch")
    if any(runtime.get("split_lineage", {}).get(name, {}).get("count") != 218 for name in ("f16", "mxfp4")):
        errors.append("split lineage count mismatch")

    validation = json.loads((root / manifest["evidence"]["validation"]["path"]).read_text())
    records = validation.get("commands", [])
    names = {record.get("name") for record in records}
    if validation.get("status") != "pass" or names != EXPECTED_VALIDATION:
        errors.append("validation command set/status mismatch")
    if any(record.get("exit_code") != 0 for record in records if record.get("required")):
        errors.append("required validation command failed")
    if any(value != {"passed_percent": 100, "failed": 0, "total": 6} for value in validation.get("test_totals", {}).values()):
        errors.append("focused native test totals mismatch")
    if not validation.get("default_tsan", {}).get("known_environmental_limitation"):
        errors.append("default TSan disposition missing")

    checkpoint_final = json.loads((root / manifest["evidence"]["checkpoint_b_final_correction"]["path"]).read_text())
    checkpoint_placement = json.loads((root / manifest["evidence"]["checkpoint_b_placement_correction"]["path"]).read_text())
    if checkpoint_final.get("controlled_cross_flight_overlap", {}).get("status") != "PASS" or \
       checkpoint_final.get("provider_post_h2d_cancellation", {}).get("status") != "PASS" or \
       checkpoint_final.get("event_capacity_boundary", {}).get("status") != "PASS":
        errors.append("accepted Checkpoint B final-correction evidence mismatch")
    if checkpoint_placement.get("placement", {}).get("status") != "PASS" or \
       checkpoint_placement.get("common_parity", {}).get("status") != "PASS":
        errors.append("accepted Checkpoint B placement evidence mismatch")

    if subprocess.run(["git", "diff", "--check", f"{PROJECT_BASE}..HEAD"], cwd=root).returncode or \
       subprocess.run(["git", "diff", "--check", f"{LLAMA_BASE}..HEAD"], cwd=nested).returncode:
        errors.append("diff check failed")
    if args.strict and (git(root, "status", "--porcelain", "--untracked-files=all") or
                        git(nested, "status", "--porcelain", "--untracked-files=all")):
        errors.append("worktree not clean")

    result_path = manifest_path.parent / "verification-result.json"
    write(result_path, {
        "schema_version": "phase7-verification-v1",
        "status": "pass" if not errors else "fail",
        "manifest_sha256": sha256(manifest_path),
        "errors": errors,
    })
    for error in errors:
        print("FAIL:", error)
    if not errors:
        print("PASS: Phase 7 evidence verified")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

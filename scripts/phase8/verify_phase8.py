#!/usr/bin/env python3
"""Fail-closed verifier for the authoritative Phase 8 manifest and evidence."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_B_COMMENT, LLAMA_BASE,
                    LLAMA_CHECKPOINT_B, PHASE8_START, PROJECT_BASE, git, sha256, write)
from verify_checkpoint_b import verify_checkpoint_b_file

ALLOWED_AFTER_EVIDENCE = {
    "results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json",
    "results/2026-07-31/skynet/phase8-miss-execution/verification-result.json",
}
EXPECTED_EVIDENCE = {
    "checkpoint_b_probe", "miss_policy_parity", "hybrid_overlap",
    "miss_policy_benchmarks", "synthetic_store", "validation_results",
}
EXPECTED_VALIDATION = {
    "build-cpu", "ctest-cpu", "build-cuda", "ctest-cuda", "configure-asan-ubsan",
    "build-asan-ubsan", "ctest-asan-ubsan", "configure-tsan", "build-tsan",
    "ctest-tsan-default-aslr", "ctest-tsan-aslr-disabled", "phase7-evidence-tests",
    "phase8-evidence-tests", "phase7-verifier", "diff-check-nested", "diff-check-project",
}


class VerificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, f"duplicate JSON key: {key}")
        value[key] = item
    return value


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(), object_pairs_hook=no_duplicate_object)
    require(isinstance(value, dict), f"{path}: root must be an object")
    return value


def load_identity(root: Path, value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
        errors.append("malformed artifact identity")
        return
    path = Path(value["path"])
    if not path.is_absolute():
        path = root / path
    if not path.is_file():
        errors.append(f"missing artifact: {value['path']}")
    elif path.stat().st_size != value["size"] or sha256(path) != value["sha256"]:
        errors.append(f"artifact identity mismatch: {value['path']}")


def all_true(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and all(item is True for item in value.values())


def verify_manifest_payload(manifest: dict[str, Any]) -> None:
    revisions = manifest["revisions"]
    require(revisions["branch"] == "codex/phase8-miss-execution", "branch binding mismatch")
    require(revisions["project_execution_base"] == PROJECT_BASE, "project base mismatch")
    require(revisions["project_phase8_4_start"] == PHASE8_START, "Phase 8.4 start mismatch")
    require(isinstance(revisions["project_capture_head"], str) and len(revisions["project_capture_head"]) == 40,
            "capture head missing")
    require(revisions["llama_cpp_base"] == LLAMA_BASE, "nested base mismatch")
    require(revisions["llama_cpp_final"] == LLAMA_CHECKPOINT_B and
            revisions["gitlink"] == LLAMA_CHECKPOINT_B, "nested final/gitlink mismatch")
    require(manifest["checkpoint_a"]["comment_id"] == CHECKPOINT_A_COMMENT and
            manifest["checkpoint_a"]["project_head"] == "07da45728b38b2d7c6a3a1b156dffcea6b94ec54" and
            manifest["checkpoint_a"]["llama_cpp_head"] == "4cfee48aacb6b33ebcbda796b26106b69440e633",
            "Checkpoint A binding mismatch")
    require(manifest["checkpoint_b"]["comment_id"] == CHECKPOINT_B_COMMENT and
            manifest["checkpoint_b"]["project_head"] == PHASE8_START and
            manifest["checkpoint_b"]["llama_cpp_head"] == LLAMA_CHECKPOINT_B,
            "Checkpoint B binding mismatch")
    require(manifest["policies"] == {"default": "PROMOTE_AND_GPU", "cpu_fallback_explicit": True,
            "auto_explicit_version": 1, "background_promotion_default": False,
            "cost_model_digests_recorded": True}, "policy authority mismatch")
    require(all_true(manifest["gates"]), "one or more Phase 8 gates are not true")
    require(set(manifest["evidence"]) == EXPECTED_EVIDENCE, "evidence set mismatch")
    if manifest["closeout_state"] == "final-review-candidate":
        require(manifest["final_review"] is None, "candidate final review must be pending")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root, nested, manifest_path = args.project_root.resolve(), args.project_root.resolve() / "llama.cpp", args.manifest.resolve()
    errors: list[str] = []
    try:
        manifest = load_json(manifest_path)
        schema = load_json(root / "schemas/phase8/phase8-manifest-v1.schema.json")
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(manifest)
        verify_manifest_payload(manifest)
    except Exception as error:
        print(f"FAIL: {error}")
        return 1

    revisions = manifest["revisions"]
    evidence_head = revisions["project_evidence_head"]
    capture_head = revisions["project_capture_head"]
    if git(nested, "rev-parse", "HEAD") != LLAMA_CHECKPOINT_B or git(root, "rev-parse", "HEAD:llama.cpp") != LLAMA_CHECKPOINT_B:
        errors.append("accepted nested head/gitlink changed")
    if subprocess.run(["git", "merge-base", "--is-ancestor", evidence_head, "HEAD"], cwd=root).returncode:
        errors.append("project evidence head is not an ancestor of current HEAD")
    else:
        changed = set(git(root, "diff", "--name-only", f"{evidence_head}..HEAD").splitlines())
        if changed - ALLOWED_AFTER_EVIDENCE:
            errors.append(f"project changed outside final-record allowlist: {sorted(changed - ALLOWED_AFTER_EVIDENCE)}")
    if subprocess.run(["git", "merge-base", "--is-ancestor", capture_head, evidence_head], cwd=root).returncode:
        errors.append("capture head is not an ancestor of evidence head")

    for value in list(manifest["evidence"].values()) + manifest["artifacts"] + [manifest["inputs"]["phase7_manifest"]]:
        load_identity(root, value, errors)
    public = manifest["inputs"]["larger_public_moe"]
    for name in ("config", "tokenizer", "gguf"):
        load_identity(root, public[name], errors)
    if public.get("repository") != "ibm-granite/granite-3.1-3b-a800m-instruct" or \
       public.get("source_revision") != "a02780686e08a03fe0d2679a293b5c74a90efa89" or \
       public.get("primary_failure_comment") != 5145455677 or \
       public.get("converter_head") != LLAMA_CHECKPOINT_B:
        errors.append("larger public MoE provenance mismatch")

    evidence_files = {name: load_json(root / value["path"]) for name, value in manifest["evidence"].items()}
    for name in ("miss_policy_parity", "hybrid_overlap", "miss_policy_benchmarks", "validation_results"):
        if evidence_files[name].get("status") != "pass":
            errors.append(f"{name} is not passing")
    parity = evidence_files["miss_policy_parity"]
    if len(parity.get("cases", [])) != 5 or not all_true(parity.get("checks")):
        errors.append("policy parity matrix incomplete")
    overlap = evidence_files["hybrid_overlap"]
    if len(overlap.get("samples", [])) < 3 or not all_true(overlap.get("checks")) or overlap.get("overlap_us", {}).get("minimum", 0) <= 0:
        errors.append("controlled mixed overlap gate failed")
    benchmarks = evidence_files["miss_policy_benchmarks"]
    if len(benchmarks.get("matrix", [])) != 120 or not all_true(benchmarks.get("checks")):
        errors.append("benchmark/crossover matrix incomplete")
    validation = evidence_files["validation_results"]
    records = validation.get("commands", [])
    if {record.get("name") for record in records} != EXPECTED_VALIDATION:
        errors.append("validation command set mismatch")
    if any(record.get("exit_code") != 0 for record in records if record.get("required")):
        errors.append("required validation command failed")
    if any(value != {"passed_percent": 100, "failed": 0, "total": 5}
           for value in validation.get("test_totals", {}).values()):
        errors.append("native validation totals mismatch")
    if not validation.get("default_tsan", {}).get("known_environmental_limitation"):
        errors.append("default TSan disposition missing")

    probe_path = root / manifest["evidence"]["checkpoint_b_probe"]["path"]
    try:
        verify_checkpoint_b_file(probe_path, expected_outer=capture_head,
            expected_nested=LLAMA_CHECKPOINT_B, actual_outer=capture_head,
            actual_nested=LLAMA_CHECKPOINT_B, gitlink=LLAMA_CHECKPOINT_B,
            expected_models={"f16": {"size": 784318432, "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7"},
                             "mxfp4": {"size": 751976576, "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169"}})
    except Exception as error:
        errors.append(f"Checkpoint B production evidence failed: {error}")

    if subprocess.run(["git", "diff", "--check", f"{PROJECT_BASE}..HEAD"], cwd=root).returncode or \
       subprocess.run(["git", "diff", "--check", f"{LLAMA_BASE}..HEAD"], cwd=nested).returncode:
        errors.append("diff check failed")
    if args.strict and (git(root, "status", "--porcelain", "--untracked-files=all") or
                        git(nested, "status", "--porcelain", "--untracked-files=all")):
        errors.append("worktree not clean")

    result_path = manifest_path.parent / "verification-result.json"
    write(result_path, {"schema_version": "phase8-verification-v1",
        "status": "pass" if not errors else "fail", "manifest_sha256": sha256(manifest_path), "errors": errors})
    for error in errors:
        print("FAIL:", error)
    if not errors:
        print("PASS: Phase 8 evidence verified")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

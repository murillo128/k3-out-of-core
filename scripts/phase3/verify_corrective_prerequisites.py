#!/usr/bin/env python3
"""Bind every corrective-amendment prerequisite before the one allowed v2 standing capture."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import git, sha256


PROJECT_CORRECTIVE_BASE = "bb9a7778b207c248646c46083c03bdef5076c5bf"
LLAMA_CORRECTIVE_BASE = "523f825d2df5efa7c9a08561e2b64861ad5594c5"
HISTORICAL_IDENTITIES = {
    "provider-overhead.json": "df0fa1f05c6a57838e54f9b9da7a8d66f6ef826adf83fe3c19ccf771d04540a5",
    "provider-overhead-corrected-attempt1-fail.json": "0a2110c2b6d0608c08c4d93a514b55e8b2c3d579cf893fff238123dae752958b",
    "provider-overhead-corrected-attempt2-fail.json": "93e0229d852dc4248c4ca6909cd1a7043f8c49226ef213cd310d244b44c42a5d",
}
ALLOWED_NESTED_PATHS = {
    "src/llama-context.cpp",
    "src/llama-expert-weight-provider.cpp",
    "src/llama-expert-weight-provider.h",
    "src/llama-graph.cpp",
    "src/llama-graph.h",
    "tests/test-expert-weight-provider.cpp",
}
ALLOWED_PROJECT_PREFIXES = (
    "llama.cpp",
    "scripts/phase2/overhead_probe.cpp",
    "scripts/phase2/route_probe.cpp",
    "scripts/phase3/",
    "tests/phase3/",
    "results/2026-07-29/skynet/phase3-resident-provider/",
    "PLAN.md",
    "docs/",
)
REQUIRED_LIFECYCLE_COVERAGE = {
    "disabled_lifecycle",
    "resident_lifecycle",
    "shared_two_contexts_sequential_and_interleaved",
    "mixed_f16_mxfp4_models_and_modes_one_process",
    "repeated_context_create_destroy",
    "partial_initialization_failure",
    "graph_binding_failure_and_no_reuse",
    "graph_binding_storage_reserved_before_construction",
    "request_allocation_and_preparation_failure",
    "one_resident_lease_per_nonempty_ubatch",
    "empty_binding_set_has_no_lease",
    "invalid_key_and_descriptor",
    "resident_bundle_first_registration_and_fast_path",
    "resident_bundle_conflict_rejected",
    "resident_cached_binding_fault_injection",
    "resident_registration_thread_safe",
    "cancellation_before_submission",
    "abort_while_handles_held",
    "context_destroy_after_async_submission",
    "model_unload_after_contexts",
    "handles_released_exactly_once",
    "asan_ubsan_focused_unit",
}


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if value.get("status") != "pass":
        raise RuntimeError(f"prerequisite artifact is not a pass: {path}")
    return value


def diff_check(repository: Path, base: str) -> bool:
    committed = subprocess.run(
        ["git", "diff", "--check", f"{base}..HEAD"], cwd=repository,
        text=True, capture_output=True, check=False,
    )
    working = subprocess.run(
        ["git", "diff", "--check"], cwd=repository,
        text=True, capture_output=True, check=False,
    )
    return committed.returncode == 0 and working.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parity", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--administration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    nested = root / "llama.cpp"
    result_root = root / "results/2026-07-29/skynet/phase3-resident-provider"
    parity = load(args.parity.resolve())
    lifecycle = load(args.lifecycle.resolve())
    administration = load(args.administration.resolve())
    candidate = git(nested, "rev-parse", "HEAD")
    project_head = git(root, "rev-parse", "HEAD")
    coverage = lifecycle.get("coverage", {})

    nested_paths = set(git(nested, "diff", "--name-only", f"{LLAMA_CORRECTIVE_BASE}..HEAD").splitlines())
    project_paths = git(root, "diff", "--name-only", f"{PROJECT_CORRECTIVE_BASE}..HEAD").splitlines()
    historical = {
        name: {"expected_sha256": expected, "observed_sha256": sha256(result_root / name)}
        for name, expected in HISTORICAL_IDENTITIES.items()
    }
    checks = {
        "candidate_is_corrective_descendant": subprocess.run(
            ["git", "merge-base", "--is-ancestor", LLAMA_CORRECTIVE_BASE, "HEAD"],
            cwd=nested, check=False,
        ).returncode == 0 and candidate != LLAMA_CORRECTIVE_BASE,
        "candidate_is_clean_and_published": not git(nested, "status", "--porcelain", "--untracked-files=all") and
            git(nested, "rev-parse", "origin/codex/phase3-resident-provider") == candidate,
        "project_implementation_is_published": git(root, "rev-parse", "origin/codex/phase3-resident-provider") == project_head,
        "parity_revision_and_matrix": parity.get("revisions", {}).get("llama_candidate") == candidate and
            len(parity.get("cases", [])) == 4 and
            all(all(case.get("checks", {}).values()) for case in parity.get("cases", [])),
        "lifecycle_revision_and_coverage": lifecycle.get("revision") == candidate and
            all(coverage.get(name) is True for name in REQUIRED_LIFECYCLE_COVERAGE) and
            coverage.get("cpu_model_load_decode_unload_cycles", 0) >= 20 and
            coverage.get("cuda_model_load_decode_unload_cycles", 0) >= 10,
        "administration_revision_and_checks": administration.get("revisions", {}).get("candidate") == candidate and
            all(administration.get("checks", {}).values()),
        "nested_scope_bounded": bool(nested_paths) and nested_paths <= ALLOWED_NESTED_PATHS,
        "project_scope_bounded": all(
            any(path == prefix or path.startswith(prefix) for prefix in ALLOWED_PROJECT_PREFIXES)
            for path in project_paths
        ),
        "phase2_evidence_unchanged": not git(
            root, "diff", "--name-only", f"{PROJECT_CORRECTIVE_BASE}..HEAD", "--",
            "results/2026-07-29/skynet/phase2-observability", "schemas/phase2", "tests/fixtures/phase2",
        ),
        "historical_evidence_immutable": all(
            item["expected_sha256"] == item["observed_sha256"] for item in historical.values()
        ),
        "scope_whitespace_clean": diff_check(nested, LLAMA_CORRECTIVE_BASE) and
            diff_check(root, PROJECT_CORRECTIVE_BASE),
        "v2_capture_not_started": not (result_root / "provider-overhead-post-optimization.json").exists(),
    }
    if not all(checks.values()):
        raise RuntimeError(f"corrective prerequisites failed: {checks}")

    report = {
        "schema_version": "phase3-corrective-prerequisites-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "approval_comment_id": 5127774849,
        "revisions": {
            "project_corrective_base": PROJECT_CORRECTIVE_BASE,
            "project_implementation_head": project_head,
            "llama_corrective_base": LLAMA_CORRECTIVE_BASE,
            "llama_candidate": candidate,
        },
        "artifacts": {
            "parity": {"path": str(args.parity), "sha256": sha256(args.parity)},
            "lifecycle": {"path": str(args.lifecycle), "sha256": sha256(args.lifecycle)},
            "administration": {"path": str(args.administration), "sha256": sha256(args.administration)},
        },
        "historical_evidence": historical,
        "nested_changed_paths": sorted(nested_paths),
        "project_changed_paths": project_paths,
        "checks": checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "status": "pass", "checks": len(checks)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

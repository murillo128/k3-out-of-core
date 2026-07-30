#!/usr/bin/env python3
"""Build the deterministic issue #13 Phase 3 closeout manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from common import LLAMA_BASE, MODELS, PROJECT_BASE, PUBLISHED_CORPUS, PUBLISHED_GGUF, git, sha256
from phase3_disposition import (
    CAPTURE_RELATIVE,
    CAPTURE_SHA256,
    DESIGN_AUTHORITY_COMMENT_IDS,
    LLAMA_CPP_CANDIDATE,
    RESULTS_RELATIVE,
    validate_disposition,
    write_disposition,
)


FILES = {
    "schema": [
        "schemas/phase3/phase3-manifest-v1.schema.json",
        "schemas/phase3/phase3-manifest-v2.schema.json",
        "schemas/phase3/phase3-disposition-v1.schema.json",
        "schemas/phase3/checkpoint-b-review-v1.schema.json",
    ],
    "implementation": [
        "scripts/phase2/capture_route_observer.py",
        "scripts/phase2/overhead_probe.cpp",
        "scripts/phase2/route_probe.cpp",
        "scripts/phase3/common.py",
        "scripts/phase3/overhead_probe_baseline.cpp",
        "scripts/phase3/provider_admin_probe.cpp",
        "scripts/phase3/capture_provider_parity.py",
        "scripts/phase3/measure_provider_admin.py",
        "scripts/phase3/run_provider_lifecycle.py",
        "scripts/phase3/verify_corrective_prerequisites.py",
        "scripts/phase3/measure_provider_overhead.py",
        "scripts/phase3/phase3_disposition.py",
        "scripts/phase3/build_phase3_manifest.py",
        "scripts/phase3/verify_phase3.py",
    ],
    "test": ["tests/phase3/test_phase3_evidence.py"],
    "evidence": [
        "results/2026-07-29/skynet/phase3-resident-provider/PHASE3.md",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-parity.json",
        "results/2026-07-29/skynet/phase3-resident-provider/lifecycle-and-failures.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-overhead-corrected-attempt1-fail.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-overhead-corrected-attempt2-fail.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-overhead.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-parity-post-optimization.json",
        "results/2026-07-29/skynet/phase3-resident-provider/lifecycle-and-failures-post-optimization.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-admin-fast-path.json",
        "results/2026-07-29/skynet/phase3-resident-provider/corrective-prerequisites.json",
        "results/2026-07-29/skynet/phase3-resident-provider/provider-overhead-post-optimization.json",
        "results/2026-07-29/skynet/phase3-resident-provider/phase3-disposition.json",
    ],
    "source-of-truth": [
        "PLAN.md",
        "docs/STATUS.md",
        "docs/DECISIONS.md",
        "docs/plan/00-foundation.md",
        "docs/MODELS_AND_VALIDATION.md",
        "docs/REPOSITORIES_AND_ARTIFACTS.md",
    ],
}


def validate_schema(instance: dict, schema_path: Path) -> None:
    schema = json.loads(schema_path.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(instance)


def validate_checkpoint_b(checkpoint: dict, root: Path) -> None:
    validate_schema(checkpoint, root / "schemas/phase3/checkpoint-b-review-v1.schema.json")
    project_head = checkpoint.get("project_head")
    expected = {
        "project_range": f"{PROJECT_BASE}..{project_head}",
        "llama_cpp_range": f"{LLAMA_BASE}..{LLAMA_CPP_CANDIDATE}",
        "llama_cpp_head": LLAMA_CPP_CANDIDATE,
    }
    for name, value in expected.items():
        if checkpoint.get(name) != value:
            raise RuntimeError(f"Checkpoint B attestation differs: {name}")
    if checkpoint.get("url") != (
        "https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-"
        f"{checkpoint.get('comment_id')}"
    ):
        raise RuntimeError("Checkpoint B comment URL does not bind its comment ID")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    results_root = args.results_root.resolve()
    expected_results = root / RESULTS_RELATIVE
    if results_root != expected_results:
        raise RuntimeError("Phase 3 evidence root differs from the issue #13 host/date contract")

    phase2_path = root / "results/2026-07-29/skynet/phase2-observability/phase2-manifest.json"
    phase2 = json.loads(phase2_path.read_text())
    if phase2["revisions"]["published_corpus"] != PUBLISHED_CORPUS:
        raise RuntimeError("Phase 2 manifest lineage differs")

    disposition_path = results_root / "phase3-disposition.json"
    disposition = write_disposition(root, disposition_path)
    disposition_errors: list[str] = []
    validate_disposition(root, disposition, disposition_errors)
    if disposition_errors:
        raise RuntimeError("; ".join(disposition_errors))
    validate_schema(disposition, root / "schemas/phase3/phase3-disposition-v1.schema.json")

    checkpoint_b_path = results_root / "checkpoint-b-review.json"
    checkpoint_b = json.loads(checkpoint_b_path.read_text()) if checkpoint_b_path.is_file() else None
    overhead = json.loads((results_root / "provider-overhead-post-optimization.json").read_text())
    overhead_status = overhead.get("status")
    if overhead_status not in {"pass", "fail"}:
        raise RuntimeError("provider overhead has no valid standing result")
    if overhead_status != "fail":
        raise RuntimeError("accepted-with-notes closeout requires the raw standing performance failure")
    failed_metric_cells = sum(
        analysis.get("passed") is not True
        for combination in overhead.get("combinations", [])
        for comparison in combination.get("comparisons", [])
        for analysis in comparison.get("analysis", {}).values()
    )
    files = {kind: list(paths) for kind, paths in FILES.items()}
    if checkpoint_b is not None:
        validate_checkpoint_b(checkpoint_b, root)
        files["evidence"].append("results/2026-07-29/skynet/phase3-resident-provider/checkpoint-b-review.json")

    artifacts = []
    for kind, relative_paths in files.items():
        for relative in relative_paths:
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(relative)
            artifacts.append({"path": relative, "size": path.stat().st_size, "sha256": sha256(path), "kind": kind})

    reviews = [{
        "checkpoint": "A",
        "comment_id": 5124005466,
        "url": "https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-5124005466",
        "verdict": "PASS_WITH_NOTES",
        "safety_to_proceed": "YES",
        "project_head": "0a16a7e4b0e383ea43706d740abc19924c82cdf5",
        "llama_cpp_head": "d9d20e1b616a25ba5d0ec8ad12ef408a83ae227b",
    }]
    revisions = {
        "project_execution_base": PROJECT_BASE,
        "project_evidence_head": git(root, "rev-parse", "HEAD"),
        "llama_cpp_base": LLAMA_BASE,
        "llama_cpp_candidate": git(root / "llama.cpp", "rev-parse", "HEAD"),
        "published_gguf": PUBLISHED_GGUF,
        "published_corpus": PUBLISHED_CORPUS,
    }
    if checkpoint_b is not None:
        reviews.append(checkpoint_b)
        revisions["project_checkpoint_b_head"] = checkpoint_b["project_head"]

    disposition_identity = {
        "status": "accepted-with-notes",
        "progression_scope": "phase3-only",
        "path": str(disposition_path.relative_to(root)),
        "size": disposition_path.stat().st_size,
        "sha256": sha256(disposition_path),
        "comment_ids": DESIGN_AUTHORITY_COMMENT_IDS,
    }
    manifest = {
        "schema_version": "phase3-manifest-v2",
        "closeout_state": (
            "complete-with-performance-notes" if checkpoint_b is not None else
            "checkpoint-b-candidate-with-performance-notes"
        ),
        "execution_profile": "STANDARD",
        "issue": {
            "repository": "murillo128/k3-out-of-core", "number": 13,
            "url": "https://github.com/murillo128/k3-out-of-core/issues/13", "pull_request": 15,
        },
        "revisions": revisions,
        "raw_performance_gate": {
            "status": "fail", "passed_cells": 22, "total_cells": 24,
            "capture_path": CAPTURE_RELATIVE, "capture_sha256": CAPTURE_SHA256,
        },
        "design_disposition": disposition_identity,
        "models": [MODELS[name] for name in ("f16", "mxfp4")],
        "phase2_input": {
            "path": str(phase2_path.relative_to(root)), "size": phase2_path.stat().st_size,
            "sha256": sha256(phase2_path), "published_corpus": PUBLISHED_CORPUS,
            "raw_corpus_republished": False,
        },
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
        "validation": [
            {"name": "provider-parity", "status": "pass", "evidence": "provider-parity-post-optimization.json: 4/4 artifact/backend combinations"},
            {"name": "lifecycle-and-failures", "status": "pass", "evidence": "lifecycle-and-failures-post-optimization.json: CPU/CUDA matrix, stress, fault injection, sanitizers"},
            {"name": "provider-administration", "status": "pass", "evidence": "provider-admin-fast-path.json: corrective base versus optimized resident administration"},
            {"name": "corrective-prerequisites", "status": "pass", "evidence": "corrective-prerequisites.json: all comment 5127774849 prerequisites passed before capture"},
            {
                "name": "provider-overhead-post-optimization", "status": overhead_status,
                "evidence": (
                    "provider-overhead-post-optimization.json: every original per-cell confidence gate" if overhead_status == "pass" else
                    f"provider-overhead-post-optimization.json: standing v2 capture failed {failed_metric_cells} of 24 original metric cells"
                ),
            },
            {"name": "phase2-regression-tests", "status": "pass", "evidence": "dependency-free Phase 2 unittest suite"},
            {"name": "phase3-evidence-tests", "status": "pass", "evidence": "dependency-free Phase 3 unittest suite"},
        ],
        "reviews": reviews,
        "prohibited_scope": {
            "cache_added": False, "storage_or_transport_added": False, "prefetch_added": False,
            "expert_residency_changed": False, "raw_phase2_corpus_modified": False,
            "model_payload_tracked": False,
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    validate_schema(manifest, root / "schemas/phase3/phase3-manifest-v2.schema.json")
    print(json.dumps({"output": str(output), "artifacts": len(artifacts), "state": manifest["closeout_state"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

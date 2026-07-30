#!/usr/bin/env python3
"""Build the deterministic issue #13 Phase 3 closeout manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import LLAMA_BASE, MODELS, PROJECT_BASE, PUBLISHED_CORPUS, PUBLISHED_GGUF, git, sha256


FILES = {
    "schema": ["schemas/phase3/phase3-manifest-v1.schema.json"],
    "implementation": [
        "scripts/phase2/capture_route_observer.py",
        "scripts/phase2/overhead_probe.cpp",
        "scripts/phase2/route_probe.cpp",
        "scripts/phase3/common.py",
        "scripts/phase3/overhead_probe_baseline.cpp",
        "scripts/phase3/capture_provider_parity.py",
        "scripts/phase3/run_provider_lifecycle.py",
        "scripts/phase3/measure_provider_overhead.py",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    results_root = args.results_root.resolve()
    expected_results = root / "results/2026-07-29/skynet/phase3-resident-provider"
    if results_root != expected_results:
        raise RuntimeError("Phase 3 evidence root differs from the issue #13 host/date contract")

    phase2_path = root / "results/2026-07-29/skynet/phase2-observability/phase2-manifest.json"
    phase2 = json.loads(phase2_path.read_text())
    if phase2["revisions"]["published_corpus"] != PUBLISHED_CORPUS:
        raise RuntimeError("Phase 2 manifest lineage differs")

    checkpoint_b_path = results_root / "checkpoint-b-review.json"
    checkpoint_b = json.loads(checkpoint_b_path.read_text()) if checkpoint_b_path.is_file() else None
    overhead = json.loads((results_root / "provider-overhead.json").read_text())
    overhead_status = overhead.get("status")
    if overhead_status not in {"pass", "fail"}:
        raise RuntimeError("provider overhead has no valid standing result")
    if checkpoint_b is not None and overhead_status != "pass":
        raise RuntimeError("a failed performance gate cannot bind an accepted Checkpoint B")
    failed_metric_cells = sum(
        analysis.get("passed") is not True
        for combination in overhead.get("combinations", [])
        for comparison in combination.get("comparisons", [])
        for analysis in comparison.get("analysis", {}).values()
    )
    files = {kind: list(paths) for kind, paths in FILES.items()}
    if checkpoint_b is not None:
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
        if checkpoint_b.get("checkpoint") != "B" or checkpoint_b.get("safety_to_proceed") != "YES":
            raise RuntimeError("Checkpoint B attestation is not accepted")
        reviews.append(checkpoint_b)
        revisions["project_checkpoint_b_head"] = checkpoint_b["project_head"]

    manifest = {
        "schema_version": "phase3-manifest-v1",
        "closeout_state": (
            "complete" if checkpoint_b is not None else
            "checkpoint-b-candidate" if overhead_status == "pass" else
            "performance-gate-failed"
        ),
        "execution_profile": "STANDARD",
        "issue": {
            "repository": "murillo128/k3-out-of-core", "number": 13,
            "url": "https://github.com/murillo128/k3-out-of-core/issues/13", "pull_request": 15,
        },
        "revisions": revisions,
        "models": [MODELS[name] for name in ("f16", "mxfp4")],
        "phase2_input": {
            "path": str(phase2_path.relative_to(root)), "size": phase2_path.stat().st_size,
            "sha256": sha256(phase2_path), "published_corpus": PUBLISHED_CORPUS,
            "raw_corpus_republished": False,
        },
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
        "validation": [
            {"name": "provider-parity", "status": "pass", "evidence": "provider-parity.json: 4/4 artifact/backend combinations"},
            {"name": "lifecycle-and-failures", "status": "pass", "evidence": "lifecycle-and-failures.json: CPU/CUDA matrix, stress, fault injection, sanitizers"},
            {
                "name": "provider-overhead", "status": overhead_status,
                "evidence": (
                    "provider-overhead.json: every predeclared per-cell confidence gate" if overhead_status == "pass" else
                    f"provider-overhead.json: standing final capture failed {failed_metric_cells} of 24 predeclared metric cells"
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
    print(json.dumps({"output": str(output), "artifacts": len(artifacts), "state": manifest["closeout_state"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

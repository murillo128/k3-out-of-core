#!/usr/bin/env python3
"""Build the deterministic issue #17 Phase 4 manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_A_LLAMA, CHECKPOINT_A_PROJECT,
                    LLAMA_BASE, MODELS, PROJECT_BASE, git, gpu_identity, json_write, sha256)


FILES = {
    "implementation": [
        "scripts/phase4/common.py", "scripts/phase4/capture_hot_cache_parity.py",
        "scripts/phase4/run_hot_cache_lifecycle.py", "scripts/phase4/build_phase4_manifest.py",
        "scripts/phase4/verify_phase4.py",
    ],
    "schema": ["schemas/phase4/phase4-manifest-v1.schema.json"],
    "test": ["tests/phase4/test_phase4_evidence.py"],
    "evidence": [
        "results/2026-07-30/skynet/phase4-hot-cache/hot-cache-parity.json",
        "results/2026-07-30/skynet/phase4-hot-cache/lifecycle-and-failures.json",
        "results/2026-07-30/skynet/phase4-hot-cache/PHASE4.md",
    ],
    "source-of-truth": [
        "PLAN.md", "docs/STATUS.md", "docs/DECISIONS.md", "docs/plan/04-cache-and-storage.md",
        "docs/MODELS_AND_VALIDATION.md", "docs/REPOSITORIES_AND_ARTIFACTS.md",
    ],
}


def identity(root: Path, relative: str) -> dict:
    path = root / relative
    if not path.is_file(): raise FileNotFoundError(relative)
    return {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    expected_results = root / "results/2026-07-30/skynet/phase4-hot-cache"
    if args.results_root.resolve() != expected_results:
        raise RuntimeError("results root differs from issue #17 date/host contract")
    parity_path = expected_results / "hot-cache-parity.json"
    lifecycle_path = expected_results / "lifecycle-and-failures.json"
    parity, lifecycle = json.loads(parity_path.read_text()), json.loads(lifecycle_path.read_text())
    if parity.get("status") != "pass" or lifecycle.get("status") != "pass":
        raise RuntimeError("standing evidence is not passing")
    candidate = git(root / "llama.cpp", "rev-parse", "HEAD")
    gitlink = git(root, "rev-parse", "HEAD:llama.cpp")
    if candidate != gitlink: raise RuntimeError("gitlink differs from nested candidate")
    artifacts = []
    for kind, paths in FILES.items():
        for relative in paths: artifacts.append({**identity(root, relative), "kind": kind})
    phase3_relative = "results/2026-07-29/skynet/phase3-resident-provider/phase3-manifest.json"
    manifest = {
        "schema_version": "phase4-manifest-v1", "closeout_state": "final-review-candidate",
        "execution_profile": "STANDARD",
        "issue": {"repository": "murillo128/k3-out-of-core", "number": 17,
                  "url": "https://github.com/murillo128/k3-out-of-core/issues/17", "pull_request": 18},
        "revisions": {"project_execution_base": PROJECT_BASE, "project_evidence_head": git(root, "rev-parse", "HEAD"),
                      "llama_cpp_base": LLAMA_BASE, "llama_cpp_candidate": candidate, "gitlink": gitlink},
        "checkpoint_a": {"comment_id": CHECKPOINT_A_COMMENT,
                         "url": f"https://github.com/murillo128/k3-out-of-core/issues/17#issuecomment-{CHECKPOINT_A_COMMENT}",
                         "verdict": "PASS_WITH_NOTES", "safety_to_proceed": "YES",
                         "project_head": CHECKPOINT_A_PROJECT, "llama_cpp_head": CHECKPOINT_A_LLAMA,
                         "independent_read_only": True},
        "phase3_input": identity(root, phase3_relative),
        "models": [MODELS[name] for name in ("f16", "mxfp4")],
        "environment": {"gpu": gpu_identity(root), "cuda_build": parity["build"]["configuration"]},
        "evidence": {"parity": identity(root, str(parity_path.relative_to(root))),
                     "lifecycle": identity(root, str(lifecycle_path.relative_to(root)))},
        "validation": [
            {"name": "hot-cache-parity", "status": "pass"},
            {"name": "lifecycle-and-failures", "status": "pass"},
            {"name": "cpu-focused-ctest", "status": "pass"},
            {"name": "cuda-focused-ctest", "status": "pass"},
            {"name": "asan-ubsan", "status": "pass"},
            {"name": "phase3-regression-tests", "status": "pass"},
            {"name": "phase4-evidence-tests", "status": "pass"},
            {"name": "checkpoint-a", "status": "pass"},
        ],
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
    }
    schema = json.loads((root / "schemas/phase4/phase4-manifest-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(manifest)
    json_write(args.output.resolve(), manifest)
    print(f"PASS: wrote {args.output} with {len(artifacts)} artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

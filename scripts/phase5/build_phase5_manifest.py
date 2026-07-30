#!/usr/bin/env python3
"""Build the deterministic issue #20 Phase 5 manifest."""

from __future__ import annotations
import argparse, json
from pathlib import Path
from jsonschema import Draft202012Validator
from common import (CHECKPOINT_A_COMMENT, CHECKPOINT_A_LLAMA, CHECKPOINT_A_PROJECT,
                    LLAMA_BASE, MODELS, PHASE4_MANIFEST, PROJECT_BASE, git,
                    gpu_identity, json_write, sha256)

FILES = {
    "implementation": ["scripts/phase5/common.py", "scripts/phase5/capture_transfer_ring.py",
        "scripts/phase5/capture_cold_cache_parity.py", "scripts/phase5/run_cold_cache_lifecycle.py",
        "scripts/phase5/capture_validation_results.py", "scripts/phase5/build_phase5_manifest.py",
        "scripts/phase5/verify_phase5.py"],
    "schema": ["schemas/phase5/phase5-manifest-v1.schema.json"],
    "test": ["tests/phase5/test_phase5_evidence.py"],
    "evidence": ["results/2026-07-30/skynet/phase5-cold-cache/transfer-ring.json",
        "results/2026-07-30/skynet/phase5-cold-cache/cold-cache-parity.json",
        "results/2026-07-30/skynet/phase5-cold-cache/lifecycle-and-failures.json",
        "results/2026-07-30/skynet/phase5-cold-cache/validation-results.json",
        "results/2026-07-30/skynet/phase5-cold-cache/PHASE5.md"],
    "source-of-truth": ["PLAN.md", "docs/STATUS.md", "docs/plan/04-cache-and-storage.md",
        "docs/MODELS_AND_VALIDATION.md", "docs/REPOSITORIES_AND_ARTIFACTS.md"],
}

def identity(root: Path, relative: str) -> dict:
    path = root / relative
    if not path.is_file(): raise FileNotFoundError(relative)
    return {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--results-root", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); root = args.project_root.resolve()
    result_root = root / "results/2026-07-30/skynet/phase5-cold-cache"
    if args.results_root.resolve() != result_root: raise RuntimeError("results root differs from issue #20 contract")
    paths = {"transfer_ring": result_root / "transfer-ring.json", "parity": result_root / "cold-cache-parity.json",
             "lifecycle": result_root / "lifecycle-and-failures.json", "validation_commands": result_root / "validation-results.json"}
    loaded = {name: json.loads(path.read_text()) for name, path in paths.items()}
    if any(value.get("status") != "pass" for value in loaded.values()): raise RuntimeError("standing evidence is not passing")
    nested = git(root / "llama.cpp", "rev-parse", "HEAD"); gitlink = git(root, "rev-parse", "HEAD:llama.cpp")
    if nested != gitlink: raise RuntimeError("gitlink differs from nested candidate")
    artifacts = [{**identity(root, path), "kind": kind} for kind, values in FILES.items() for path in values]
    phase4 = identity(root, PHASE4_MANIFEST)
    manifest = {"schema_version": "phase5-manifest-v1", "closeout_state": "final-review-candidate",
        "execution_profile": "STANDARD", "issue": {"repository": "murillo128/k3-out-of-core", "number": 20,
            "url": "https://github.com/murillo128/k3-out-of-core/issues/20", "pull_request": 21},
        "revisions": {"project_execution_base": PROJECT_BASE, "project_evidence_head": git(root, "rev-parse", "HEAD"),
            "llama_cpp_base": LLAMA_BASE, "llama_cpp_candidate": nested, "gitlink": gitlink},
        "checkpoint_a": {"comment_id": CHECKPOINT_A_COMMENT,
            "url": f"https://github.com/murillo128/k3-out-of-core/issues/20#issuecomment-{CHECKPOINT_A_COMMENT}",
            "verdict": "PASS", "safety_to_proceed": "YES", "project_head": CHECKPOINT_A_PROJECT,
            "llama_cpp_head": CHECKPOINT_A_LLAMA, "independent_read_only": True},
        "phase4_input": phase4, "models": [MODELS[name] for name in ("f16", "mxfp4")],
        "environment": {"gpu": gpu_identity(root), "cuda_build": loaded["parity"]["build"]["configuration"]},
        "evidence": {name: identity(root, str(path.relative_to(root))) for name, path in paths.items()},
        "validation": loaded["validation_commands"]["commands"],
        "deferred": ["Phase 7: dedicated transfer streams/events and H2D/compute overlap",
                     "Phase 8: CPU miss execution and CPU_FALLBACK output equivalence"],
        "carried_notes": ["Fixed ordinary-prompt tokenizer limitation remains visible.",
                          "Phase 3 standing performance remains a raw 22/24 pass under D-018."],
        "artifacts": sorted(artifacts, key=lambda item: item["path"])}
    schema = json.loads((root / "schemas/phase5/phase5-manifest-v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema); Draft202012Validator(schema).validate(manifest)
    json_write(args.output.resolve(), manifest); print(f"PASS: wrote {args.output} with {len(artifacts)} artifacts"); return 0

if __name__ == "__main__": raise SystemExit(main())

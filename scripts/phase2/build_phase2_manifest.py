#!/usr/bin/env python3
"""Build the deterministic issue #10 Phase 2 closeout manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


FILES = {
    "schema": [
        "schemas/phase2/route-trace-v1.md",
        "schemas/phase2/expert-storage-map-v1.md",
        "schemas/phase2/expert-storage-map-v1.schema.json",
        "schemas/phase2/cache-simulation-v1.md",
        "schemas/phase2/cache-simulation-manifest-v1.schema.json",
        "schemas/phase2/cache-simulation-output-v1.schema.json",
        "schemas/phase2/phase2-manifest-v1.schema.json",
    ],
    "implementation": [
        "scripts/phase2/route_trace.py",
        "scripts/phase2/expert_storage_map.py",
        "scripts/phase2/cache_simulator.py",
        "scripts/phase2/run_cache_simulation.py",
        "scripts/phase2/capture_trace_corpus.py",
        "scripts/phase2/simulate_trace_corpus.py",
        "scripts/phase2/build_phase2_manifest.py",
        "scripts/phase2/verify_phase2.py",
    ],
    "test": [
        "tests/phase2/test_route_trace.py",
        "tests/phase2/test_expert_storage_map.py",
        "tests/phase2/test_cache_simulator.py",
        "tests/phase2/test_route_trace_writer.cpp",
    ],
    "fixture": [
        "tests/fixtures/phase2/k3-f16-cpu-route-v1.bin",
        "tests/fixtures/phase2/cache-simulator-reference-v1.json",
    ],
    "prompt": ["corpus/phase2/prompts-v1.json"],
    "evidence": [
        "results/2026-07-29/skynet/phase2-observability/PHASE1.md",
        "results/2026-07-29/skynet/phase2-observability/PHASE2.md",
        "results/2026-07-29/skynet/phase2-observability/PHASE3.md",
        "results/2026-07-29/skynet/phase2-observability/PHASE4.md",
        "results/2026-07-29/skynet/phase2-observability/phase1-route-validation.json",
        "results/2026-07-29/skynet/phase2-observability/phase1-disabled-overhead.json",
        "results/2026-07-29/skynet/phase2-observability/phase1-numerical/inference.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-f16-expert-storage-map-v1.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-mxfp4-expert-storage-map-v1.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-route-regression.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-storage-validation.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-numerical/inference.json",
        "results/2026-07-29/skynet/phase2-observability/phase2-trace-enabled-performance.json",
        "results/2026-07-29/skynet/phase2-observability/phase3-simulation-manifest-v1.json",
        "results/2026-07-29/skynet/phase2-observability/phase3-f16-reference-simulation.json",
        "results/2026-07-29/skynet/phase2-observability/phase4-corpus-capture.json",
        "results/2026-07-29/skynet/phase2-observability/phase4-corpus-publication.json",
        "results/2026-07-29/skynet/phase2-observability/phase4-corpus-simulations.json",
    ],
    "source-of-truth": [
        "docs/STATUS.md",
        "docs/plan/00-foundation.md",
        "docs/MODELS_AND_VALIDATION.md",
        "docs/REPOSITORIES_AND_ARTIFACTS.md",
    ],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()

    publication_path = root / "results/2026-07-29/skynet/phase2-observability/phase4-corpus-publication.json"
    capture_path = root / "results/2026-07-29/skynet/phase2-observability/phase4-corpus-capture.json"
    publication = json.loads(publication_path.read_text())
    capture = json.loads(capture_path.read_text())
    artifacts = []
    for kind, paths in FILES.items():
        for relative in paths:
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(relative)
            artifacts.append(
                {"path": relative, "size": path.stat().st_size, "sha256": sha256(path), "kind": kind}
            )

    manifest = {
        "schema_version": "phase2-manifest-v1",
        "closeout_state": "complete",
        "execution_profile": "STANDARD",
        "issue": {
            "repository": "murillo128/k3-out-of-core",
            "number": 10,
            "url": "https://github.com/murillo128/k3-out-of-core/issues/10",
            "pull_request": 11,
        },
        "revisions": {
            "project_execution_base": "c0ef5d08c6efb8d1f7a08a62109feb1a488c72fa",
            "project_checkpoint_a_head": "43216235b6e74914afdb1b76918557675bf7e0b1",
            "project_phase3_head": "8aeee910ce6da4e10dfff4ede2395680580d1e7a",
            "project_checkpoint_b_head": "961e2f44413ec2031497dcc1474e8e79b828e6cb",
            "llama_cpp_base": "84245db4c790af22135f34992689edcc11877003",
            "llama_cpp_route_observer": "92c4627e19219134ed42e24aa84a1514bf3dffa3",
            "llama_cpp_storage_metadata": "4daaaa1a4dd26d6465f84891b854b5f7ddc03020",
            "published_gguf": "88de02cf8fa37f87eb06daaed370ac9c3411d5ca",
            "published_corpus": publication["published_revision"],
        },
        "models": [
            {
                "name": "Kimi-K3-0.40B-F16.gguf",
                "size": 784318432,
                "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
                "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
            },
            {
                "name": "Kimi-K3-0.40B-MXFP4.gguf",
                "size": 751976576,
                "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
                "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
            },
        ],
        "artifacts": sorted(artifacts, key=lambda item: item["path"]),
        "external_archive": {
            "repository": publication["repository"],
            "revision": publication["published_revision"],
            "path": publication["archive"]["path"],
            "size": publication["archive"]["size"],
            "sha256": publication["archive"]["sha256"],
            "members": capture["archive"]["members"],
        },
        "validation": [
            {
                "name": "phase2-unit-tests",
                "status": "pass",
                "evidence": "20/20 dependency-free unittest cases",
            },
            {
                "name": "route-regression",
                "status": "pass",
                "evidence": "phase2-route-regression.json",
            },
            {
                "name": "numerical-parity",
                "status": "pass",
                "evidence": "phase2-numerical/inference.json",
            },
            {
                "name": "storage-maps",
                "status": "pass",
                "evidence": "phase2-storage-validation.json",
            },
            {
                "name": "trace-enabled-performance",
                "status": "pass",
                "evidence": "phase2-trace-enabled-performance.json",
            },
            {
                "name": "simulator-reference",
                "status": "pass",
                "evidence": "phase3-f16-reference-simulation.json",
            },
            {
                "name": "corpus-repeatability-and-parity",
                "status": "pass",
                "evidence": "phase4-corpus-capture.json",
            },
            {
                "name": "corpus-publication",
                "status": "pass",
                "evidence": "phase4-corpus-publication.json",
            },
            {
                "name": "corpus-simulation",
                "status": "pass",
                "evidence": "phase4-corpus-simulations.json",
            },
        ],
        "reviews": [
            {
                "checkpoint": "A",
                "comment_id": 5122694227,
                "url": "https://github.com/murillo128/k3-out-of-core/issues/10#issuecomment-5122694227",
                "verdict": "PASS_WITH_NOTES",
                "safety_to_proceed": "YES",
                "project_head": "43216235b6e74914afdb1b76918557675bf7e0b1",
                "llama_cpp_head": "4daaaa1a4dd26d6465f84891b854b5f7ddc03020",
            },
            {
                "checkpoint": "B",
                "comment_id": 5123025188,
                "url": "https://github.com/murillo128/k3-out-of-core/issues/10#issuecomment-5123025188",
                "verdict": "PASS_WITH_NOTES",
                "safety_to_proceed": "YES",
                "project_head": "961e2f44413ec2031497dcc1474e8e79b828e6cb",
                "llama_cpp_head": "4daaaa1a4dd26d6465f84891b854b5f7ddc03020",
            },
        ],
        "prohibited_scope": {
            "runtime_provider_or_cache_added": False,
            "raw_corpus_tracked": False,
            "model_payload_tracked": False,
        },
    }
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "artifacts": len(artifacts)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

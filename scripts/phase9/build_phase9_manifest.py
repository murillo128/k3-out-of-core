#!/usr/bin/env python3
"""Build the non-circular Phase 9 technical manifest from exact frozen inputs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity  # noqa: E402


def git(path: Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *arguments], text=True).strip()


def repository_identity(path: Path) -> dict[str, Any]:
    identity = file_identity(path.resolve())
    identity["path"] = str(path.resolve().relative_to(ROOT))
    return identity


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation-head", required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--checkpoint-evidence", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--residency", type=Path, required=True)
    parser.add_argument("--waste", type=Path, required=True)
    parser.add_argument("--statistics", type=Path, required=True)
    parser.add_argument("--prefill-protection", type=Path, required=True)
    parser.add_argument("--policy-benchmark", type=Path, required=True)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--online-boundaries", type=Path, required=True)
    parser.add_argument("--default-equivalence", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if git(ROOT, "rev-parse", "HEAD") != args.implementation_head:
        raise RuntimeError("manifest must be built while checked out at the exact implementation evidence head")
    selection = json.loads(args.selection.read_text())
    validation = json.loads(args.validation.read_text())
    evidence_paths = {
        "replay_online_checkpoint": args.checkpoint_evidence, "working_sets": args.working_sets,
        "residency": args.residency, "waste": args.waste, "statistics": args.statistics,
        "prefill_protection": args.prefill_protection, "policy_benchmark": args.policy_benchmark,
        "transport": args.transport, "online_boundaries": args.online_boundaries,
        "selection": args.selection, "default_equivalence": args.default_equivalence,
        "validation": args.validation,
    }
    phase8 = ROOT / "results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json"
    phase2 = ROOT / "results/2026-07-29/skynet/phase2-observability/phase2-manifest.json"
    manifest: dict[str, Any] = {
        "schema_version": "phase9-manifest-v1", "status": "pass",
        "project": {"repository": "murillo128/k3-out-of-core", "branch": "codex/phase9-cache-policy",
                    "pull_request": 31, "implementation_evidence_head": args.implementation_head,
                    "base": "17a4e5be38a4820984a7bd4d3082695d8822c9ba",
                    "closeout_allowed_paths": [
                        "results/2026-07-31/skynet/phase9-cache-policy/phase9-manifest.json",
                        "results/2026-07-31/skynet/phase9-cache-policy/PHASE9.md"]},
        "nested": {"repository": "murillo128/llama.cpp", "head": git(ROOT / "llama.cpp", "rev-parse", "HEAD"),
                   "gitlink": git(ROOT, "rev-parse", "HEAD:llama.cpp"),
                   "base": "dc4d50c68378d908131b518662160fdd08f4e005"},
        "inputs": {"phase2_manifest": repository_identity(phase2),
                   "phase8_manifest": repository_identity(phase8),
                   "issue": 30, "execution_profile": "STANDARD"},
        "checkpoints": {
            "A": {"comment": 5148012128, "verdict": "PASS", "safety": "YES",
                  "project_head": "35348d78022419fa14296d5bc4c3e87d28242914",
                  "nested_head": "fb691026b3cc3a75842b1d0432e4f8449e078243"},
            "B": {"comment": 5148752231, "verdict": "PASS", "safety": "YES",
                  "project_head": "91446113847fbb4fd382e1694241ca0c54dae020",
                  "nested_head": "665ae2af902c8ceab8444b77c964ed2af461405b"},
            "C": validation["checkpoint_c"],
        },
        "replay": json.loads(args.checkpoint_evidence.read_text())["offline_replay"],
        "online": json.loads(args.checkpoint_evidence.read_text())["online_replay"],
        "working_sets": json.loads(args.working_sets.read_text()),
        "evidence": {name: file_identity(path) for name, path in evidence_paths.items()},
        "selection": selection,
        "verification": validation,
        "environment": validation["environment"],
        "technical_closeout_state": "complete-with-global-lru-retained" if selection["selected"]["hot"] == "LRU-GLOBAL"
                                    else "complete-with-evidence-selected-default",
        "evidence_limits": selection["limits"],
    }
    schema = json.loads((ROOT / "schemas/phase9/phase9-manifest-v1.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(manifest))
    print(canonical_json({"status": "pass", "output": str(args.output)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

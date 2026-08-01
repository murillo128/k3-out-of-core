#!/usr/bin/env python3
"""Strictly verify the non-circular Phase 9 manifest and frozen artifacts."""

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
from evidence_common import canonical_json, sha256_file  # noqa: E402


def git(path: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(["git", "-C", str(path), *arguments], capture_output=True, text=True, check=check)
    return completed.stdout.strip()


def resolve(path: str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def verify_identity(identity: dict[str, Any]) -> None:
    path = resolve(identity["path"])
    if not path.is_file(): raise RuntimeError(f"artifact is absent: {path}")
    if path.stat().st_size != identity["size"]: raise RuntimeError(f"artifact size changed: {path}")
    if sha256_file(path) != identity["sha256"]: raise RuntimeError(f"artifact digest changed: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text())
    schema = json.loads((ROOT / "schemas/phase9/phase9-manifest-v1.schema.json").read_text())
    jsonschema.validate(manifest, schema)
    serialized = canonical_json(manifest)
    for forbidden in ("manifest_sha256", "containing_commit", "final_review"):
        if forbidden in serialized: raise RuntimeError(f"circular manifest field is forbidden: {forbidden}")
    for identity in manifest["inputs"].values():
        if isinstance(identity, dict) and {"path", "size", "sha256"} <= identity.keys(): verify_identity(identity)
    for identity in manifest["evidence"].values(): verify_identity(identity)
    if not all(manifest["checkpoints"][name]["safety"] == "YES" for name in ("A", "B", "C")):
        raise RuntimeError("checkpoint safety is not YES")
    selection_project = manifest["selection"]["candidate_heads"]["project"]
    selection_nested = manifest["selection"]["candidate_heads"]["nested"]
    implementation = manifest["project"]["implementation_evidence_head"]
    if subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor",
                       selection_project, implementation]).returncode != 0:
        raise RuntimeError("Phase 9.4 project selection is not an ancestor of implementation evidence")
    if subprocess.run(["git", "-C", str(ROOT / "llama.cpp"), "merge-base", "--is-ancestor",
                       selection_nested, manifest["nested"]["head"]]).returncode != 0:
        raise RuntimeError("Phase 9.4 nested selection is not an ancestor of the final default-resolution head")
    if args.strict:
        current = git(ROOT, "rev-parse", "HEAD")
        if subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", implementation, current]).returncode != 0:
            raise RuntimeError("implementation evidence head is not an ancestor of current HEAD")
        allowed = {str(manifest_path.relative_to(ROOT)),
                   "results/2026-07-31/skynet/phase9-cache-policy/PHASE9.md"}
        changed = set(filter(None, git(ROOT, "diff", "--name-only", f"{implementation}..{current}").splitlines()))
        if not changed <= allowed: raise RuntimeError(f"closeout commit changed non-derived files: {sorted(changed - allowed)}")
        if git(ROOT, "status", "--short") or git(ROOT / "llama.cpp", "status", "--short"):
            raise RuntimeError("strict verification requires clean project and nested worktrees")
        if git(ROOT / "llama.cpp", "rev-parse", "HEAD") != manifest["nested"]["head"]:
            raise RuntimeError("nested HEAD changed after implementation evidence")
    print(canonical_json({"status": "pass", "manifest": str(manifest_path),
                          "sha256": sha256_file(manifest_path), "strict": args.strict}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

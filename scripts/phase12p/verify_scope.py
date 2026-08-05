#!/usr/bin/env python3
"""Fail closed when a Phase 12P branch escapes its exact top-level allowlist."""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = ("scripts/phase12p/", "schemas/phase12p/", "tests/phase12p/", "results/2026-08-05/msi-edgexpert-gb10/phase12p/")


def git(*arguments: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def verify(base: str) -> list[str]:
    changed = set(filter(None, git("diff", "--name-only", f"{base}...HEAD").splitlines()))
    changed.update(filter(None, git("diff", "--name-only").splitlines()))
    changed.update(filter(None, git("ls-files", "--others", "--exclude-standard").splitlines()))
    invalid = sorted(path for path in changed if not path.startswith(ALLOWED))
    if invalid:
        raise ValueError(f"out-of-scope paths: {invalid}")
    expected_gitlink = git("rev-parse", f"{base}:llama.cpp")
    if git("rev-parse", "HEAD:llama.cpp") != expected_gitlink:
        raise ValueError("gitlink delta relative to base")
    if git("status", "--porcelain", cwd=ROOT / "llama.cpp"):
        raise ValueError("nested worktree is dirty")
    for relative in changed:
        path = ROOT / relative
        if path.is_file() and path.stat().st_size > 10 * (1 << 20):
            raise ValueError(f"unbounded committed artifact: {relative}")
        if relative.endswith((".bin", ".gguf")):
            raise ValueError(f"generated corpus/binary payload in branch: {relative}")
    return sorted(changed)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--base", required=True)
    args = parser.parse_args(); changed = verify(args.base); print(f"phase12p scope verified: {len(changed)} allowed paths"); return 0


if __name__ == "__main__":
    raise SystemExit(main())

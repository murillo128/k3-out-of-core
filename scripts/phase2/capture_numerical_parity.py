#!/usr/bin/env python3
"""Reuse the Phase 1 numerical gate against an exact Phase 2 llama.cpp commit."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--llama-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", default="inference.json")
    args = parser.parse_args()

    if len(args.llama_revision) != 40 or any(
        character not in "0123456789abcdef" for character in args.llama_revision
    ):
        raise SystemExit("llama revision must be a full lowercase commit SHA")

    root = args.repo_root.resolve()
    module_path = root / "scripts/phase1/capture_inference.py"
    spec = importlib.util.spec_from_file_location("phase1_capture_inference", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit("could not load the Phase 1 inference gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.LLAMA_CPP_COMMIT = args.llama_revision

    output_dir = args.output_dir if args.output_dir.is_absolute() else root / args.output_dir
    document = module.capture(root, output_dir)
    document["phase2_reuse"] = {
        "source_gate": "scripts/phase1/capture_inference.py",
        "llama_cpp_commit_override": args.llama_revision,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / args.output
    output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output_path), "status": document["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

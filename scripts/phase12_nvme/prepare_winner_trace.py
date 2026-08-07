#!/usr/bin/env python3
"""Prepare exact plan, command, and case identity for the issue #58 winner trace."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from plan import build_plan, encode_plan  # noqa: E402
from qualify_harness import sha256_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--cell-output", type=Path, required=True)
    parser.add_argument("--command-json", type=Path, required=True)
    parser.add_argument("--case-json", type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    corpus = args.corpus.resolve()
    plan = args.plan.resolve()
    cell_output = args.cell_output.resolve()
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_bytes(encode_plan(build_plan(corpus, "A", "COLD_SPREAD", 0, "LOGICAL_SELECTED")))
    command = [
        str(binary), "--plan", str(plan), "--api", "direct-pread",
        "--cache-state", "OS_COLD_VERIFIED", "--qd", "32", "--iterations", "1",
        "--output", str(cell_output),
    ]
    command_document = {"command": command, "environment": {}}
    case_document = {
        "schema_version": "phase12-nvme-winner-trace-case-v1",
        "winner": "SINGLE_NVME_LAYOUT_A_LOGICAL_DIRECT_PREAD_QD32",
        "layout": "A",
        "submission_order": "LOGICAL_SELECTED",
        "api": "direct-pread",
        "requested_qd": 32,
        "cache_state": "OS_COLD_VERIFIED",
        "request_class": "COLD_SPREAD",
        "route_token": 0,
        "iterations": 1,
        "plan": {"path": str(plan), "sha256": sha256_file(plan)},
        "binary": {"path": str(binary), "sha256": sha256_file(binary)},
        "corpus_generation_sha256": sha256_file(corpus / "generation.json"),
    }
    args.command_json.parent.mkdir(parents=True, exist_ok=True)
    args.command_json.write_text(json.dumps(command_document, indent=2, sort_keys=True) + "\n")
    args.case_json.write_text(json.dumps(case_document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "case": case_document}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

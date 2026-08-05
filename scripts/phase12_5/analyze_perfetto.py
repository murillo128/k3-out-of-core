#!/usr/bin/env python3
"""Run the pinned Phase 12.5 SQL package against one verified trace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import ROOT, file_identity, query_trace, trace_processor_version, write_json

QUERIES = (
    "token_critical_path.sql",
    "storage_queue_service.sql",
    "cpu_runqueue.sql",
    "cuda_activity.sql",
    "overlap_and_idle.sql",
)


def typed(value: str):
    if value == "[NULL]":
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True)
    parser.add_argument("--case-name", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    verification = json.loads(args.verification.read_text())
    if verification.get("status") != "valid":
        raise ValueError("analysis requires a valid verification document")
    sql_root = ROOT / "scripts/phase12_5/sql"
    outputs = {}
    identities = []
    for name in QUERIES:
        path = sql_root / name
        rows = query_trace(args.trace_processor, args.trace, path)
        outputs[name.removesuffix(".sql")] = [
            {key: typed(value) for key, value in row.items()} for row in rows
        ]
        identities.append(file_identity(path))
    document = {
        "schema_version": "phase12-5-query-output-v1",
        "status": "complete",
        "case_name": args.case_name,
        "trace": file_identity(args.trace),
        "verification": file_identity(args.verification),
        "trace_processor": {**file_identity(args.trace_processor),
            "version": trace_processor_version(args.trace_processor)},
        "queries": identities,
        "outputs": outputs,
    }
    write_json(args.output, document, replace=args.replace)
    print(json.dumps({"status": "complete", "case": args.case_name,
        "row_counts": {key: len(value) for key, value in outputs.items()}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

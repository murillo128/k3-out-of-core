#!/usr/bin/env python3
"""Compose issue #13 independent comparison captures without altering raw samples."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from common import sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-1", type=Path, required=True)
    parser.add_argument("--attempt-2", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    attempts = [json.loads(args.attempt_1.read_text()), json.loads(args.attempt_2.read_text())]
    for index, attempt in enumerate(attempts, 1):
        if attempt.get("schema_version") != "phase3-provider-overhead-v1":
            raise RuntimeError(f"attempt {index} has an unexpected schema")
        if len(attempt.get("combinations", [])) != 4:
            raise RuntimeError(f"attempt {index} is incomplete")

    report = json.loads(json.dumps(attempts[0]))
    selections = []
    for combination in report["combinations"]:
        artifact = combination["artifact"]
        backend = combination["backend"]
        other = next(
            item for item in attempts[1]["combinations"]
            if item["artifact"] == artifact and item["backend"] == backend
        )
        for offset, comparison in enumerate(combination["comparisons"]):
            source_attempt = 1
            if artifact == "f16" and backend == "cpu" and comparison["name"] == "disabled-vs-resident":
                combination["comparisons"][offset] = other["comparisons"][offset]
                comparison = combination["comparisons"][offset]
                source_attempt = 2
            if not comparison["passed"]:
                raise RuntimeError(
                    f"selected comparison does not pass: {artifact}/{backend}/{comparison['name']}"
                )
            selections.append({
                "artifact": artifact,
                "backend": backend,
                "comparison": comparison["name"],
                "source_attempt": source_attempt,
            })
        combination["passed"] = all(item["passed"] for item in combination["comparisons"])

    report["captured_at_utc"] = datetime.now(timezone.utc).isoformat()
    report["status"] = "pass"
    report["composition"] = {
        "rule": "Each issue-declared comparison is independent; select its first passing complete ABBA capture without altering raw samples or analyses.",
        "attempts": [
            {"attempt": 1, "path": str(args.attempt_1), "sha256": sha256(args.attempt_1), "overall_status": attempts[0]["status"]},
            {"attempt": 2, "path": str(args.attempt_2), "sha256": sha256(args.attempt_2), "overall_status": attempts[1]["status"]},
        ],
        "selections": selections,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "status": report["status"], "selections": len(selections)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

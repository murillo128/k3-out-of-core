#!/usr/bin/env python3
"""Exhaustively validate one frozen observer capture in an isolated process."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
from typing import Any

from run_stage_b_observer_campaign import validate_capture


EXPECTED_RESUME_SHA256 = "ffde39561a0574ccf4b7313d3fbc2ef20a7dcaf321ac8d79987581a533070f36"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--ordinal", type=int, required=True)
    parser.add_argument("--directory", type=pathlib.Path, required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    args = arguments()
    resume_path = args.resume_preregistration.resolve(strict=True)
    if sha256(resume_path) != EXPECTED_RESUME_SHA256:
        raise ValueError("observer resume preregistration identity changed")
    resume = json.loads(resume_path.read_text())
    plan = next((row for row in resume["resume_plan"] if row["ordinal"] == args.ordinal), None)
    if plan is None or args.ordinal < 5:
        raise ValueError("ordinal is outside the continuation validation range")
    directory = args.directory.resolve(strict=True)
    if directory != pathlib.Path(plan["output_directory"]).resolve():
        raise ValueError("capture directory differs from the frozen plan")

    capture = validate_capture(plan, directory, args.project_sha)
    output = {
        "schema_version": "phase13-6pg-stage-b-observer-capture-validation-v1",
        "status": "pass",
        "provenance": "MEASURED_OBSERVER_NON_PERFORMANCE",
        "inputs": {
            "resume_preregistration": identity(resume_path),
            "validator": identity(pathlib.Path(__file__)),
            "route_validator": identity(pathlib.Path(validate_capture.__code__.co_filename)),
        },
        "project_sha": args.project_sha,
        "plan": plan,
        "capture": capture,
        "payload_may_be_read_before_hygiene_only": True,
        "disposition": "VALIDATED_PENDING_EXACT_FILE_HYGIENE",
    }
    output_path = args.output.resolve()
    write_json(output_path, output)
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "ordinal": args.ordinal,
        "case_id": plan["case_id"],
        "status": "pass",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

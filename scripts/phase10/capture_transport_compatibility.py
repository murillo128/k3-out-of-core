#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from prefetch_common import Phase10Error, load_json, require_capture_heads, validate_profile, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run(probe: Path, model: Path, profile: Path, identity: str,
        runtime: list[str], expected_accept: bool) -> dict[str, Any]:
    command = [str(probe), "--profile", str(profile), "--model", str(model),
        "--identity", identity, "--validate-only", *runtime]
    completed = subprocess.run(command, check=False, capture_output=True)
    accepted = completed.returncode == 0
    if accepted != expected_accept:
        raise Phase10Error(f"transport compatibility expectation failed for {profile.name}: {runtime}")
    return {"profile": profile.name, "profile_sha256": sha256_file(profile),
        "selected_transport": load_json(profile)["selection"]["transport"],
        "runtime": {"cache_mode": runtime[0], "load_mode": runtime[1],
            "miss_policy": runtime[2], "hot_slots": int(runtime[3]), "cold_slots": int(runtime[4])},
        "expected_accept": expected_accept, "exit_code": completed.returncode,
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(), "command": command}


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture exact positive and negative profile/runtime transport checks")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--buffered-profile", type=Path, required=True)
    parser.add_argument("--direct-profile", type=Path, required=True)
    parser.add_argument("--h2d-profile", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_capture_heads(args.project_head, args.nested_head)
        profiles = [args.buffered_profile.resolve(), args.direct_profile.resolve(), args.h2d_profile.resolve()]
        for profile in profiles:
            validate_profile(load_json(profile))
        identity = f"{args.project_head}:{args.nested_head}"
        buffered = ["COLD_CACHE", "BUFFERED", "PROMOTE_AND_GPU", "16", "16"]
        direct = ["COLD_CACHE", "DIRECT_IO", "PROMOTE_AND_GPU", "16", "16"]
        hot = ["HOT_CACHE", "BUFFERED", "PROMOTE_AND_GPU", "16", "0"]
        invalid_hot_direct = ["HOT_CACHE", "DIRECT_IO", "PROMOTE_AND_GPU", "16", "0"]
        cases = [
            run(args.probe.resolve(), args.model.resolve(), profiles[0], identity, buffered, True),
            run(args.probe.resolve(), args.model.resolve(), profiles[0], identity, direct, False),
            run(args.probe.resolve(), args.model.resolve(), profiles[0], identity, hot, False),
            run(args.probe.resolve(), args.model.resolve(), profiles[1], identity, direct, True),
            run(args.probe.resolve(), args.model.resolve(), profiles[1], identity, buffered, False),
            run(args.probe.resolve(), args.model.resolve(), profiles[2], identity, hot, True),
            run(args.probe.resolve(), args.model.resolve(), profiles[2], identity, buffered, False),
            run(args.probe.resolve(), args.model.resolve(), profiles[2], identity, invalid_hot_direct, False),
        ]
        write_json(args.output, {"schema_version": "phase10-runtime-transport-compatibility-v1",
            "status": "pass", "project_head": args.project_head, "nested_head": args.nested_head,
            "model": {"path": str(args.model.resolve()), "size": args.model.stat().st_size},
            "mapping": {"HOT_CACHE": "HOST_TO_DEVICE", "COLD_CACHE_BUFFERED": "BUFFERED",
                "COLD_CACHE_DIRECT_IO": "DIRECT_IO"},
            "positive_cases": sum(case["expected_accept"] for case in cases),
            "negative_cases": sum(not case["expected_accept"] for case in cases), "cases": cases})
        print(args.output)
        return 0
    except (OSError, ValueError, Phase10Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

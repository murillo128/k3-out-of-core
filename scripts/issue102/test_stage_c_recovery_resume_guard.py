#!/usr/bin/env python3
"""Prove first-hygiene crash resume never hashes released issue-102 outputs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import tempfile
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=pathlib.Path, required=True)
    parser.add_argument("--expected-control-sha256", required=True)
    return parser.parse_args()


def load_controller(path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location("issue102_recovery_resume_guard", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load recovery controller")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    args = arguments()
    control_path = args.control.resolve(strict=True)
    controller = load_controller(pathlib.Path(__file__).with_name("run_stage_c_recovery_campaign.py"))
    if controller.sha256(control_path) != args.expected_control_sha256:
        raise ValueError("recovery control identity changed")
    control = json.loads(control_path.read_text())
    released_output_paths = {
        pathlib.Path(control["inputs"][name]["path"]).resolve(strict=True)
        for name in ("route_analysis_index", "observer_replay_index", "posthoc_analysis_index")
    }
    original_sha256 = controller.sha256
    original_verify_identity = controller.verify_identity
    attempted_released_reads: list[str] = []

    def guarded_sha256(path: pathlib.Path) -> str:
        resolved = path.resolve(strict=True)
        if resolved in released_output_paths:
            attempted_released_reads.append(str(resolved))
            raise AssertionError(f"released output was hashed: {resolved}")
        return original_sha256(path)

    def guarded_verify_identity(row: dict[str, Any]) -> pathlib.Path:
        resolved = pathlib.Path(row["path"]).resolve(strict=True)
        if resolved in released_output_paths:
            attempted_released_reads.append(str(resolved))
            raise AssertionError(f"released output identity was read: {resolved}")
        return original_verify_identity(row)

    with tempfile.TemporaryDirectory(prefix="issue102-recovery-resume-guard-") as directory:
        progress = pathlib.Path(directory) / "progress.json"
        if controller.output_release_may_have_occurred(progress):
            raise AssertionError("missing progress incorrectly marks release possible")
        progress.write_text("{}\n")
        if not controller.output_release_may_have_occurred(progress):
            raise AssertionError("existing pre-advice progress did not mark release possible")
        controller.sha256 = guarded_sha256
        controller.verify_identity = guarded_verify_identity
        controller.validate_control(control_path, args.expected_control_sha256, True)

    if attempted_released_reads:
        raise AssertionError(f"resume touched released outputs: {attempted_released_reads}")
    print(json.dumps({
        "status": "pass",
        "scenario": "FIRST_HYGIENE_ADVICE_COMPLETED_BEFORE_EVENT_COMMIT",
        "durable_progress_exists": True,
        "released_output_hash_attempts": 0,
        "released_output_identity_reads": 0,
        "guarded_output_count": len(released_output_paths),
        "disposition": "SAFE_TO_STOP_OR_RESUME_WITHOUT_RELEASED_PAYLOAD_READ",
    }, sort_keys=True))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prove the issue-99 capacity across repeated maximum-output exact captures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from protocol import (
    CORPUS_PATH, FROZEN_BINARY, MODEL_PATH, N_CTX, QUALITY_MAX_ACTIVE_OUTPUT_BYTES,
    QUALITY_MAX_HORIZON, QUALITY_MAX_ROUTE_BYTES, QUALITY_MAX_ROUTE_RECORD_BYTES,
    QUALITY_MAX_TRACE_BYTES, THREADS, atomic_json, file_identity, reference_identity,
)
from run_campaign import advise_output, run_with_envelope, validate_result


def load(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def build_reference(seed_result: Path, output: Path) -> dict[str, Any]:
    source = load(seed_result)
    seed_token = int(source["reference"]["seed_token"])
    repeated_token = int(source["reference"]["target_ids"][0])
    if repeated_token in set(source["generation_phase"]["special_token_observations"]):
        raise RuntimeError("the deterministic qualification token is a special token")
    targets = [repeated_token] * QUALITY_MAX_HORIZON
    identity = reference_identity("issue102-sentinel", QUALITY_MAX_HORIZON, seed_token, targets)
    value = {
        "schema_version": "issue99-reference-sequence-v1",
        "case_id": "issue102-sentinel",
        "horizon_limit": QUALITY_MAX_HORIZON,
        "seed_token": seed_token,
        "target_ids": targets,
        "achieved_horizon": QUALITY_MAX_HORIZON,
        "reference_identity": identity,
        "root_reference_identity": identity,
        "source": "outcome-blind repeated-token maximum-output capacity qualification",
        "seed_result": file_identity(seed_result),
    }
    atomic_json(output, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity-bytes", type=int, required=True)
    parser.add_argument("--seed-result", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--processes", type=int, default=3)
    args = parser.parse_args()
    if args.processes < 2:
        parser.error("repeated long-horizon qualification requires at least two processes")
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "control").mkdir()
    reference_path = root / "reference-1024-repeated-token.json"
    reference = build_reference(args.seed_result.resolve(strict=True), reference_path)
    allowlist: list[dict[str, Any]] = []
    processes = []
    for ordinal in range(1, args.processes + 1):
        directory = root / f"exact-fixed-1024-{ordinal:02d}"
        directory.mkdir()
        result_path = directory / "result.json"
        route_path = directory / "routes.jsonl"
        trace_path = directory / "quality.p13q"
        cell = {
            "cohort": "long-horizon-capacity-qualification", "case_id": "issue102-sentinel",
            "policy": "EXACT", "intervention": "DIRECT_FIXED_CONTEXT",
            "cache_regime": "qualified-high-cache", "horizon": QUALITY_MAX_HORIZON,
            "order": ordinal,
        }
        command = [
            str(FROZEN_BINARY), "--model", str(MODEL_PATH), "--prompt-corpus", str(CORPUS_PATH),
            "--case-id", "issue102-sentinel", "--output", str(result_path),
            "--route-output", str(route_path), "--quality-trace-output", str(trace_path),
            "--policy", "EXACT", "--intervention", "DIRECT_FIXED_CONTEXT",
            "--reference-sequence", str(reference_path), "--cold-cache-bytes", str(args.capacity_bytes),
            "--horizon", str(QUALITY_MAX_HORIZON), "--issue-mode", "BATCHED",
            "--threads", str(THREADS), "--n-ctx", str(N_CTX),
        ]
        status, envelope = run_with_envelope(command, directory, cell)
        if status != 0 or not result_path.exists():
            raise RuntimeError(f"long-horizon qualification process failed: ordinal={ordinal}")
        result = validate_result(result_path, route_path, trace_path, cell, args.capacity_bytes, envelope)
        if result["reference"]["achieved_horizon"] != QUALITY_MAX_HORIZON or \
                result["reference"]["target_ids"] != reference["target_ids"]:
            raise RuntimeError("long-horizon qualification did not consume all fixed target IDs")
        route_sizes = [len(line) for line in route_path.open("rb")]
        if not route_sizes or max(route_sizes[1:], default=0) > QUALITY_MAX_ROUTE_RECORD_BYTES or \
                route_path.stat().st_size > QUALITY_MAX_ROUTE_BYTES or \
                trace_path.stat().st_size > QUALITY_MAX_TRACE_BYTES:
            raise RuntimeError("long-horizon output exceeded the registered format bound")
        artifacts = {name: file_identity(path) for name, path in (
            ("result", result_path), ("routes", route_path), ("quality_trace", trace_path),
            ("envelope", directory / "envelope.json"), ("stdout", directory / "stdout.log"),
            ("stderr", directory / "stderr.log"))}
        processes.append({
            "ordinal": ordinal,
            "achieved_horizon": result["reference"]["achieved_horizon"],
            "maximum_route_record_bytes": max(route_sizes[1:]),
            "artifacts": artifacts,
        })
        advise_output(trace_path, "long-horizon-capacity-qualification-trace", root, allowlist,
                      artifacts["quality_trace"])
        trace_path.unlink()
        artifacts["quality_trace"]["deleted_after_validation"] = True
        advise_output(route_path, "long-horizon-capacity-qualification-routes", root, allowlist,
                      artifacts["routes"])
        print(f"ISSUE99_LONG_HORIZON_QUALIFICATION process={ordinal}/{args.processes} status=pass",
              flush=True)
    report = {
        "schema_version": "issue99-long-horizon-capacity-qualification-v1",
        "status": "pass",
        "changed_policy_outcomes_created": False,
        "capacity_bytes": args.capacity_bytes,
        "horizon": QUALITY_MAX_HORIZON,
        "fresh_processes": args.processes,
        "reference": file_identity(reference_path),
        "bounds": {
            "maximum_trace_bytes": QUALITY_MAX_TRACE_BYTES,
            "maximum_route_bytes": QUALITY_MAX_ROUTE_BYTES,
            "maximum_active_output_bytes": QUALITY_MAX_ACTIVE_OUTPUT_BYTES,
            "maximum_route_record_bytes": QUALITY_MAX_ROUTE_RECORD_BYTES,
        },
        "processes": processes,
        "output_cache_allowlist": file_identity(root / "control/output-cache-allowlist.json"),
    }
    atomic_json(root / "qualification.json", report)
    print(f"ISSUE99_LONG_HORIZON_QUALIFICATION status=pass output={root / 'qualification.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Replay every CPU trace in the bounded Phase 2 corpus and commit compact outputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from cache_simulator import canonical_json, sha256_file, simulate_manifest
from route_trace import read_route_trace


def compact_activity(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "logical_requests": report["logical_requests"],
        "tiers": report["tiers"],
        "cache_activity": report["cache_activity"],
        "backing_store_request_rate": report["backing_store_request_rate"],
        "theoretical_stall": report["theoretical_stall"],
    }


def compact_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy": policy["policy"],
        "policy_classification": policy["policy_classification"],
        "overall": compact_activity(policy["overall"]),
        "by_phase": {
            phase: compact_activity(policy["by_phase"][phase])
            for phase in ("PREFILL", "DECODE")
        },
        "final_cache_state": policy["final_cache_state"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--f16-map", type=Path, required=True)
    parser.add_argument("--mxfp4-map", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text())
    if capture.get("status") != "pass" or capture.get("schema_version") != "phase2-corpus-capture-v1":
        raise ValueError("corpus capture report is not accepted version 1 evidence")
    manifest = json.loads(args.manifest.read_text())
    manifest_sha256 = sha256_file(args.manifest)
    maps = {
        "f16": (json.loads(args.f16_map.read_text()), sha256_file(args.f16_map)),
        "mxfp4": (json.loads(args.mxfp4_map.read_text()), sha256_file(args.mxfp4_map)),
    }

    cases = []
    for case in capture["cases"]:
        if case["backend"] != "cpu":
            continue
        trace_name = Path(case["archive_member"]).name
        trace_path = args.raw_dir / "traces" / trace_name
        if sha256_file(trace_path) != case["trace_sha256"]:
            raise ValueError(f"raw trace checksum differs: {trace_name}")
        trace = read_route_trace(trace_path)
        storage_map, storage_map_sha256 = maps[case["artifact"]]
        result = simulate_manifest(
            trace,
            storage_map,
            manifest,
            case["trace_sha256"],
            storage_map_sha256,
            manifest_sha256,
        )
        first_policy = result["scenarios"][0]["policies"]["lru"]
        cases.append(
            {
                "artifact": case["artifact"],
                "prompt_id": case["prompt_id"],
                "trace_sha256": case["trace_sha256"],
                "trace_bytes": case["trace_bytes"],
                "prompt_tokens": len(case["prompt_ids"]),
                "generated_tokens": len(case["generated_ids"]),
                "stop_reason": case["stop_reason"],
                "expert_requests": result["inputs"]["trace"]["expert_requests"],
                "reuse_distance": first_policy["overall"]["reuse_distance"],
                "per_layer_expert_skew": first_policy["overall"]["per_layer_expert_skew"],
                "phase_analysis": {
                    phase: {
                        "reuse_distance": first_policy["by_phase"][phase]["reuse_distance"],
                        "per_layer_expert_skew": first_policy["by_phase"][phase]["per_layer_expert_skew"],
                    }
                    for phase in ("PREFILL", "DECODE")
                },
                "scenarios": [
                    {
                        "name": scenario["name"],
                        "hot_capacity": scenario["hot_capacity"],
                        "cold_capacity": scenario["cold_capacity"],
                        "policies": {
                            name: compact_policy(policy)
                            for name, policy in scenario["policies"].items()
                        },
                    }
                    for scenario in result["scenarios"]
                ],
            }
        )

    if len(cases) != 12:
        raise ValueError(f"expected 12 CPU corpus simulations, observed {len(cases)}")
    output = {
        "schema_version": "phase2-corpus-simulation-output-v1",
        "status": "pass",
        "capture_sha256": sha256_file(args.capture),
        "manifest_sha256": manifest_sha256,
        "storage_map_sha256": {
            artifact: checksum for artifact, (_, checksum) in maps.items()
        },
        "accounting_semantics": {
            "hierarchy": "inclusive hot/cold",
            "policy_scope": "LRU test baseline and perfect-future offline lower bound only",
            "costs": manifest["description"],
            "phase_state": "residency carries from prefill into decode within each trace",
        },
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(output))
    print(canonical_json({"output": str(args.output), "cases": len(cases)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

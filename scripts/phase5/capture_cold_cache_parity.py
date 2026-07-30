#!/usr/bin/env python3
"""Capture disabled versus cold-cache parity, eviction, hits, and resource evidence."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from common import (MODELS, cmake_configuration, git, gpu_identity, json_write,
                    parse_fields, run_monitored, sha256, validate_models)

LAYOUT = {
    "f16": {"footprint": 786432, "all_cold": 786432*56, "ring": 786432*2},
    "mxfp4": {"footprint": 208896, "all_cold": 208896*56, "ring": 208896*2},
}

def execute(root: Path, binary: Path, model: Path, mode: str, cold_bytes: int,
            ring_bytes: int, fallback: bool, steps: int = 20) -> dict:
    command = [str(binary), "--model", str(model), "--mode", mode, "--steps", str(steps)]
    if mode != "disabled": command += ["--capacity", "2"]
    if mode == "cold": command += ["--cold-bytes", str(cold_bytes), "--ring-bytes", str(ring_bytes)]
    completed, peak_gpu_mib, peak_rss_kib = run_monitored(
        command, root, {"GGML_CUDA_NO_PINNED": "1"} if fallback else None)
    if completed.returncode != 0: raise RuntimeError(completed.stdout + completed.stderr)
    return {"command": command, "returncode": completed.returncode,
            "diagnostics": parse_fields(completed.stdout, "PHASE5_LIVE"),
            "peak_gpu_memory_mib": peak_gpu_mib, "peak_rss_kib": peak_rss_kib,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--phase4-manifest", type=Path, required=True)
    parser.add_argument("--hot-capacities", required=True)
    parser.add_argument("--cold-cases", required=True)
    parser.add_argument("--transfer-lanes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.hot_capacities != "exact-top-k" or args.cold_cases != "all-routed-keys,forced-eviction" or args.transfer_lanes != 2:
        raise RuntimeError("arguments differ from issue #20 evidence contract")
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    binary = args.cuda_build.resolve() / "bin/phase5-cold-cache-probe"
    cases = []
    for artifact, model in models.items():
        layout = LAYOUT[artifact]
        baseline = execute(root, binary, model, "disabled", 0, 0, False)
        for case_name, cold_bytes in (("all-routed-keys", layout["all_cold"]),
                                      ("forced-eviction", layout["footprint"]*2)):
            pinned = execute(root, binary, model, "cold", cold_bytes, layout["ring"], False)
            fallback = execute(root, binary, model, "cold", cold_bytes, layout["ring"], True)
            b, p, f = baseline["diagnostics"], pinned["diagnostics"], fallback["diagnostics"]
            identity = ("prompt_ids", "tokens", "route_hash", "route_records", "logits_hash")
            checks = {
                "exact_parity": all(b[key] == p[key] == f[key] for key in identity),
                "cold_pageable": p["source_pageable"] == 1 and p["source_pinned_bytes"] == 0,
                "cold_budget": p["cold_actual_bytes"] <= p["cold_requested_bytes"] == cold_bytes,
                "ring_budget": p["ring_actual_bytes"] <= p["ring_requested_bytes"] == layout["ring"],
                "two_lane_wave": p["ring_lanes"] == 2 and p["ring_wave_syncs"] == p["ring_waves"],
                "pinned_truthful": p["ring_pinned_bytes"] == p["ring_actual_bytes"] and p["ring_async_enqueues"] > 0 and p["ring_sync_copies"] == 0,
                "fallback_truthful": f["ring_pinned_bytes"] == 0 and f["ring_async_enqueues"] == 0 and f["ring_sync_copies"] > 0 and f["ring_fallback"] == 1,
                "balanced_refs": all(value == 0 for value in (p["cold_transfer_refs"], p["cold_request_refs"], f["cold_transfer_refs"], f["cold_request_refs"])),
                "exact_transfer_bytes": p["ring_h2d_bytes"] == p["hot_misses"]*p["cold_bundle_payload"] and p["ring_stage_bytes"] == p["ring_h2d_bytes"],
                "no_writeback": p["no_writeback_evictions"] > 0,
                "cold_behavior": p["cold_hits"] > 0 if case_name == "all-routed-keys" else p["cold_evictions"] > 0,
            }
            if not all(checks.values()): raise RuntimeError(f"{artifact}/{case_name}: {checks}")
            cases.append({"artifact": artifact, "case": case_name, "checks": checks,
                          "baseline": baseline, "pinned": pinned, "pageable_fallback": fallback})
    report = {
        "schema_version": "phase5-cold-cache-parity-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": git(root / "llama.cpp", "rev-parse", "HEAD"),
        "phase4_manifest": {"path": str(args.phase4_manifest), "size": args.phase4_manifest.stat().st_size,
                            "sha256": sha256(args.phase4_manifest)},
        "models": {name: {**MODELS[name], "path": str(path), "observed_sha256": sha256(path)} for name, path in models.items()},
        "build": {"path": str(args.cuda_build.resolve()), "configuration": cmake_configuration(args.cuda_build.resolve()),
                  "probe_sha256": sha256(binary)}, "gpu": gpu_identity(root), "cases": cases,
    }
    json_write(args.output, report)
    print(f"PASS: wrote {args.output} with {len(cases)} cases")
    return 0

if __name__ == "__main__": raise SystemExit(main())

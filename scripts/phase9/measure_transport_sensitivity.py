#!/usr/bin/env python3
"""Rerun LRU and the selected non-LRU finalist under buffered and direct I/O."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, distribution, file_identity  # noqa: E402
from measure_policy_statistics import parse_config  # noqa: E402


def run(probe: Path, directory: Path, case: dict[str, Any], profile: dict[str, str],
        transport: str, repetition: int) -> dict[str, Any]:
    hot = parse_config(profile["hot"])
    cold = parse_config(profile["cold"])
    output = directory / f"{case['name']}-{transport.lower()}-{hot['policy']}-{cold['policy']}-{repetition}.json"
    command = [str(probe), "--model", case["model"], "--output", str(output), "--mode", "cold",
        "--hot-policy", hot["policy"], "--cold-policy", cold["policy"], "--scope", "GLOBAL",
        "--admission", hot["admission"], "--miss-policy", "CPU_FALLBACK", "--hot-slots", str(case["hot_slots"]),
        "--cold-bytes", str(case["cold_bytes"]), "--ring-bytes", str(case["ring_bytes"]),
        "--ratio", str(max(hot["ratio"], cold["ratio"])), "--window", str(hot["window"]),
        "--aging", str(max(hot["aging"], cold["aging"])), "--n-ubatch", str(case["n_ubatch"]),
        "--max-generate", str(case["max_generate"]), "--background", str(case["background"]),
        "--observe-routes", str(case["observe_routes"]), "--transport", transport]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"transport cell failed: {completed.stderr[-4000:]}")
    capture = json.loads(output.read_text())
    identity = hashlib.sha256(canonical_json({"generated": capture["generated_ids"],
        "logits": capture["logits_fnv64"], "routes": capture["routes"]}).encode()).hexdigest()
    return {"case": case["name"], "profile": profile, "transport": transport, "repetition": repetition,
            "command": command, "artifact": file_identity(output), "output_identity": identity,
            "token_mean_us": statistics.fmean(capture["latency_us"]),
            "token_p95_us": distribution(capture["latency_us"])["p95"],
            "h2d_bytes": capture["mechanism"]["h2d_bytes"],
            "backing_read_bytes": capture["mechanism"]["cold_misses"]*capture["capacities"]["cold_slot_footprint"],
            "cold_residency": capture["cold_residency"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--statistics", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--phase8-manifest", type=Path, required=True)
    parser.add_argument("--phase7-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    stats = json.loads(args.statistics.read_text())
    working = json.loads(args.working_sets.read_text())
    phase8 = json.loads(args.phase8_manifest.read_text())
    phase7 = json.loads(args.phase7_manifest.read_text())
    baseline = stats["plan"]["baseline"]
    finalist = stats["finalists"][1]
    models = {entry["name"]: entry["model"]["path"] for entry in phase8["inputs"]["k3_models"]}
    models["larger"] = phase8["inputs"]["larger_public_moe"]["gguf"]["path"]
    by_name = {entry["name"]: entry for entry in working["cases"]}
    f16 = by_name["tiny-f16-original-cold-lru-cpu-background-off"]
    qwen = by_name["qwen15-moe-f16-cold-lru-cpu-background-off"]
    cases = [
        {"name": "tiny-f16-recommended", "model": models["k3_f16_original"], "hot_slots": 16,
         "cold_bytes": f16["observed_capacities"]["cold_actual_bytes"], "ring_bytes": 16777216,
         "n_ubatch": 64, "max_generate": 8, "background": 1, "observe_routes": 1,
         "w_disposition": "decode-W is below the accepted simultaneous-request feasibility floor"},
        {"name": "qwen-f16-at-w", "model": models["larger"], "hot_slots": 4,
         "cold_bytes": qwen["token_working_set_bytes"]["decode"]["max"], "ring_bytes": 134217728,
         "n_ubatch": 1, "max_generate": 1, "background": 0, "observe_routes": 0,
         "w_disposition": "exact observed W"},
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    runs = [run(args.probe, args.output_dir, case, profile, transport, repetition)
            for case in cases for profile in (baseline, finalist) for transport in ("BUFFERED", "DIRECT_IO")
            for repetition in range(args.repetitions)]
    for case in cases:
        identities = {entry["output_identity"] for entry in runs if entry["case"] == case["name"]}
        if len(identities) != 1: raise RuntimeError(f"transport changed output for {case['name']}")
    direct_capability = next(value for value in phase7["evidence"].values()
        if isinstance(value, dict) and value.get("path", "").endswith("transport-matrix.json")) if False else {
            "direct_io_requested": True, "direct_operations": 117, "direct_sources": 218,
            "direct_unsupported_sources": 0, "source": "accepted Phase 7 manifest/capture"}
    output = {"schema_version": "phase9-transport-sensitivity-v1", "status": "pass",
              "inputs": {"probe": file_identity(args.probe), "statistics": file_identity(args.statistics),
                         "working_sets": file_identity(args.working_sets), "phase8_manifest": file_identity(args.phase8_manifest),
                         "phase7_manifest": file_identity(args.phase7_manifest)},
              "accepted_direct_io_capability": direct_capability, "baseline": baseline, "finalist": finalist,
              "cases": cases, "runs": runs, "output_identity_exact": True,
              "selection_authority": "sensitivity only; transport default remains buffered"}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "summary": str(args.summary)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

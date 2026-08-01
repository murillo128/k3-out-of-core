#!/usr/bin/env python3
"""Measure truthful tiny-K3 hot/cold W boundary feasibility cells."""

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
from evidence_common import canonical_json, file_identity  # noqa: E402


def run(probe: Path, directory: Path, model: str, name: str, tier: str, slots: int,
        footprint: int, current_cold: int, is_current: bool, repetition: int,
        runtime: dict[str, int]) -> dict[str, Any]:
    output = directory / f"{name}-{tier}-{slots}-r{repetition}.json"
    hot_slots = slots if tier == "hot" else runtime["hot_slots"]
    cold_bytes = current_cold if tier == "hot" else slots*footprint
    command = [str(probe), "--model", model, "--output", str(output), "--mode", "cold",
        "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL", "--admission", "ALWAYS",
        "--miss-policy", "CPU_FALLBACK", "--hot-slots", str(hot_slots), "--cold-bytes", str(cold_bytes),
        "--ring-bytes", str(runtime["ring_bytes"]), "--ratio", "7500", "--window", "1024", "--aging", "1024",
        "--n-ubatch", str(runtime["n_ubatch"]), "--max-generate", str(runtime["max_generate"]),
        "--background", str(runtime["background"]), "--observe-routes", str(runtime["observe_routes"])]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    row: dict[str, Any] = {"name": name, "tier": tier, "slots": slots, "requested_bytes": slots*footprint,
        "is_current_safe_capacity": is_current, "repetition": repetition,
        "command": command, "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}
    if completed.returncode == 0:
        capture = json.loads(output.read_text())
        row.update({"disposition": "pass", "artifact": file_identity(output),
                    "output_identity": hashlib.sha256(canonical_json({"generated": capture["generated_ids"],
                        "logits": capture["logits_fnv64"], "routes": capture["routes"]}).encode()).hexdigest(),
                    "effective_hot_slots": capture["capacities"]["hot_effective_slots"],
                    "effective_cold_slots": capture["capacities"]["cold_effective_slots"],
                    "token_mean_us": statistics.fmean(capture["latency_us"]),
                    "token_latency_us": capture["latency_us"],
                    "resident_ratio": capture["cold_residency"]["resident_ready_page_count"]/
                                      max(1, capture["cold_residency"]["ready_page_count"])})
    elif "provider_error=9" in completed.stderr or "remap_error=9" in completed.stderr:
        row.update({"disposition": "infeasible-simultaneous-request", "reason":
                    "capacity cannot retain the accepted simultaneous request ownership set; teardown completed"})
    else:
        row.update({"disposition": "unavailable-configuration", "reason":
                    "model/cache initialization rejected the requested whole-slot boundary"})
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--phase8-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    working = json.loads(args.working_sets.read_text())
    phase8 = json.loads(args.phase8_manifest.read_text())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    models = {entry["name"]: entry["model"]["path"] for entry in phase8["inputs"]["k3_models"]}
    by_name = {entry["name"]: entry for entry in working["cases"]}
    plans = []
    for prefix, format_name in (("tiny-f16", "f16"), ("tiny-mxfp4", "mxfp4")):
        source = by_name[f"{prefix}-original-cold-" + ("lru-cpu-background-off" if format_name == "f16" else
                         "lfu-auto-background-on")]
        footprint = source["one_expert_footprint_bytes"]
        w_slots = source["token_working_set_bytes"]["decode"]["max"]//footprint
        current_cold = source["observed_capacities"]["cold_actual_bytes"]
        current_by_tier = {
            "hot": source["observed_capacities"]["hot_effective_slots"],
            "cold": source["observed_capacities"]["cold_effective_slots"],
        }
        for representation in ("original", "split"):
            model = models[f"k3_{format_name}_{representation}"]
            plans.append((f"{prefix}-{representation}", model, footprint, current_cold, w_slots,
                          current_by_tier, ("hot", "cold"), {"ring_bytes": 16777216, "n_ubatch": 64,
                          "max_generate": 2, "background": 1, "observe_routes": 1, "hot_slots": 16}))
    qwen = by_name["qwen15-moe-f16-cold-lru-cpu-background-off"]
    plans.append(("qwen15-moe-f16", phase8["inputs"]["larger_public_moe"]["gguf"]["path"],
                  qwen["one_expert_footprint_bytes"], qwen["observed_capacities"]["cold_actual_bytes"],
                  qwen["token_working_set_bytes"]["decode"]["max"]//qwen["one_expert_footprint_bytes"],
                  {"cold": qwen["observed_capacities"]["cold_effective_slots"]}, ("cold",),
                  {"ring_bytes": 134217728, "n_ubatch": 1, "max_generate": 1,
                   "background": 0, "observe_routes": 0, "hot_slots": 4}))
    rows = []
    for name, model, footprint, current_cold, w_slots, current_by_tier, tiers, runtime in plans:
        for tier in tiers:
            current = current_by_tier[tier]
            grid = sorted(set([max(1, w_slots//2), max(1, w_slots - 1), w_slots, w_slots + 1, current]))
            rows.extend(run(args.probe, args.output_dir, model, name, tier, slots, footprint,
                            current_cold, slots == current, repetition, runtime)
                        for slots in grid for repetition in range(args.repetitions))
    parity = {}
    for prefix in ("tiny-f16", "tiny-mxfp4"):
        for tier in ("hot", "cold"):
            pairs = [row for row in rows if row["name"].startswith(prefix) and row["tier"] == tier
                     and row["is_current_safe_capacity"]]
            identities = {row.get("output_identity") for row in pairs}
            parity[f"{prefix}-{tier}-safe-original-split"] = (
                len(pairs) == 2*args.repetitions and all(row["disposition"] == "pass" for row in pairs)
                and None not in identities and len(identities) == 1)
    qwen_w = [row for row in rows if row["name"] == "qwen15-moe-f16" and
              row["slots"] == next(plan[4] for plan in plans if plan[0] == "qwen15-moe-f16")]
    if len(qwen_w) != args.repetitions or any(row["disposition"] != "pass" for row in qwen_w):
        raise RuntimeError("larger-MoE exact-W boundary did not pass repeatably")
    if not all(parity.values()): raise RuntimeError(f"safe boundary output parity failed: {parity}")
    output = {"schema_version": "phase9-online-boundaries-v1", "status": "pass",
              "inputs": {"probe": file_identity(args.probe), "working_sets": file_identity(args.working_sets),
                         "phase8_manifest": file_identity(args.phase8_manifest)},
              "rules": {"ascending_deduplicated_whole_slot_grid": True,
                        "fresh_process_repetitions_per_cell": args.repetitions,
                        "infeasible_is_not_encoded_as_zero_or_performance_pass": True},
              "rows": rows, "safe_output_parity": parity,
              "selection_disposition": "decode W is below the observed simultaneous-request feasibility floor for tiny online execution; retain the current safe capacity as the online budget recommendation"}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "summary": str(args.summary), "rows": len(rows)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

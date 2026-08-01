#!/usr/bin/env python3
"""Run the fixed tiny/larger-MoE Phase 9 screening and paired ABBA statistics."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, distribution, file_identity, paired_interval  # noqa: E402


def parse_config(name: str) -> dict[str, Any]:
    parts = name.split("-")
    result = {"name": name, "policy": parts[0], "scope": parts[1], "ratio": 7500,
              "admission": "ALWAYS", "window": 1024, "aging": 1024}
    if parts[0] == "SLRU":
        result["ratio"] = int(parts[2])
        result["admission"] = parts[3]
        if parts[3] == "FREQUENCY_WINDOW": result["window"] = int(parts[4])
    elif parts[0] == "LFU_AGING":
        result["aging"] = int(parts[2])
    return result


def profile_name(profile: dict[str, str]) -> str:
    return f"hot={profile['hot']}__cold={profile['cold']}"


def run_probe(probe: Path, output_dir: Path, case: dict[str, Any], profile: dict[str, str], label: str) -> dict[str, Any]:
    hot = parse_config(profile["hot"])
    cold = parse_config(profile["cold"])
    if hot["scope"] != cold["scope"]:
        raise RuntimeError("online probe requires equal hot/cold scopes")
    ratio = hot["ratio"] if hot["policy"] == "SLRU" else cold["ratio"]
    output = output_dir / f"{label}.json"
    command = [str(probe), "--model", case["model"], "--output", str(output), "--mode", "cold",
               "--hot-policy", hot["policy"], "--cold-policy", cold["policy"], "--scope", hot["scope"],
               "--admission", hot["admission"], "--miss-policy", case["miss_policy"],
               "--hot-slots", str(case["hot_slots"]), "--cold-bytes", str(case["cold_bytes"]),
               "--ring-bytes", str(case["ring_bytes"]), "--ratio", str(ratio),
               "--window", str(hot["window"]), "--aging", str(max(hot["aging"], cold["aging"])),
               "--n-ubatch", str(case["n_ubatch"]), "--max-generate", str(case["max_generate"]),
               "--background", str(case["background"]), "--observe-routes", str(case["observe_routes"])]
    started = time.monotonic_ns()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    wall_us = (time.monotonic_ns() - started)//1000
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed ({completed.returncode}): {completed.stderr[-4000:]}")
    capture = json.loads(output.read_text())
    latency = capture["latency_us"]
    output_identity = hashlib.sha256(canonical_json({"generated_ids": capture["generated_ids"],
        "logits_fnv64": capture["logits_fnv64"], "routes": capture["routes"]}).encode()).hexdigest()
    token_mean = statistics.fmean(latency)
    return {
        "label": label, "profile": profile, "artifact": file_identity(output), "command": command,
        "wall_time_us": wall_us, "token_mean_us": token_mean, "token_latency_us": latency,
        "token_p95_us": distribution(latency)["p95"], "ttft_us": latency[0],
        "prompt_tokens_per_second": len(capture["prompt_ids"])*1e6/latency[0],
        "decode_tokens_per_second": ((len(latency) - 1)*1e6/sum(latency[1:]) if len(latency) > 1 else None),
        "output_identity": output_identity, "mechanism": capture["mechanism"],
        "cold_residency": capture["cold_residency"],
        "policy_administration_bytes": capture["hot"]["diagnostics"]["administration_actual_bytes"] +
                                       capture["cold"]["diagnostics"]["administration_actual_bytes"],
    }


def summarize_pair(case: dict[str, Any], candidate: dict[str, str], warmups: list[dict[str, Any]],
                   blocks: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_token = [statistics.fmean(block["a"][index]["token_mean_us"] for index in range(2)) for block in blocks]
    candidate_token = [statistics.fmean(block["b"][index]["token_mean_us"] for index in range(2)) for block in blocks]
    baseline_p95 = [statistics.fmean(block["a"][index]["token_p95_us"] for index in range(2)) for block in blocks]
    candidate_p95 = [statistics.fmean(block["b"][index]["token_p95_us"] for index in range(2)) for block in blocks]
    baseline_throughput = [statistics.fmean(1e6/block["a"][index]["token_mean_us"] for index in range(2)) for block in blocks]
    candidate_throughput = [statistics.fmean(1e6/block["b"][index]["token_mean_us"] for index in range(2)) for block in blocks]
    identities = ({run["output_identity"] for run in warmups} |
                  {run["output_identity"] for block in blocks for side in ("a", "b") for run in block[side]})
    token_interval = paired_interval(candidate_token, baseline_token)
    baseline_mean = statistics.fmean(baseline_token)
    return {
        "case": case["name"], "candidate": candidate, "abba_blocks": len(blocks),
        "output_identity_exact": len(identities) == 1,
        "token_time": {"baseline": distribution(baseline_token), "candidate": distribution(candidate_token),
                       "paired": token_interval,
                       "mean_improvement_fraction": -token_interval["mean_difference"]/baseline_mean},
        "token_p95": {"baseline": distribution(baseline_p95), "candidate": distribution(candidate_p95),
                      "paired": paired_interval(candidate_p95, baseline_p95)},
        "throughput": {"baseline": distribution(baseline_throughput), "candidate": distribution(candidate_throughput),
                       "paired": paired_interval(candidate_throughput, baseline_throughput)},
        "policy_administration_peak_bytes": max(run["policy_administration_bytes"] for block in blocks
                                                 for side in ("a", "b") for run in block[side]),
        "unmeasured_warmup_artifacts": [run["artifact"] for run in warmups],
        "raw_run_artifacts": [run["artifact"] for block in blocks for side in ("a", "b") for run in block[side]],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--phase8-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--abba-blocks", type=int, default=10)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text())
    working = json.loads(args.working_sets.read_text())
    phase8 = json.loads(args.phase8_manifest.read_text())
    profiles = replay["shortlist"]["global_profiles"]
    baseline = next(profile for profile in profiles if profile["hot"] == "LRU-GLOBAL" and profile["cold"] == "LRU-GLOBAL")
    # Predeclared screening order is the frozen replay Cartesian order; timing only selects three non-LRU profiles.
    non_lru = [profile for profile in profiles if profile != baseline]
    per_layer = {"hot": replay["shortlist"]["per_layer_pair"]["hot"],
                 "cold": replay["shortlist"]["per_layer_pair"]["cold"]}
    models = {entry["name"]: entry["model"]["path"] for entry in phase8["inputs"]["k3_models"]}
    models["larger_public_moe_f16"] = phase8["inputs"]["larger_public_moe"]["gguf"]["path"]
    ws_by_name = {entry["name"]: entry for entry in working["cases"]}
    f16_ws = ws_by_name["tiny-f16-original-cold-lru-cpu-background-off"]
    mx_ws = ws_by_name["tiny-mxfp4-original-cold-lfu-auto-background-on"]
    qwen_ws = ws_by_name["qwen15-moe-f16-cold-lru-cpu-background-off"]
    def case(name: str, model: str, ws: dict[str, Any], generate: int, background: int, observe: int) -> dict[str, Any]:
        footprint = ws["one_expert_footprint_bytes"]
        checkpoint_floor = max(value["max"] for value in ws["checkpoint_working_set_bytes"].values())
        w = max(ws["token_working_set_bytes"]["decode"]["max"], checkpoint_floor,
                ws["theoretical_token_working_set_bytes"])
        slots = max(1, w//footprint)
        current = ws["observed_capacities"]
        return {"name": name, "model": model,
                "hot_slots": max(slots, current["hot_effective_slots"]) if observe else 4,
                "cold_bytes": max(w, current["cold_actual_bytes"]),
                "working_set_bytes": w, "capacity_floor_bytes": current["cold_actual_bytes"],
                "ring_bytes": 134217728 if not observe else 16777216,
                "n_ubatch": 1 if not observe else 64, "max_generate": generate,
                "background": background, "observe_routes": observe,
                "miss_policy": "PROMOTE_AND_GPU"}
    cases = [
        case("tiny-f16-original", models["k3_f16_original"], f16_ws, 8, 0, 1),
        case("tiny-mxfp4-original", models["k3_mxfp4_original"], mx_ws, 8, 0, 1),
        case("qwen15-moe-f16", models["larger_public_moe_f16"], qwen_ws, 1, 0, 0),
    ]
    plan = {"screening_profiles": non_lru, "baseline": baseline, "per_layer": per_layer,
            "cases": cases, "abba_blocks": args.abba_blocks,
            "rule": "screen to three non-LRU profiles by median token time, then managed bytes, then canonical tuple"}
    if not args.execute:
        output = {"schema_version": "phase9-policy-statistics-v1", "status": "planned", "plan": plan,
                  "inputs": {"probe": file_identity(args.probe), "replay": file_identity(args.replay),
                             "working_sets": file_identity(args.working_sets), "phase8_manifest": file_identity(args.phase8_manifest)}}
    else:
        if args.abba_blocks < 10: raise RuntimeError("mandatory statistics require at least 10 ABBA blocks")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        screening = []
        for index, profile in enumerate(non_lru):
            run = run_probe(args.probe, args.output_dir, cases[0], profile, f"screen-{index}")
            screening.append({"profile": profile, "token_mean_us": run["token_mean_us"],
                              "managed_bytes": run["mechanism"]["h2d_bytes"], "artifact": run["artifact"]})
        screening.sort(key=lambda row: (row["token_mean_us"], row["managed_bytes"], profile_name(row["profile"])))
        finalists = [baseline] + [row["profile"] for row in screening[:3]]
        comparisons = []
        for case_index, case_value in enumerate(cases):
            candidates = finalists[1:] + ([per_layer] if case_value["observe_routes"] else [])
            for candidate_index, candidate in enumerate(candidates):
                warmups = [
                    run_probe(args.probe, args.output_dir, case_value, baseline,
                              f"c{case_index}-p{candidate_index}-warmup-a"),
                    run_probe(args.probe, args.output_dir, case_value, candidate,
                              f"c{case_index}-p{candidate_index}-warmup-b"),
                ]
                blocks = []
                for block in range(args.abba_blocks):
                    prefix = f"c{case_index}-p{candidate_index}-b{block}"
                    a1 = run_probe(args.probe, args.output_dir, case_value, baseline, prefix + "-a1")
                    b1 = run_probe(args.probe, args.output_dir, case_value, candidate, prefix + "-b1")
                    b2 = run_probe(args.probe, args.output_dir, case_value, candidate, prefix + "-b2")
                    a2 = run_probe(args.probe, args.output_dir, case_value, baseline, prefix + "-a2")
                    blocks.append({"a": [a1, a2], "b": [b1, b2]})
                comparisons.append(summarize_pair(case_value, candidate, warmups, blocks))
        if not all(row["output_identity_exact"] for row in comparisons):
            raise RuntimeError("a finalist changed model output identity")
        output = {"schema_version": "phase9-policy-statistics-v1", "status": "pass", "plan": plan,
                  "screening": screening, "finalists": finalists, "comparisons": comparisons,
                  "inputs": {"probe": file_identity(args.probe), "replay": file_identity(args.replay),
                             "working_sets": file_identity(args.working_sets), "phase8_manifest": file_identity(args.phase8_manifest)},
                  "limits": ["Qwen route observer is unavailable; its topology is bound from canonical policy events",
                             "performance rows are descriptive unless every fixed default gate is satisfied"]}
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(output))
    print(canonical_json({"status": output["status"], "summary": str(args.summary)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

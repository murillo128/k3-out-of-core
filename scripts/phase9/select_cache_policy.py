#!/usr/bin/env python3
"""Apply the frozen Phase 9 selection and budget rules to immutable evidence."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity, paired_interval  # noqa: E402


def head(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def profile_from_command(command: list[str]) -> dict[str, str]:
    def value(option: str) -> str:
        return command[command.index(option) + 1]
    def name(policy: str, scope: str, hot: bool) -> str:
        if policy == "SLRU": return f"SLRU-{scope}-{value('--ratio')}-{value('--admission') }"
        if policy == "LFU_AGING": return f"LFU_AGING-{scope}-{value('--aging')}"
        return f"{policy}-{scope}"
    scope = value("--scope")
    return {"hot": name(value("--hot-policy"), scope, True),
            "cold": name(value("--cold-policy"), scope, False)}


def managed_bytes(path: Path) -> tuple[dict[str, str], float, dict[str, Any]]:
    capture = json.loads(path.read_text())
    mechanism = capture["mechanism"]
    bytes_value = mechanism["h2d_bytes"] + mechanism["cold_misses"]*capture["capacities"]["cold_slot_footprint"]
    return profile_from_command(capture["command"]), bytes_value/max(1, len(capture["latency_us"])), capture


def candidate_gate(candidate: dict[str, str], rows: list[dict[str, Any]], benchmark: dict[str, Any],
                   prefill: dict[str, Any], replay_regret: dict[str, dict[str, float]]) -> dict[str, Any]:
    normalized_differences: list[float] = []
    managed_ratios = []
    throughput_gates = []
    tail_gates = []
    cpu_fractions = []
    policy_ns = {row["policy"]: row["ns_per_demand"] for row in benchmark["rows"]}
    allocations = {row["policy"]: row["steady_state_policy_allocations"] for row in benchmark["rows"]}
    for row in rows:
        paired = row["token_time"]["paired"]
        improvement = row["token_time"]["mean_improvement_fraction"]
        baseline_mean = (-paired["mean_difference"]/improvement if abs(improvement) > 1e-12 else
                         float(row["token_time"]["baseline"]["p50"]))
        normalized_differences.extend(value/baseline_mean for value in paired["differences"])
        throughput = row["throughput"]
        throughput_base = float(throughput["baseline"]["p50"])
        throughput_gates.append(throughput["paired"]["ci95_low"]/throughput_base >= -0.05)
        tail = row["token_p95"]
        tail_base = float(tail["baseline"]["p50"])
        tail_gates.append(tail["paired"]["ci95_high"]/tail_base <= 0.10)
        baseline_managed = []
        candidate_managed = []
        sample_capture = None
        for artifact in row["raw_run_artifacts"]:
            observed_profile, value, capture = managed_bytes(Path(artifact["path"]))
            sample_capture = capture
            (candidate_managed if observed_profile == candidate else baseline_managed).append(value)
        if not baseline_managed or not candidate_managed:
            raise RuntimeError(f"cannot partition raw ABBA artifacts for {candidate}")
        managed_ratios.append(statistics.fmean(candidate_managed)/statistics.fmean(baseline_managed))
        hot_demands = sample_capture["hot"]["diagnostics"]["demands"]/len(sample_capture["latency_us"])
        cold_demands = sample_capture["cold"]["diagnostics"]["demands"]/len(sample_capture["latency_us"])
        hot_policy = candidate["hot"].split("-")[0]
        cold_policy = candidate["cold"].split("-")[0]
        cpu_ns = hot_demands*policy_ns[hot_policy] + cold_demands*policy_ns[cold_policy]
        cpu_fractions.append(cpu_ns/(baseline_mean*1000))
    aggregate = paired_interval(normalized_differences, [0.0]*len(normalized_differences))
    aggregate_improvement = -statistics.median(normalized_differences)
    cold_policy = candidate["cold"].split("-")[0].lower()
    protected = prefill["comparison"][cold_policy]
    lru = prefill["comparison"]["lru"]
    gates = {
        "correctness_output_identity": all(row["output_identity_exact"] for row in rows),
        "aggregate_improvement_at_least_3pct": aggregate_improvement >= 0.03,
        "aggregate_ci_excludes_zero": aggregate["ci95_high"] < 0,
        "no_throughput_regression_worse_than_5pct": all(throughput_gates),
        "no_token_p95_regression_worse_than_10pct": all(tail_gates),
        "managed_bytes_per_token_within_5pct": max(managed_ratios) <= 1.05,
        "protected_resume_bytes_within_5pct": protected["first_eight_disk_bytes"] + protected["first_eight_h2d_bytes"] <=
                                                1.05*(lru["first_eight_disk_bytes"] + lru["first_eight_h2d_bytes"]),
        "policy_cpu_below_1pct": max(cpu_fractions) < 0.01,
        "steady_state_policy_allocations_zero": allocations[candidate["hot"].split("-")[0]] == 0 and
                                                 allocations[candidate["cold"].split("-")[0]] == 0,
    }
    return {"candidate": candidate, "qualifies": all(gates.values()), "gates": gates,
            "aggregate_normalized_token_time": aggregate,
            "aggregate_median_improvement_fraction": aggregate_improvement,
            "worst_managed_bytes_ratio": max(managed_ratios), "worst_policy_cpu_fraction": max(cpu_fractions),
            "worst_replay_regret": max(replay_regret["hot"][candidate["hot"]],
                                         replay_regret["cold"][candidate["cold"]]),
            "policy_cpu_ns_per_demand": policy_ns[candidate["hot"].split("-")[0]] +
                                         policy_ns[candidate["cold"].split("-")[0]],
            "protected_resume": protected}


def boundary_budget(boundaries: dict[str, Any], name: str, model_format: str, scope: str, w: int,
                    headroom: dict[str, Any]) -> dict[str, Any]:
    passing = [row for row in boundaries["rows"] if row["name"] == name and row["tier"] == "cold"
               and row["disposition"] == "pass"]
    by_slots: dict[int, list[dict[str, Any]]] = {}
    for row in passing: by_slots.setdefault(row["slots"], []).append(row)
    cells = []
    repetitions = boundaries["rules"]["fresh_process_repetitions_per_cell"]
    for slots, rows in sorted(by_slots.items()):
        if len(rows) != repetitions: continue
        timings = [row["token_mean_us"] for row in rows]
        interval = paired_interval(timings, [0.0]*len(timings))
        cells.append({"slots": slots, "budget_bytes": rows[0]["requested_bytes"],
                      "median_token_time_us": statistics.median(timings), "mean_ci95_us": interval,
                      "minimum_ready_resident_ratio": min(row["resident_ratio"] for row in rows)})
    if not cells: raise RuntimeError(f"no safe online budget cell for {name}")
    fastest = min(cells, key=lambda cell: (cell["median_token_time_us"], cell["budget_bytes"]))
    equivalent = [cell for cell in cells if cell["median_token_time_us"] <= 1.03*fastest["median_token_time_us"]
                  and cell["mean_ci95_us"]["ci95_low"] <= fastest["mean_ci95_us"]["ci95_high"]
                  and cell["mean_ci95_us"]["ci95_high"] >= fastest["mean_ci95_us"]["ci95_low"]]
    selected = min(equivalent, key=lambda cell: cell["budget_bytes"])
    attempted = [row for row in boundaries["rows"] if row["name"] == name and row["tier"] == "cold"]
    footprint = attempted[0]["requested_bytes"]//attempted[0]["slots"]
    w_slots = w//footprint
    return {"model_format": model_format, "scope": scope,
            "recommended_cold_bytes": selected["budget_bytes"],
            "decode_w_bytes": w, "budget_over_w": selected["budget_bytes"]/w,
            "selection_rule": "lowest median; within 3% with overlapping 95% mean intervals chooses fewer bytes",
            "selected_cell": selected, "safe_cells": cells,
            "immediate_w_minus_attempted": any(row["slots"] == w_slots - 1 for row in attempted),
            "immediate_w_plus_attempted": any(row["slots"] == w_slots + 1 for row in attempted),
            "headroom": headroom, "transport": "BUFFERED runtime default; DIRECT_IO measured as sensitivity",
            "runtime_auto_sizing": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--residency", type=Path, required=True)
    parser.add_argument("--waste", type=Path, required=True)
    parser.add_argument("--statistics", type=Path, required=True)
    parser.add_argument("--prefill-protection", type=Path, required=True)
    parser.add_argument("--policy-benchmark", type=Path, required=True)
    parser.add_argument("--online-boundaries", type=Path, required=True)
    parser.add_argument("--transport", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    replay = json.loads(args.replay.read_text())
    working = json.loads(args.working_sets.read_text())
    residency = json.loads(args.residency.read_text())
    waste = json.loads(args.waste.read_text())
    stats = json.loads(args.statistics.read_text())
    prefill = json.loads(args.prefill_protection.read_text())
    benchmark = json.loads(args.policy_benchmark.read_text())
    boundaries = json.loads(args.online_boundaries.read_text())
    transport = json.loads(args.transport.read_text())
    if any(value["status"] != "pass" for value in
           (working, residency, waste, stats, prefill, benchmark, boundaries, transport)):
        raise RuntimeError("selection input is not a passing frozen artifact")
    baseline = stats["plan"]["baseline"]
    eligible = [profile for profile in stats["finalists"] if profile != baseline]
    regret = {tier: {entry["config"]: entry["worst_managed_byte_regret"]
                     for entry in replay["shortlist"][tier]["tuning_winners"]}
              for tier in ("hot", "cold")}
    evaluations = []
    for candidate in eligible:
        rows = [row for row in stats["comparisons"] if row["candidate"] == candidate]
        if len(rows) != 3:
            raise RuntimeError(f"global finalist lacks three mandatory model cells: {candidate}")
        evaluations.append(candidate_gate(candidate, rows, benchmark, prefill, regret))
    qualifiers = [evaluation for evaluation in evaluations if evaluation["qualifies"]]
    if qualifiers:
        simplicity = {"LRU": 0, "SLRU": 1, "LFRU": 2, "LFU_AGING": 3}
        qualifiers.sort(key=lambda value: (-value["aggregate_median_improvement_fraction"],
            value["worst_replay_regret"], value["policy_cpu_ns_per_demand"],
            simplicity[value["candidate"]["hot"].split("-")[0]],
            simplicity[value["candidate"]["cold"].split("-")[0]],
            value["candidate"]["hot"], value["candidate"]["cold"]))
        selected = qualifiers[0]["candidate"]
        disposition = "non-LRU finalist satisfies every frozen default gate"
    else:
        selected = baseline
        disposition = "no non-LRU finalist satisfies every frozen default gate; retain exact global LRU/ALWAYS"
    ws_by_name = {case["name"]: case for case in working["cases"]}
    budgets = []
    for boundary_name, working_name, model_format, scope in (
        ("tiny-f16-original", "tiny-f16-original-cold-lru-cpu-background-off", "tiny-k3-f16",
         "two-token tiny-K3 CPU_FALLBACK boundary workload"),
        ("tiny-mxfp4-original", "tiny-mxfp4-original-cold-lfu-auto-background-on", "tiny-k3-mxfp4",
         "two-token tiny-K3 CPU_FALLBACK boundary workload"),
        ("qwen15-moe-f16", "qwen15-moe-f16-cold-lru-cpu-background-off", "qwen1.5-moe-f16",
         "one-token accepted Qwen bootstrap; not a long-decode recommendation")):
        case = ws_by_name[working_name]
        budgets.append(boundary_budget(boundaries, boundary_name, model_format, scope,
                                       case["token_working_set_bytes"]["decode"]["max"], working["headroom"]))
    full_k3_w = working["full_k3_mxfp4"]["theoretical_token_working_set_bytes"]
    budgets.append({"model_format": "full-k3-mxfp4-exact-layout", "recommended_cold_bytes": full_k3_w,
                    "decode_w_bytes": full_k3_w, "budget_over_w": 1.0,
                    "reason": "safe exact-layout mechanism/residency W cell; no quality or token-throughput claim",
                    "ready_resident_ratio": next(row["ready_resident_ratio"] for row in residency["full_k3_cells"]
                                                  if row["name"] == "at-w"),
                    "paging_cliff": False, "headroom": working["headroom"],
                    "transport": "not an inference transport claim", "runtime_auto_sizing": False})
    output = {
        "schema_version": "phase9-selection-v1", "status": "pass",
        "candidate_heads": {"project": head(ROOT), "nested": head(ROOT / "llama.cpp")},
        "inputs": {name: file_identity(path) for name, path in (
            ("replay", args.replay), ("working_sets", args.working_sets), ("residency", args.residency),
            ("waste", args.waste), ("statistics", args.statistics),
            ("prefill_protection", args.prefill_protection), ("policy_benchmark", args.policy_benchmark),
            ("online_boundaries", args.online_boundaries), ("transport", args.transport))},
        "rule_frozen_before_results": True, "global_default_eligible_only": True,
        "baseline": baseline, "evaluations": evaluations, "selected": selected,
        "selection_disposition": disposition, "per_layer_default_eligible": False,
        "memory_safety": {"all_full_k3_cells_non_cliff": all(not row.get("paging_cliff", False)
            for row in residency["full_k3_cells"] if row.get("status") == "pass"),
            "ready_resident_ratio_at_w": next(row["ready_resident_ratio"] for row in residency["full_k3_cells"]
                if row["name"] == "at-w"), "headroom_preserved": True},
        "waste_disposition": waste["interpretation"], "transport_sensitivity": {
            "output_identity_exact": transport["output_identity_exact"],
            "accepted_direct_io_capability": transport["accepted_direct_io_capability"]},
        "budget_recommendations": budgets,
        "limits": ["global defaults only", "single-request discrete-GPU envelope",
                   "budget recommendations do not install hidden runtime auto-sizing",
                   "full-K3 evidence is exact-layout memory/residency only"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "selected": selected, "output": str(args.output)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

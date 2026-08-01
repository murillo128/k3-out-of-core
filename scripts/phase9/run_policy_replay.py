#!/usr/bin/env python3
"""Run the predeclared Phase 9 offline policy matrix over Phase 2 evidence."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase2"))
from cache_simulator import Capacity, requests_from_trace, simulate_policy  # noqa: E402
from route_trace import read_route_trace  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from adapt_phase2_events import adapt_phase2, lru_config, sha256_file  # noqa: E402
from cache_policy_simulator import (  # noqa: E402
    Key,
    canonical_json,
    canonical_sha256,
    replay,
    validate_config,
    waste_hierarchy,
)


PINNED_WASTE = {
    "repository": "https://github.com/sqliteai/waste",
    "commit": "c4d45c5914d1d15643d201855128938e8fb1698a",
    "license": "Apache-2.0",
    "inspected_files": ["src/ecache.h", "src/ecache.c"],
    "implementation": "independent project-side semantic reproduction; no WASTE source imported",
}


def policy_config(policy: str, scope: str, parameter: int = 0,
                  admission: str = "ALWAYS", window: int = 0) -> dict[str, Any]:
    return {
        "schema_version": "cache-policy-config-v1",
        "policy": policy,
        "scope": scope,
        "slru_protected_ratio_bps": parameter if policy == "SLRU" else 0,
        "admission": admission,
        "admission_window_events": window,
        "lfu_aging_interval_events": parameter if policy == "LFU_AGING" else 0,
    }


def candidates(tier: str) -> list[dict[str, Any]]:
    result = []
    for scope in ("GLOBAL", "PER_LAYER"):
        result.extend([policy_config("LRU", scope), policy_config("LFRU", scope)])
        for ratio in (5000, 7500, 8750):
            result.append(policy_config("SLRU", scope, ratio))
            if tier == "hot":
                for window in (256, 1024, 4096):
                    result.append(policy_config("SLRU", scope, ratio, "FREQUENCY_WINDOW", window))
        for interval in (256, 1024, 4096):
            result.append(policy_config("LFU_AGING", scope, interval))
    for value in result:
        validate_config(value, tier.upper())
    return result


def config_name(value: dict[str, Any]) -> str:
    fields = [value["policy"], value["scope"]]
    if value["policy"] == "SLRU":
        fields.append(str(value["slru_protected_ratio_bps"]))
        fields.append(value["admission"])
        if value["admission"] == "FREQUENCY_WINDOW":
            fields.append(str(value["admission_window_events"]))
    if value["policy"] == "LFU_AGING":
        fields.append(str(value["lfu_aging_interval_events"]))
    return "-".join(fields)


def token_working_set_slots(trace: dict[str, Any], phase: str) -> list[int]:
    grouped: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for record in trace["records"]:
        if record["phase"] != phase:
            continue
        token = (record["request_ordinal"], record["position"])
        grouped[token].update((record["layer"], expert) for expert in record["selected_experts"])
    return [len(keys) for _, keys in sorted(grouped.items())]


def legal_budget_grid(w_slots: int, footprint: int, distinct_keys: int) -> list[dict[str, Any]]:
    requests = [
        ("0.50W", 0.50, "exact-or-ceil"),
        ("0.75W", 0.75, "exact-or-ceil"),
        ("W-minus-one", None, "exact"),
        ("W", 1.00, "exact"),
        ("W-plus-one", None, "exact"),
        ("1.25W", 1.25, "exact-or-ceil"),
        ("1.50W", 1.50, "exact-or-ceil"),
        ("2.00W", 2.00, "exact-or-ceil"),
        ("phase8-hot", None, "exact"),
        ("phase8-cold", None, "exact"),
        ("safe-evidence-ceiling", None, "exact"),
    ]
    raw_slots = [
        math.ceil(w_slots * 0.50), math.ceil(w_slots * 0.75), max(1, w_slots - 1), w_slots,
        min(distinct_keys, w_slots + 1), min(distinct_keys, math.ceil(w_slots * 1.25)),
        min(distinct_keys, math.ceil(w_slots * 1.50)), min(distinct_keys, math.ceil(w_slots * 2.00)),
        min(distinct_keys, 8), min(distinct_keys, 24), distinct_keys,
    ]
    deduplicated: dict[int, dict[str, Any]] = {}
    for (label, ratio, rounding), slots in zip(requests, raw_slots):
        entry = deduplicated.setdefault(slots, {
            "slots": slots,
            "bytes": slots * footprint,
            "labels": [],
            "rounding": rounding,
            "requested_ratio": ratio,
            "remainder_bytes": 0,
        })
        entry["labels"].append(label)
    return [deduplicated[slots] for slots in sorted(deduplicated)]


def p95_stall(summary: dict[str, int], footprint: int, cost_model: dict[str, Any]) -> float:
    count = summary["logical_requests"]
    rank = max(1, math.ceil(0.95 * count))
    cumulative = 0
    for source in ("hot", "cold", "backing_store"):
        cumulative += summary[f"{source}_hits"]
        if rank <= cumulative:
            parameters = cost_model["tiers"][source]
            return parameters["fixed_latency_us"] + footprint / parameters["bandwidth_bytes_per_second"] * 1e6
    raise ValueError("summary source counts are inconsistent")


def modeled_stall(summary: dict[str, int], footprint: int, cost_model: dict[str, Any]) -> dict[str, Any]:
    total = 0.0
    for source in ("hot", "cold", "backing_store"):
        parameters = cost_model["tiers"][source]
        service = parameters["fixed_latency_us"] + footprint / parameters["bandwidth_bytes_per_second"] * 1e6
        total += summary[f"{source}_hits"] * service
    return {
        "model": cost_model["overlap_model"], "unit": "microseconds",
        "total": total, "mean": total / summary["logical_requests"],
        "p95": p95_stall(summary, footprint, cost_model),
    }


def compact_row(role: str, case_name: str, budget: dict[str, Any], hot_slots: int,
                cold_slots: int, config: dict[str, Any], output: dict[str, Any],
                footprint: int, cost_model: dict[str, Any]) -> dict[str, Any]:
    summary = output["summary"]
    managed = summary["backing_store_bytes"] + (summary["cold_bytes"] if role == "hot" else 0)
    tier = output["tiers"][role]
    return {
        "role": role,
        "case": case_name,
        "budget_slots": budget["slots"],
        "budget_labels": budget["labels"],
        "hot_slots": hot_slots,
        "cold_slots": cold_slots,
        "config": config_name(config),
        "config_digest": tier["config_digest"],
        "managed_bytes": managed,
        "modeled_p95_stall_us": p95_stall(summary, footprint, cost_model),
        "modeled_stall": modeled_stall(summary, footprint, cost_model),
        "summary": summary,
        "tier_counters": tier["counters"],
        "final_digest": tier["final_digest"],
        "domain_occupancy": tier["domains"],
        "by_phase": output["analysis"]["by_phase"],
        "by_layer": output["analysis"]["by_layer"],
        "segment_occupancy": output["analysis"]["tiers"][role]["segment_occupancy"],
        "policy_work": output["analysis"]["tiers"][role]["policy_work"],
    }


def select_shortlist(rows: list[dict[str, Any]], configs: list[dict[str, Any]], tier: str) -> dict[str, Any]:
    by_cell: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_cell[(row["case"], row["budget_slots"])].append(row)
    best = {cell: min(row["managed_bytes"] for row in values) for cell, values in by_cell.items()}
    scores = []
    for config in configs:
        name = config_name(config)
        selected = [row for row in rows if row["config"] == name]
        regrets = [
            (row["managed_bytes"] - best[(row["case"], row["budget_slots"])]) /
            max(1, best[(row["case"], row["budget_slots"])])
            for row in selected
        ]
        scores.append({
            "config": name,
            "policy": config["policy"],
            "scope": config["scope"],
            "worst_managed_byte_regret": max(regrets),
            "mean_managed_byte_regret": sum(regrets) / len(regrets),
            "mean_modeled_p95_stall_us": sum(row["modeled_p95_stall_us"] for row in selected) / len(selected),
            "canonical_distance": (
                abs(config["slru_protected_ratio_bps"] - 7500) +
                (abs(int(math.log2(config["admission_window_events"])) - 10) if config["admission_window_events"] else 0) +
                (abs(int(math.log2(config["lfu_aging_interval_events"])) - 10) if config["lfu_aging_interval_events"] else 0)
            ),
            "numeric_parameter": (
                config["slru_protected_ratio_bps"], config["admission_window_events"],
                config["lfu_aging_interval_events"],
            ),
        })
    key = lambda item: (
        item["worst_managed_byte_regret"], item["mean_managed_byte_regret"],
        item["mean_modeled_p95_stall_us"], item["canonical_distance"], item["numeric_parameter"], item["config"],
    )
    tuning_winners = []
    for family in sorted({(entry["policy"], entry["scope"]) for entry in scores}):
        tuning_winners.append(min((entry for entry in scores if (entry["policy"], entry["scope"]) == family), key=key))
    global_lru = next(entry for entry in tuning_winners if entry["policy"] == "LRU" and entry["scope"] == "GLOBAL")
    global_non_lru = sorted((entry for entry in tuning_winners if entry["policy"] != "LRU" and entry["scope"] == "GLOBAL"), key=key)[:2]
    per_layer = min((entry for entry in tuning_winners if entry["scope"] == "PER_LAYER"), key=key)
    return {
        "tier": tier,
        "rule": [
            "lowest worst-cell managed-byte regret", "lowest mean regret",
            "lowest modeled p95 stall", "closest canonical default", "lower numeric parameter",
        ],
        "tuning_winners": sorted(tuning_winners, key=key),
        "global_retained": [global_lru, *global_non_lru],
        "per_layer_explicit_comparator": per_layer,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--native-replay", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-cases", type=int)
    arguments = parser.parse_args()
    native_replay = arguments.native_replay.resolve()
    if not native_replay.is_file():
        raise SystemExit(f"native replay does not exist: {native_replay}")
    corpus = arguments.corpus.resolve()
    index_path = corpus / "corpus-index.json"
    index = json.loads(index_path.read_text())
    trace_paths = sorted((corpus / "traces").glob("*.bin"))
    if arguments.max_cases is not None:
        trace_paths = trace_paths[:arguments.max_cases]
    if not trace_paths:
        raise SystemExit("no Phase 2 traces found")
    cost_manifest_path = ROOT / "results/2026-07-29/skynet/phase2-observability/phase3-simulation-manifest-v1.json"
    cost_model = json.loads(cost_manifest_path.read_text())["cost_model"]
    maps = {
        artifact: ROOT / f"results/2026-07-29/skynet/phase2-observability/phase2-{artifact}-expert-storage-map-v1.json"
        for artifact in ("f16", "mxfp4")
    }
    hot_configs, cold_configs = candidates("hot"), candidates("cold")
    traces: dict[Path, dict[str, Any]] = {path: read_route_trace(path) for path in trace_paths}
    format_w: dict[str, int] = {}
    for artifact in maps:
        values = [slot for path, trace in traces.items() if path.name.startswith(artifact + "-")
                  for slot in token_working_set_slots(trace, "DECODE")]
        format_w[artifact] = max(values) if values else 14

    rows: list[dict[str, Any]] = []
    oracle_checks = []
    waste_rows = []
    belady_rows = []
    replay_identities = []
    for trace_path in trace_paths:
        artifact = "mxfp4" if trace_path.name.startswith("mxfp4-") else "f16"
        storage_map_path = maps[artifact]
        storage_map = json.loads(storage_map_path.read_text())
        footprint = storage_map["entries"][0]["atomic_bundle_bytes"]
        trace = traces[trace_path]
        base_input, lineage = adapt_phase2(trace_path, storage_map_path, 1, 1, lru_config(), lru_config())
        sequence = [
            (Key(demand["layer"], demand["expert"]), demand["logical_payload_bytes"], checkpoint["phase"])
            for request in base_input["requests"] for checkpoint in request["checkpoints"] for demand in checkpoint["demands"]
        ]
        grid = legal_budget_grid(format_w[artifact], footprint, len(storage_map["entries"]))
        phase2_requests = requests_from_trace(trace, storage_map)
        # Cross-check the immutable Phase 2 scenario cells exactly.  Those are
        # the published oracle contract; Phase 9's additional boundary cells
        # deliberately do not add new claims to immutable Phase 2 output.
        for hot_slots, cold_slots, scenario in (
            (1, 1, "one-expert"), (8, 24, "bounded-hierarchy"),
            (len(storage_map["entries"]), len(storage_map["entries"]), "full-working-set"),
        ):
            oracle_value = copy.deepcopy(base_input)
            oracle_value["hot"]["slots"], oracle_value["cold"]["slots"] = hot_slots, cold_slots
            oracle_output = replay(oracle_value)
            with tempfile.TemporaryDirectory(prefix="phase9-native-oracle-") as directory:
                input_path = Path(directory) / "input.json"
                output_path = Path(directory) / "output.json"
                input_path.write_text(canonical_json(oracle_value))
                completed = subprocess.run(
                    [str(native_replay), "--input", str(input_path), "--output", str(output_path)],
                    capture_output=True, text=True, check=False,
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        f"native Phase 2 LRU replay failed: {trace_path.name} {scenario}: "
                        f"{completed.stderr[-2000:]}")
                native_output = json.loads(output_path.read_text())
            if native_output != oracle_output:
                raise RuntimeError(f"native/Python LRU replay mismatch: {trace_path.name} {scenario}")
            oracle = simulate_policy(
                phase2_requests, Capacity(hot_slots, hot_slots * footprint),
                Capacity(cold_slots, cold_slots * footprint), cost_model, "lru",
            )
            expected = {
                "logical_requests": oracle["overall"]["logical_requests"],
                "hot_hits": oracle["overall"]["tiers"]["hot"]["hits"],
                "cold_hits": oracle["overall"]["tiers"]["cold"]["hits"],
                "backing_store_hits": oracle["overall"]["tiers"]["backing_store"]["hits"],
                "hot_bytes": oracle["overall"]["tiers"]["hot"]["bytes_transferred"],
                "cold_bytes": oracle["overall"]["tiers"]["cold"]["bytes_transferred"],
                "backing_store_bytes": oracle["overall"]["tiers"]["backing_store"]["bytes_transferred"],
            }
            exact = oracle_output["summary"] == expected and all([
                oracle_output["tiers"]["hot"]["counters"]["admissions"] == oracle["overall"]["cache_activity"]["hot"]["admissions"],
                oracle_output["tiers"]["hot"]["counters"]["evictions"] == oracle["overall"]["cache_activity"]["hot"]["evictions"],
                oracle_output["tiers"]["cold"]["counters"]["admissions"] == oracle["overall"]["cache_activity"]["cold"]["admissions"],
                oracle_output["tiers"]["cold"]["counters"]["evictions"] == oracle["overall"]["cache_activity"]["cold"]["evictions"],
            ])
            if not exact:
                raise RuntimeError(f"Phase 2 LRU oracle mismatch: {trace_path.name} {scenario}")
            oracle_checks.append({
                "case": trace_path.name,
                "scenario": scenario,
                "status": "pass",
                "native_python_exact": True,
                "replay_sha256": canonical_sha256(oracle_output),
            })
        for budget in grid:
            b = budget["slots"]
            pairs = {
                "hot": (b, min(len(storage_map["entries"]), max(b, 2 * b, 24))),
                "cold": (min(b, max(1, min(8, b // 3))), b),
            }
            for role, (hot_slots, cold_slots) in pairs.items():
                value = copy.deepcopy(base_input)
                value["hot"]["slots"], value["cold"]["slots"] = hot_slots, cold_slots
                value["hot"]["config"], value["cold"]["config"] = lru_config(), lru_config()
                selected_configs = hot_configs if role == "hot" else cold_configs
                for candidate in selected_configs:
                    candidate_value = copy.deepcopy(value)
                    candidate_value[role]["config"] = candidate
                    output = replay(candidate_value, capture_events=False, include_analysis=True)
                    rows.append(compact_row(role, trace_path.name, budget, hot_slots, cold_slots,
                                            candidate, output, footprint, cost_model))
                for policy in ("waste_sampled_lru", "waste_sampled_lfru"):
                    waste = waste_hierarchy(sequence, hot_slots, cold_slots, policy)
                    waste_rows.append({
                        "case": trace_path.name, "role": role, "budget_slots": b,
                        "hot_slots": hot_slots, "cold_slots": cold_slots, **waste,
                    })
                belady = simulate_policy(
                    phase2_requests, Capacity(hot_slots, hot_slots * footprint),
                    Capacity(cold_slots, cold_slots * footprint), cost_model, "belady_min",
                )
                belady_rows.append({
                    "case": trace_path.name, "role": role, "budget_slots": b,
                    "hot_slots": hot_slots, "cold_slots": cold_slots,
                    "summary": {
                        "hot_hits": belady["overall"]["tiers"]["hot"]["hits"],
                        "cold_hits": belady["overall"]["tiers"]["cold"]["hits"],
                        "backing_store_hits": belady["overall"]["tiers"]["backing_store"]["hits"],
                        "backing_store_bytes": belady["overall"]["tiers"]["backing_store"]["bytes_transferred"],
                    },
                })
        replay_identities.append(lineage)

    hot_rows = [row for row in rows if row["role"] == "hot"]
    cold_rows = [row for row in rows if row["role"] == "cold"]
    hot_shortlist = select_shortlist(hot_rows, hot_configs, "hot")
    cold_shortlist = select_shortlist(cold_rows, cold_configs, "cold")
    profiles = [
        {"hot": hot["config"], "cold": cold["config"]}
        for hot in hot_shortlist["global_retained"] for cold in cold_shortlist["global_retained"]
    ]
    output = {
        "schema_version": "phase9-policy-replay-matrix-v1",
        "status": "pass",
        "inputs": {
            "corpus_index": {"path": str(index_path), "sha256": sha256_file(index_path)},
            "corpus_archive_sha256": "6aa924a6c18bee4e2490f317ced836bcc4740c3ec63e9427a95951e79a649a5f",
            "published_revision": "2d838d6b4d0aca4e9af1e7d899e57ad29330c72e",
            "cost_model": {"path": str(cost_manifest_path), "sha256": sha256_file(cost_manifest_path)},
            "storage_maps": {name: {"path": str(path), "sha256": sha256_file(path)} for name, path in maps.items()},
            "native_replay": {"path": str(native_replay), "sha256": sha256_file(native_replay)},
            "case_count": len(trace_paths),
            "index_case_count": len(index["cases"]),
        },
        "predeclared_matrix": {
            "hot_configurations": [config_name(value) for value in hot_configs],
            "cold_configurations": [config_name(value) for value in cold_configs],
            "format_w_slots": format_w,
            "row_count": len(rows),
        },
        "phase2_lru_oracle": {"status": "pass", "checks": oracle_checks},
        "rows": rows,
        "baselines": {"waste": {"attribution": PINNED_WASTE, "rows": waste_rows}, "belady_min": belady_rows},
        "shortlist": {
            "rule_frozen_before_results": True,
            "hot": hot_shortlist,
            "cold": cold_shortlist,
            "global_profiles": profiles,
            "global_profile_count": len(profiles),
            "per_layer_pair": {
                "hot": hot_shortlist["per_layer_explicit_comparator"]["config"],
                "cold": cold_shortlist["per_layer_explicit_comparator"]["config"],
                "default_eligible": False,
            },
        },
        "adapter_lineage_digest": canonical_sha256(replay_identities),
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(canonical_json(output))
    print(canonical_json({
        "status": "pass", "cases": len(trace_paths), "rows": len(rows),
        "oracle_checks": len(oracle_checks), "global_profiles": len(profiles),
        "output": str(arguments.output), "sha256": sha256_file(arguments.output),
    }), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

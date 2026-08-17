#!/usr/bin/env python3
"""Build the deterministic issue-102 16..512-token horizon analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


BOUNDARIES = (16, 32, 64, 128, 256, 512)
EXPECTED_CACHE_BYTES = 137728475136
EXPECTED_CACHE_SLOTS = 7849
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
EXPECTED_BINARY_SHA256 = "aef1c347bad779fde7842b1364f3d0e2b5b721a049a9b64200b6be7802d5f25d"
EXPECTED_RUNNER_SHA256 = "0e09960035666f15bfc82cef2a8dd81358f744a848f3f1f633d27d420afeca92"

CELL_SPECS = (
    ("sentinel", "issue102-sentinel", "S2_P50", "01-sentinel-s2-post-reboot-gate", 1, 1),
    ("sentinel", "issue102-sentinel", "EXACT", "02-sentinel-exact", 2, 1),
    ("low_hit", "04-factual-b4", "S2_P50", "03-low-hit-s2", 3, 2),
    ("low_hit", "04-factual-b4", "EXACT", "04-low-hit-exact", 4, 2),
    ("high_hit", "10-planning-b2", "S2_P50", "05-high-hit-s2", 5, 3),
    ("high_hit", "10-planning-b2", "EXACT", "06-high-hit-exact", 6, 3),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-root", type=pathlib.Path, required=True)
    parser.add_argument("--continuity-gate", type=pathlib.Path, required=True)
    parser.add_argument("--unused-nvme", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def all_zero(source: dict[str, Any]) -> bool:
    return all(value == 0 for value in source.values())


def ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator == 0:
        raise ValueError("zero denominator in horizon analysis")
    return numerator / denominator


def require_clean(result: dict[str, Any], envelope: dict[str, Any], unused_nvme: str) -> dict[str, bool]:
    storage = result["measured"]["storage_delta"]
    asynchronous = result["measured"]["async_delta"]
    scheduler = result["measured"]["scheduler_delta"]
    memory = result["resources"]["system_memory"]
    vmstat = envelope["delta"]["vmstat"]
    safety = {
        "result_pass": result["status"] == "pass" and result["exit_status"] == 0,
        "runner_pass": envelope["exit_status"] == 0,
        "empty_cache_start": result["preflight"]["process_start_occupancy"] == 0,
        "frozen_capacity": (
            result["preflight"]["initial_cold"]["capacity"] == EXPECTED_CACHE_SLOTS
            and result["preflight"]["initial_cold"]["actual_bytes"] == EXPECTED_CACHE_BYTES
            and result["measured"]["cold_before"]["capacity"] == EXPECTED_CACHE_SLOTS
            and result["measured"]["cold_after"]["capacity"] == EXPECTED_CACHE_SLOTS
            and result["measured"]["cold_before"]["occupancy"] == EXPECTED_CACHE_SLOTS
            and result["measured"]["cold_after"]["occupancy"] == EXPECTED_CACHE_SLOTS
        ),
        "boundaries_exact": (
            result["horizon"]["snapshot_boundaries"] == list(BOUNDARIES)
            and [row["generated_tokens"] for row in result["horizon"]["cumulative_snapshots"]]
            == list(BOUNDARIES)
        ),
        "generated_512": result["output"]["generated_token_count"] == 512,
        "no_result_io_error": all(storage[key] == 0 for key in ("cancelled_reads", "short_reads", "io_errors")),
        "no_io_fallback": (
            asynchronous["read_requests_cancelled"] == 0
            and asynchronous["buffered_fallback_operations"] == 0
            and asynchronous["synchronous_fallback_operations"] == 0
        ),
        "scheduler_clean": all(
            scheduler[key] == 0
            for key in ("terminal_failed", "terminal_cancelled", "stale_completions", "active_requests", "queued_requests")
        ),
        "terminal_references_zero": (
            all_zero(result["resources"]["terminal_references"])
            and result["resources"]["terminal_scheduler_active_requests"] == 0
            and result["resources"]["terminal_scheduler_queued_requests"] == 0
        ),
        "no_routing_failure": result["routing"]["stats"]["failures"] == 0,
        "no_swap": (
            result["resources"]["vm_swap_kib"] == 0
            and envelope["samples"]["peak_process_swap_kib"] == 0
            and vmstat["pswpin"] == 0
            and vmstat["pswpout"] == 0
        ),
        "no_reclaim_refault_or_oom": (
            vmstat["pgscan_direct"] == 0
            and vmstat["pgscan_kswapd"] == 0
            and vmstat["workingset_refault_file"] == 0
            and vmstat["oom_kill"] == 0
            and all_zero(envelope["delta"]["cgroup_memory_events"])
            and not any(envelope["memory_pressure_total_delta_usec"].values())
        ),
        "pressure_circuit_closed": memory["pressure_rejections"] == 0 and not memory["pressure_circuit_open"],
        "unused_nvme_zero_reads": (
            envelope["delta"]["nvme"][unused_nvme]["read_bytes"] == 0
            and envelope["delta"]["nvme"][unused_nvme]["read_operations"] == 0
        ),
    }
    if not all(safety.values()):
        failed = sorted(key for key, value in safety.items() if not value)
        raise ValueError(f"cell failed safety validation: {failed}")
    return safety


def cumulative_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    tokens = snapshot["generated_tokens"]
    cold = snapshot["cold_delta"]
    storage = snapshot["storage_delta"]
    routing = snapshot["routing"]
    return {
        "generated_tokens": tokens,
        "elapsed_s": snapshot["elapsed_s"],
        "hits": cold["hits"],
        "misses": cold["misses"],
        "requests": cold["requests"],
        "hit_ratio": ratio(cold["hits"], cold["requests"]),
        "backing_loads": storage["backing_loads"],
        "loads_per_token": ratio(storage["backing_loads"], tokens),
        "backing_bytes": storage["backing_bytes"],
        "bytes_per_token": ratio(storage["backing_bytes"], tokens),
        "occupancy_before": cold["occupancy_before"],
        "occupancy_after": cold["occupancy_after"],
        "evictions": cold["evictions"],
        "routing_decisions": routing["decisions"],
        "changed_decisions": routing["changed_decisions"],
        "changed_fraction": ratio(routing["changed_decisions"], routing["decisions"]) if routing["decisions"] else 0.0,
        "realized_swaps": routing["swaps"],
        "swaps_per_token": ratio(routing["swaps"], tokens),
        "cumulative_score_regret": routing["cumulative_score_regret"],
        "mean_regret_per_swap": ratio(routing["cumulative_score_regret"], routing["swaps"]) if routing["swaps"] else 0.0,
        "generated_token_hash": snapshot["output_prefix"]["generated_token_hash"],
        "first_eog_position": snapshot["output_prefix"]["first_eog_position"],
    }


def subtract(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    start = 1 if previous is None else previous["generated_tokens"] + 1
    count = current["generated_tokens"] if previous is None else current["generated_tokens"] - previous["generated_tokens"]
    prior = previous or {
        "elapsed_s": 0.0,
        "hits": 0,
        "misses": 0,
        "requests": 0,
        "backing_loads": 0,
        "backing_bytes": 0,
        "evictions": 0,
        "routing_decisions": 0,
        "changed_decisions": 0,
        "realized_swaps": 0,
        "cumulative_score_regret": 0.0,
    }
    hits = current["hits"] - prior["hits"]
    misses = current["misses"] - prior["misses"]
    requests = current["requests"] - prior["requests"]
    loads = current["backing_loads"] - prior["backing_loads"]
    backing_bytes = current["backing_bytes"] - prior["backing_bytes"]
    decisions = current["routing_decisions"] - prior["routing_decisions"]
    changed = current["changed_decisions"] - prior["changed_decisions"]
    swaps = current["realized_swaps"] - prior["realized_swaps"]
    regret = current["cumulative_score_regret"] - prior["cumulative_score_regret"]
    return {
        "start_token": start,
        "end_token": current["generated_tokens"],
        "token_count": count,
        "elapsed_s": current["elapsed_s"] - prior["elapsed_s"],
        "hits": hits,
        "misses": misses,
        "requests": requests,
        "hit_ratio": ratio(hits, requests),
        "backing_loads": loads,
        "loads_per_token": ratio(loads, count),
        "backing_bytes": backing_bytes,
        "bytes_per_token": ratio(backing_bytes, count),
        "occupancy_before": current["occupancy_before"] if previous is None else previous["occupancy_after"],
        "occupancy_after": current["occupancy_after"],
        "evictions": current["evictions"] - prior["evictions"],
        "routing_decisions": decisions,
        "changed_decisions": changed,
        "changed_fraction": ratio(changed, decisions) if decisions else 0.0,
        "realized_swaps": swaps,
        "swaps_per_token": ratio(swaps, count),
        "score_regret": regret,
        "mean_regret_per_swap": ratio(regret, swaps) if swaps else 0.0,
        "output_prefix_hash_at_end": current["generated_token_hash"],
        "first_eog_position_at_end": current["first_eog_position"],
    }


def paired_row(s2: dict[str, Any], exact: dict[str, Any], label_key: str) -> dict[str, Any]:
    if s2[label_key] != exact[label_key]:
        raise ValueError("paired horizon/interval boundaries differ")
    return {
        label_key: s2[label_key],
        "s2_hit_ratio": s2["hit_ratio"],
        "exact_hit_ratio": exact["hit_ratio"],
        "s2_minus_exact_hit_ratio": s2["hit_ratio"] - exact["hit_ratio"],
        "s2_misses": s2["misses"],
        "exact_misses": exact["misses"],
        "s2_over_exact_miss_ratio": ratio(s2["misses"], exact["misses"]),
        "relative_miss_reduction": 1.0 - ratio(s2["misses"], exact["misses"]),
        "s2_backing_loads": s2["backing_loads"],
        "exact_backing_loads": exact["backing_loads"],
        "relative_load_reduction": 1.0 - ratio(s2["backing_loads"], exact["backing_loads"]),
        "s2_backing_bytes": s2["backing_bytes"],
        "exact_backing_bytes": exact["backing_bytes"],
        "relative_backing_byte_reduction": 1.0 - ratio(s2["backing_bytes"], exact["backing_bytes"]),
    }


def classify(curve: list[float]) -> dict[str, Any]:
    differences = [right - left for left, right in zip(curve, curve[1:])]
    if all(value == 0 for value in differences):
        label = "PLATEAUING"
    elif all(value >= 0 for value in differences):
        label = "AMPLIFYING"
    elif all(value <= 0 for value in differences):
        label = "DECAYING"
    else:
        label = "NON_MONOTONIC"
    net = curve[-1] - curve[0]
    return {
        "classification": label,
        "classification_rule": (
            "AMPLIFYING iff every adjacent cumulative relative-miss-reduction change is nonnegative; "
            "DECAYING iff every change is nonpositive; PLATEAUING iff all are exactly zero; "
            "otherwise NON_MONOTONIC"
        ),
        "adjacent_changes": differences,
        "net_change_16_to_512": net,
        "net_direction_16_to_512": "INCREASE" if net > 0 else "DECREASE" if net < 0 else "UNCHANGED",
    }


def main() -> int:
    args = arguments()
    root = args.long_root.resolve()
    gate_path = args.continuity_gate.resolve()
    output_path = args.output.resolve()
    gate = load(gate_path)
    if gate["status"] != "pass" or gate["disposition"] != "CONTINUITY_PASS_PROCEED":
        raise ValueError("continuity gate is not a pass")

    cells: list[dict[str, Any]] = []
    by_workload: dict[str, dict[str, dict[str, Any]]] = {}
    project_shas: set[str] = set()
    for workload, case_id, point, directory, ordinal, triplet in CELL_SPECS:
        result_path = root / directory / "result.json"
        envelope_path = root / directory / "envelope.json"
        result = load(result_path)
        envelope = load(envelope_path)
        if result["case"]["id"] != case_id or result["point"] != point:
            raise ValueError(f"case/policy mismatch in {directory}")
        if result["protocol"] != "full-prompt" or result["preflight"]["pass"] is not True:
            raise ValueError(f"protocol/preflight mismatch in {directory}")
        if (
            envelope["campaign"] != "issue102-long-horizon"
            or envelope["run_ordinal"] != ordinal
            or envelope["order"] != ordinal
            or envelope["triplet"] != triplet
            or envelope["point"] != point
        ):
            raise ValueError(f"execution-order mismatch in {directory}")
        identities = envelope["identities"]
        if (
            identities["nested"] != EXPECTED_NESTED_SHA
            or identities["binary_sha256"] != EXPECTED_BINARY_SHA256
            or identities["runner_sha256"] != EXPECTED_RUNNER_SHA256
        ):
            raise ValueError(f"runtime identity mismatch in {directory}")
        safety = require_clean(result, envelope, args.unused_nvme)
        cumulative = [cumulative_row(row) for row in result["horizon"]["cumulative_snapshots"]]
        intervals = [subtract(row, cumulative[index - 1] if index else None) for index, row in enumerate(cumulative)]
        project_shas.add(identities["project"])
        cell = {
            "workload": workload,
            "directory": directory,
            "case": result["case"],
            "point": point,
            "run_ordinal": ordinal,
            "triplet": triplet,
            "inputs": {
                "result": {"path": str(result_path), "sha256": sha256(result_path)},
                "envelope": {"path": str(envelope_path), "sha256": sha256(envelope_path)},
            },
            "identities": identities,
            "safety": safety,
            "decode_tok_s_diagnostic_only": result["measured"]["decode_tok_s"],
            "first_eog_position": result["horizon"]["first_eog_position"],
            "generated_token_hash": result["output"]["generated_token_hash"],
            "cumulative": cumulative,
            "intervals": intervals,
        }
        cells.append(cell)
        by_workload.setdefault(workload, {})[point] = cell

    expected_workloads = {"sentinel", "low_hit", "high_hit"}
    if set(by_workload) != expected_workloads or any(set(pair) != {"S2_P50", "EXACT"} for pair in by_workload.values()):
        raise ValueError("the exact 3x2 paired matrix is incomplete")

    pairs: list[dict[str, Any]] = []
    for workload in ("sentinel", "low_hit", "high_hit"):
        s2 = by_workload[workload]["S2_P50"]
        exact = by_workload[workload]["EXACT"]
        cumulative_pairs = [
            paired_row(s2_row, exact_row, "generated_tokens")
            for s2_row, exact_row in zip(s2["cumulative"], exact["cumulative"])
        ]
        interval_pairs = []
        for s2_row, exact_row in zip(s2["intervals"], exact["intervals"]):
            row = paired_row(s2_row, exact_row, "end_token")
            row["start_token"] = s2_row["start_token"]
            row["token_count"] = s2_row["token_count"]
            interval_pairs.append(row)
        curve = [row["relative_miss_reduction"] for row in cumulative_pairs]
        classification = classify(curve)
        at_64 = next(row for row in cumulative_pairs if row["generated_tokens"] == 64)
        at_512 = next(row for row in cumulative_pairs if row["generated_tokens"] == 512)
        final_interval = next(row for row in interval_pairs if row["end_token"] == 512)
        pairs.append({
            "workload": workload,
            "case": s2["case"],
            "cumulative": cumulative_pairs,
            "intervals": interval_pairs,
            "curve_classification": classification,
            "stage_a_64_window_diagnostic": {
                "relative_miss_reduction_at_64": at_64["relative_miss_reduction"],
                "relative_miss_reduction_at_512": at_512["relative_miss_reduction"],
                "change_64_to_512": at_512["relative_miss_reduction"] - at_64["relative_miss_reduction"],
                "relative_miss_reduction_interval_257_512": final_interval["relative_miss_reduction"],
                "near_steady_state_classification": "NOT_THRESHOLD_CLASSIFIED",
            },
            "trajectory_identity": {
                "s2_generated_token_hash": s2["generated_token_hash"],
                "exact_generated_token_hash": exact["generated_token_hash"],
                "s2_first_eog_position": s2["first_eog_position"],
                "exact_first_eog_position": exact["first_eog_position"],
                "semantic_equivalence_claimed": False,
            },
        })

    output = {
        "schema_version": "phase13-6pg-long-horizon-analysis-v1",
        "status": "pass",
        "provenance": "MEASURED_PHYSICAL_DIAGNOSTIC",
        "scope": {
            "performance_acceptance_evidence": False,
            "semantic_quality_authority": False,
            "free_generation_feedback_included": True,
            "description": "Real free-generation EXACT/S2 locality curves; TPS is diagnostic context only.",
        },
        "continuity_gate": {"path": str(gate_path), "sha256": sha256(gate_path), "status": gate["status"]},
        "configuration": {
            "workloads": ["sentinel", "low_hit", "high_hit"],
            "policies": ["S2_P50", "EXACT"],
            "process_count": 6,
            "snapshot_boundaries": list(BOUNDARIES),
            "intervals": [[1, 16], [17, 32], [33, 64], [65, 128], [129, 256], [257, 512]],
            "cache_slots": EXPECTED_CACHE_SLOTS,
            "cache_bytes": EXPECTED_CACHE_BYTES,
            "n_ctx": 768,
            "decode_forwards": 512,
        },
        "runtime_identity": {
            "project_shas": sorted(project_shas),
            "project_sha_note": (
                "The first post-reboot gate ran at cf17c797; the remaining cells ran after the "
                "continuity-gate-only commit 5ae82a9. Runtime/helper/corpus/model identities are unchanged."
            ),
            "nested_llama_cpp": EXPECTED_NESTED_SHA,
            "helper_binary_sha256": EXPECTED_BINARY_SHA256,
            "runner_sha256": EXPECTED_RUNNER_SHA256,
        },
        "cells": cells,
        "pairs": pairs,
        "cross_workload_summary": {
            "primary_classifications": {row["workload"]: row["curve_classification"]["classification"] for row in pairs},
            "net_directions_16_to_512": {row["workload"]: row["curve_classification"]["net_direction_16_to_512"] for row in pairs},
            "consistent_direction_across_workloads": len({row["curve_classification"]["net_direction_16_to_512"] for row in pairs}) == 1,
            "steady_state_threshold_defined": False,
        },
        "disposition": "LONG_HORIZON_DIAGNOSTIC_COMPLETE",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output_path), "sha256": sha256(output_path), "status": output["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

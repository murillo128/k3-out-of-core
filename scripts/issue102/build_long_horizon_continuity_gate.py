#!/usr/bin/env python3
"""Validate the issue-102 long-horizon 64-token continuity gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def select(source: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in keys}


def all_zero(source: dict[str, Any]) -> bool:
    return all(value == 0 for value in source.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--long-result", type=pathlib.Path, required=True)
    parser.add_argument("--long-envelope", type=pathlib.Path, required=True)
    parser.add_argument("--stage-a-result", type=pathlib.Path, required=True)
    parser.add_argument("--unused-nvme", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()

    long_result = load(args.long_result)
    envelope = load(args.long_envelope)
    stage = load(args.stage_a_result)
    snapshots = long_result["horizon"]["cumulative_snapshots"]
    snapshot64 = next(item for item in snapshots if item["generated_tokens"] == 64)

    async_stable_keys = (
        "read_requests_submitted",
        "read_requests_completed",
        "read_requests_cancelled",
        "read_operations_completed",
        "read_bytes_completed",
        "queue_wait_samples",
        "ring_submissions",
        "ring_completions",
        "ring_request_batches",
        "peak_ring_batch_requests_lifetime",
        "peak_active_read_requests_lifetime",
        "peak_active_operations_lifetime",
        "peak_sq_occupancy_lifetime",
        "direct_read_operations",
        "direct_useful_bytes",
        "direct_aligned_bytes",
        "buffered_fallback_operations",
        "synchronous_fallback_operations",
    )
    routing_keys = (
        "ubatches",
        "layers",
        "decisions",
        "changed_decisions",
        "swaps",
        "cumulative_score_regret",
        "explicit_synchronizations",
        "failures",
    )
    preflight_keys = (
        "pass",
        "process_start_occupancy",
        "cpu_cold_only",
        "hot_capacity",
        "hot_pool_bytes",
        "first_miss_backing_read",
        "same_cache_residency_visible_to_routing",
        "initial_cold",
        "initial_storage",
        "initial_terminal_references",
    )
    fill_keys = (
        "tokens_to_full",
        "full_prompt_tokens",
        "cold",
        "cold_delta",
        "storage_delta",
        "scheduler_delta",
    )

    long_prefix_ids = long_result["output"]["generated_ids"][:64]
    stage_ids = stage["output"]["generated_ids"]
    comparisons = {
        "case_identity": long_result["case"] == stage["case"],
        "point": long_result["point"] == stage["point"] == "S2_P50",
        "protocol": long_result["protocol"] == stage["protocol"] == "full-prompt",
        "preflight_semantics": select(long_result["preflight"], preflight_keys)
        == select(stage["preflight"], preflight_keys),
        "fill_semantics": select(long_result["fill"], fill_keys)
        == select(stage["fill"], fill_keys),
        "fill_async_structural": select(long_result["fill"]["async_delta"], async_stable_keys)
        == select(stage["fill"]["async_delta"], async_stable_keys),
        "decode_cold_before": long_result["measured"]["cold_before"]
        == stage["measured"]["cold_before"],
        "decode_cold_after_64": snapshot64["cold"] == stage["measured"]["cold_after"],
        "decode_cold_delta_64": snapshot64["cold_delta"] == stage["measured"]["cold_delta"],
        "decode_storage_delta_64": snapshot64["storage_delta"]
        == stage["measured"]["storage_delta"],
        "decode_async_structural_64": select(snapshot64["async_delta"], async_stable_keys)
        == select(stage["measured"]["async_delta"], async_stable_keys),
        "decode_scheduler_delta_64": snapshot64["scheduler_delta"]
        == stage["measured"]["scheduler_delta"],
        "routing_64": select(snapshot64["routing"], routing_keys)
        == select(stage["routing"]["stats"], routing_keys),
        "generated_ids_64": long_prefix_ids == stage_ids,
        "generated_count_64": snapshot64["output_prefix"]["generated_token_count"]
        == stage["output"]["generated_token_count"] == 64,
        "generated_hash_64": snapshot64["output_prefix"]["generated_token_hash"]
        == stage["output"]["generated_token_hash"],
        "no_eog_through_64": snapshot64["output_prefix"]["first_eog_position"] is None,
    }

    boundaries = [16, 32, 64, 128, 256, 512]
    terminal = long_result["resources"]["terminal_references"]
    storage = long_result["measured"]["storage_delta"]
    async_delta = long_result["measured"]["async_delta"]
    system_memory = long_result["resources"]["system_memory"]
    vmstat = envelope["delta"]["vmstat"]
    safety = {
        "result_pass": long_result["status"] == "pass" and long_result["exit_status"] == 0,
        "runner_pass": envelope["exit_status"] == 0,
        "boundaries_exact": long_result["horizon"]["snapshot_boundaries"] == boundaries
        and [item["generated_tokens"] for item in snapshots] == boundaries,
        "generated_512": long_result["output"]["generated_token_count"] == 512,
        "empty_cache_start": long_result["preflight"]["process_start_occupancy"] == 0,
        "frozen_capacity": long_result["preflight"]["initial_cold"]["capacity"] == 7849
        and long_result["preflight"]["initial_cold"]["actual_bytes"] == 137728475136,
        "no_result_io_error": storage["cancelled_reads"] == 0
        and storage["short_reads"] == 0
        and storage["io_errors"] == 0,
        "no_io_fallback": async_delta["read_requests_cancelled"] == 0
        and async_delta["buffered_fallback_operations"] == 0
        and async_delta["synchronous_fallback_operations"] == 0,
        "no_routing_failure": long_result["routing"]["stats"]["failures"] == 0,
        "terminal_references_zero": all_zero(terminal)
        and long_result["resources"]["terminal_scheduler_active_requests"] == 0
        and long_result["resources"]["terminal_scheduler_queued_requests"] == 0,
        "no_swap": long_result["resources"]["vm_swap_kib"] == 0
        and envelope["samples"]["peak_process_swap_kib"] == 0
        and vmstat["pswpin"] == 0
        and vmstat["pswpout"] == 0,
        "no_reclaim_or_oom": vmstat["pgscan_direct"] == 0
        and vmstat["pgscan_kswapd"] == 0
        and vmstat["workingset_refault_file"] == 0
        and vmstat["oom_kill"] == 0
        and all_zero(envelope["delta"]["cgroup_memory_events"]),
        "no_pressure_circuit": system_memory["pressure_rejections"] == 0
        and not system_memory["pressure_circuit_open"],
        "unused_nvme_zero_reads": envelope["delta"]["nvme"][args.unused_nvme]["read_bytes"] == 0
        and envelope["delta"]["nvme"][args.unused_nvme]["read_operations"] == 0,
    }

    passed = all(comparisons.values()) and all(safety.values())
    output = {
        "schema_version": "phase13-6pg-long-horizon-continuity-gate-v1",
        "status": "pass" if passed else "fail",
        "inputs": {
            "long_result": {"path": str(args.long_result), "sha256": sha256(args.long_result)},
            "long_envelope": {"path": str(args.long_envelope), "sha256": sha256(args.long_envelope)},
            "stage_a_result": {"path": str(args.stage_a_result), "sha256": sha256(args.stage_a_result)},
        },
        "revisions": {
            "project": envelope["identities"]["project"],
            "nested_llama_cpp": envelope["identities"]["nested"],
            "helper_binary_sha256": envelope["identities"]["binary_sha256"],
            "runner_sha256": envelope["identities"]["runner_sha256"],
        },
        "configuration": {
            "case": long_result["case"],
            "point": long_result["point"],
            "protocol": long_result["protocol"],
            "cache_slots": long_result["preflight"]["initial_cold"]["capacity"],
            "cache_bytes": long_result["preflight"]["initial_cold"]["actual_bytes"],
            "decode_forwards": long_result["output"]["generated_token_count"],
            "snapshot_boundaries": boundaries,
        },
        "continuity_at_64": {
            "comparisons": comparisons,
            "excluded_async_scheduling_fields": [
                "queue_wait_us",
                "queue_wait_max_us_lifetime",
                "cq_empty_waits",
                "peak_cq_occupancy_lifetime",
            ],
            "generated_token_hash": snapshot64["output_prefix"]["generated_token_hash"],
            "cold_before_digest": long_result["measured"]["cold_before"]["residency_digest"],
            "cold_after_digest": snapshot64["cold"]["residency_digest"],
            "hit_ratio": snapshot64["cold_delta"]["hits"] / snapshot64["cold_delta"]["requests"],
            "backing_loads": snapshot64["storage_delta"]["backing_loads"],
            "backing_bytes": snapshot64["storage_delta"]["backing_bytes"],
            "changed_decisions": snapshot64["routing"]["changed_decisions"],
            "swaps": snapshot64["routing"]["swaps"],
            "cumulative_score_regret": snapshot64["routing"]["cumulative_score_regret"],
        },
        "safety": safety,
        "long_horizon_context": {
            "decode_tok_s": long_result["measured"]["decode_tok_s"],
            "backing_loads": long_result["measured"]["storage_delta"]["backing_loads"],
            "generated_token_hash": long_result["output"]["generated_token_hash"],
            "first_eog_position": long_result["horizon"]["first_eog_position"],
            "minimum_mem_available_kib": envelope["samples"]["minimum_mem_available_kib"],
        },
        "disposition": "CONTINUITY_PASS_PROCEED" if passed else "CONTINUITY_FAIL_STOP",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(output, indent=2, sort_keys=True) + "\n"
    args.output.write_text(encoded)
    print(json.dumps({"output": str(args.output), "sha256": sha256(args.output), "status": output["status"]}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build one cumulative issue-102 Stage-A round checkpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import statistics
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--stage-a-root", required=True)
    parser.add_argument("--sentinel-root", required=True)
    parser.add_argument("--completed-rounds", type=int, required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--nested-sha", required=True)
    parser.add_argument("--generated-utc", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if not 1 <= args.completed_rounds <= 8:
        parser.error("completed-rounds must be in 1..8")
    return args


def load_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty distribution")
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p10": quantile(values, 0.10),
        "p90": quantile(values, 0.90),
        "min": min(values),
        "max": max(values),
    }


def normalized_sentinel_signature(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": result["case"],
        "point": result["point"],
        "protocol": result["protocol"],
        "fill": {
            "tokens_to_full": result["fill"]["tokens_to_full"],
            "full_prompt_tokens": result["fill"]["full_prompt_tokens"],
            "cold": result["fill"]["cold"],
            "cold_delta": result["fill"]["cold_delta"],
            "storage_delta": result["fill"]["storage_delta"],
        },
        "measured": {
            "decode_forwards": result["measured"]["decode_forwards"],
            "cold_before": result["measured"]["cold_before"],
            "cold_after": result["measured"]["cold_after"],
            "cold_delta": result["measured"]["cold_delta"],
            "storage_delta": result["measured"]["storage_delta"],
        },
        "routing": result["routing"],
        "output": result["output"],
    }


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_clean(result: dict[str, Any], envelope: dict[str, Any]) -> None:
    if result.get("status") != "pass" or result.get("exit_status") != 0:
        raise ValueError("non-passing result")
    if result["resources"].get("vm_swap_kib") != 0:
        raise ValueError("process swap is nonzero")
    if envelope["samples"].get("peak_process_swap_kib") != 0:
        raise ValueError("sampled swap is nonzero")
    if any(envelope.get("memory_pressure_total_delta_usec", {}).values()):
        raise ValueError("memory PSI delta is nonzero")
    for key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"):
        if envelope["delta"]["cgroup_memory_events"].get(key, 0) != 0:
            raise ValueError(f"cgroup memory event {key} is nonzero")
    for key, value in envelope["delta"]["vmstat"].items():
        if key.startswith(("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_")) and value != 0:
            raise ValueError(f"reclaim/refault counter {key} is nonzero")
    for phase in (result["fill"], result["measured"]):
        storage = phase["storage_delta"]
        asynchronous = phase["async_delta"]
        scheduler = phase["scheduler_delta"]
        if any(storage.get(key, 0) for key in ("cancelled_reads", "short_reads", "io_errors")):
            raise ValueError("storage completion failure")
        if any(asynchronous.get(key, 0) for key in (
            "read_requests_cancelled", "buffered_fallback_operations",
            "synchronous_fallback_operations",
        )):
            raise ValueError("asynchronous I/O failure or fallback")
        if any(scheduler.get(key, 0) for key in (
            "terminal_failed", "terminal_cancelled", "stale_completions",
            "active_requests", "queued_requests",
        )):
            raise ValueError("scheduler terminal failure")
    terminal = result["resources"]["terminal_references"]
    if any(value for value in terminal.values() if isinstance(value, int)):
        raise ValueError("terminal reference is nonzero")
    if result["routing"]["stats"].get("failures") != 0:
        raise ValueError("routing failure is nonzero")


def main() -> int:
    args = arguments()
    corpus_path = pathlib.Path(args.corpus).resolve()
    stage_a_root = pathlib.Path(args.stage_a_root).resolve()
    sentinel_root = pathlib.Path(args.sentinel_root).resolve()
    output_path = pathlib.Path(args.output).resolve()
    corpus = load_json(corpus_path)
    case_by_id = {case["id"]: case for case in corpus["cases"]}
    expected_primary_count = args.completed_rounds * 16
    expected_order = [
        case_id for case_id in corpus["execution_order"]
        if case_id != "issue102-sentinel"
    ][:expected_primary_count]
    if len(expected_order) != expected_primary_count:
        raise ValueError("execution order does not contain the expected primary prefix")

    rows: list[dict[str, Any]] = []
    safety_evidence: list[dict[str, Any]] = []
    for ordinal, case_id in enumerate(expected_order, 1):
        root = stage_a_root / f"run-{ordinal:03d}-{case_id}"
        result_path = root / "result.json"
        envelope_path = root / "envelope.json"
        result = load_json(result_path)
        envelope = load_json(envelope_path)
        require_clean(result, envelope)
        case = case_by_id[case_id]
        if result["case"]["id"] != case_id:
            raise ValueError(f"case mismatch at ordinal {ordinal}")
        if result["case"]["templated_prompt_tokens"] != case["observed_templated_prompt_tokens"]:
            raise ValueError(f"templated token mismatch for {case_id}")
        if result["case"]["consumed_prompt_tokens"] != case["observed_templated_prompt_tokens"]:
            raise ValueError(f"consumed token mismatch for {case_id}")
        measured = result["measured"]
        cold = measured["cold_delta"]
        storage = measured["storage_delta"]
        routing = result["routing"]["stats"]
        forwards = measured["decode_forwards"]
        row = {
            "ordinal": ordinal,
            "case_id": case_id,
            "semantic_family": case["semantic_family"],
            "length_level": case["length_level"],
            "templated_prompt_tokens": case["observed_templated_prompt_tokens"],
            "fill_token": result["fill"]["tokens_to_full"],
            "fill_seconds": result["fill"]["time_to_full_s"],
            "fill_residency_digest": result["fill"]["cold"]["residency_digest"],
            "decode_tok_s": measured["decode_tok_s"],
            "p50_forward_s": measured["p50_forward_s"],
            "p95_forward_s": measured["p95_forward_s"],
            "p99_forward_s": measured["p99_forward_s"],
            "hits": cold["hits"],
            "misses": cold["misses"],
            "hit_ratio": cold["hits"] / (cold["hits"] + cold["misses"]),
            "backing_loads": storage["backing_loads"],
            "backing_bytes": storage["backing_bytes"],
            "loads_per_token": storage["backing_loads"] / forwards,
            "bytes_per_token": storage["backing_bytes"] / forwards,
            "routing_decisions": routing["decisions"],
            "changed_decisions": routing["changed_decisions"],
            "changed_fraction": routing["changed_decisions"] / routing["decisions"],
            "realized_swaps": routing["swaps"],
            "swaps_per_token": routing["swaps"] / forwards,
            "cumulative_score_regret": routing["cumulative_score_regret"],
            "mean_score_regret_per_realized_swap": (
                routing["cumulative_score_regret"] / routing["swaps"]
                if routing["swaps"] else 0.0
            ),
            "maximum_realized_regret": routing.get("maximum_realized_regret"),
            "maximum_realized_regret_status": routing.get("maximum_realized_regret_status"),
            "generated_token_count": result["output"]["generated_token_count"],
            "generated_token_hash": result["output"]["generated_token_hash"],
            "result_sha256": sha256(result_path),
            "envelope_sha256": sha256(envelope_path),
        }
        rows.append(row)
        safety_evidence.append({
            "case_id": case_id,
            "minimum_mem_available_kib": envelope["samples"]["minimum_mem_available_kib"],
            "peak_process_rss_kib": envelope["samples"]["peak_process_rss_kib"],
            "peak_process_swap_kib": envelope["samples"]["peak_process_swap_kib"],
            "unused_nvme_read_bytes": envelope["delta"]["nvme"]["nvme1n1"]["read_bytes"],
        })

    sentinel_rows: list[dict[str, Any]] = []
    expected_sentinel_signature = "19c23a98b8c8410929e8080c95f230e62b2476589d7f1dd112afaf14153fa947"
    for round_number in range(1, args.completed_rounds + 1):
        root = sentinel_root / f"round-{round_number:02d}-sentinel"
        result_path = root / "result.json"
        envelope_path = root / "envelope.json"
        result = load_json(result_path)
        envelope = load_json(envelope_path)
        require_clean(result, envelope)
        observed_signature = canonical_sha256(normalized_sentinel_signature(result))
        if observed_signature != expected_sentinel_signature:
            raise ValueError(f"sentinel signature mismatch in round {round_number}")
        sentinel_rows.append({
            "round": round_number,
            "status": "pass",
            "deterministic_signature_sha256": observed_signature,
            "decode_tok_s": result["measured"]["decode_tok_s"],
            "result_sha256": sha256(result_path),
            "envelope_sha256": sha256(envelope_path),
        })

    metric_names = (
        "decode_tok_s", "hit_ratio", "loads_per_token", "bytes_per_token",
        "changed_fraction", "swaps_per_token", "cumulative_score_regret",
        "mean_score_regret_per_realized_swap",
    )
    overall = {name: distribution([float(row[name]) for row in rows]) for name in metric_names}
    families: dict[str, Any] = {}
    for family in dict.fromkeys(case["semantic_family"] for case in corpus["cases"]):
        family_rows = [row for row in rows if row["semantic_family"] == family]
        if not family_rows:
            continue
        families[family] = {
            "values": family_rows,
            "medians": {name: statistics.median(row[name] for row in family_rows) for name in metric_names},
        }
    levels: dict[str, Any] = {}
    for level in sorted({row["length_level"] for row in rows}):
        level_rows = [row for row in rows if row["length_level"] == level]
        levels[str(level)] = {
            "values": level_rows,
            "medians": {name: statistics.median(row[name] for row in level_rows) for name in metric_names},
        }

    value: dict[str, Any] = {
        "schema_version": "issue102-stage-a-checkpoint-v1",
        "status": "pass",
        "generated_utc": args.generated_utc,
        "checkpoint": {
            "completed_rounds": args.completed_rounds,
            "completed_primary_prompts": len(rows),
            "total_primary_prompts": 128,
            "semantic_families_covered": len({row["semantic_family"] for row in rows}),
            "total_semantic_families": 16,
            "length_levels_covered": len({row["length_level"] for row in rows}),
            "total_length_levels": 8,
            "next_execution_order_entry": corpus["execution_order"][args.completed_rounds * 17]
                if args.completed_rounds < 8 else None,
        },
        "identities": {
            "project_at_checkpoint": args.project_sha,
            "nested_llama_cpp": args.nested_sha,
            "corpus_path": str(corpus_path),
            "corpus_sha256": sha256(corpus_path),
            "helper_binary_sha256": "c35cdc52d3669b080972e1c1ac68df6b88290e79d46c92edde2f48eae3733975",
            "model_identity_manifest_sha256": "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2",
            "build_fingerprint_sha256": "d150d179f41ebd2deab49b663e64c909b7d8fa6b4546c716aee889479f633a10",
            "cache_slots": 7849,
            "cache_bytes": 137728475136,
            "n_ctx": 768,
            "point": "S2_P50",
        },
        "sentinels": {
            "status": "pass",
            "expected_deterministic_signature_sha256": expected_sentinel_signature,
            "all_deterministic_signatures_equal": True,
            "runs": sentinel_rows,
            "decode_tok_s": distribution([row["decode_tok_s"] for row in sentinel_rows]),
        },
        "overall_distributions": overall,
        "per_family": families,
        "per_length_level": levels,
        "primary_rows": rows,
        "host_safety": {
            "all_runner_validations_passed": True,
            "all_swap_reclaim_psi_oom_cgroup_pressure_fallback_error_counters_zero": True,
            "all_terminal_resources_zero": True,
            "all_unused_nvme_read_bytes_zero": all(row["unused_nvme_read_bytes"] == 0 for row in safety_evidence),
            "minimum_mem_available_kib": min(row["minimum_mem_available_kib"] for row in safety_evidence),
            "maximum_peak_process_rss_kib": max(row["peak_process_rss_kib"] for row in safety_evidence),
            "per_primary": safety_evidence,
        },
        "progression": {
            "observational_only": True,
            "corpus_policy_capacity_or_order_changed": False,
            "early_stopping_or_winner_selection_authorized": False,
            "next": (
                "continue the immutable execution order after publishing this checkpoint"
                if args.completed_rounds < 8
                else "publish the final Stage A checkpoint before any post-Stage-A work"
            ),
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

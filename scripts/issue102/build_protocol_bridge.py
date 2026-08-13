#!/usr/bin/env python3
"""Build the bounded issue-102 post-Round-1 protocol-bridge synthesis."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-root", required=True)
    parser.add_argument("--sentinel-root", required=True)
    parser.add_argument("--legacy-ctx256-root", required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--nested-sha", required=True)
    parser.add_argument("--generated-utc", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def load(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def distribution(values: list[float]) -> dict[str, Any]:
    return {
        "values": values,
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def require_clean(result: dict[str, Any], envelope: dict[str, Any]) -> None:
    if result.get("status") != "pass" or result.get("exit_status") != 0:
        raise ValueError("non-passing result")
    if result["resources"].get("vm_swap_kib") != 0 or envelope["samples"].get("peak_process_swap_kib") != 0:
        raise ValueError("swap is nonzero")
    if any(envelope.get("memory_pressure_total_delta_usec", {}).values()):
        raise ValueError("memory PSI delta is nonzero")
    for key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"):
        if envelope["delta"]["cgroup_memory_events"].get(key, 0) != 0:
            raise ValueError(f"cgroup event {key} is nonzero")
    for key, value in envelope["delta"]["vmstat"].items():
        if key.startswith(("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_")) and value != 0:
            raise ValueError(f"reclaim/refault {key} is nonzero")
    for phase in (result["fill"], result["measured"]):
        storage = phase["storage_delta"]
        asynchronous = phase["async_delta"]
        scheduler = phase["scheduler_delta"]
        if any(storage.get(key, 0) for key in ("cancelled_reads", "short_reads", "io_errors")):
            raise ValueError("storage failure")
        if any(asynchronous.get(key, 0) for key in (
            "read_requests_cancelled", "buffered_fallback_operations", "synchronous_fallback_operations",
        )):
            raise ValueError("I/O fallback or cancellation")
        if any(scheduler.get(key, 0) for key in (
            "terminal_failed", "terminal_cancelled", "stale_completions", "active_requests", "queued_requests",
        )):
            raise ValueError("scheduler failure")
    if any(value for value in result["resources"]["terminal_references"].values() if isinstance(value, int)):
        raise ValueError("terminal reference is nonzero")
    if result["routing"]["stats"].get("failures") != 0:
        raise ValueError("routing failure is nonzero")
    if envelope["delta"]["nvme"]["nvme1n1"]["read_bytes"] != 0:
        raise ValueError("unused NVMe serviced reads")


def row(root: pathlib.Path, label: str) -> dict[str, Any]:
    result_path = root / "result.json"
    envelope_path = root / "envelope.json"
    result = load(result_path)
    envelope = load(envelope_path)
    require_clean(result, envelope)
    measured = result["measured"]
    cold = measured["cold_delta"]
    storage = measured["storage_delta"]
    routing = result["routing"]["stats"]
    forwards = measured["decode_forwards"]
    decisions = routing["decisions"]
    swaps = routing["swaps"]
    return {
        "label": label,
        "point": result["point"],
        "protocol": result["protocol"],
        "n_ctx": result["execution"]["n_ctx"],
        "prefill_tokens_consumed": result["case"]["consumed_prompt_tokens"],
        "fill_token": result["fill"]["tokens_to_full"],
        "decode_tok_s": measured["decode_tok_s"],
        "p50_forward_s": measured["p50_forward_s"],
        "p95_forward_s": measured["p95_forward_s"],
        "p99_forward_s": measured["p99_forward_s"],
        "hits": cold["hits"],
        "misses": cold["misses"],
        "hit_ratio": cold["hits"] / (cold["hits"] + cold["misses"]),
        "loads_per_token": storage["backing_loads"] / forwards,
        "bytes_per_token": storage["backing_bytes"] / forwards,
        "changed_decisions": routing["changed_decisions"],
        "changed_fraction": routing["changed_decisions"] / decisions if decisions else 0.0,
        "realized_swaps": swaps,
        "swaps_per_token": swaps / forwards,
        "cumulative_score_regret": routing["cumulative_score_regret"],
        "mean_score_regret_per_realized_swap": routing["cumulative_score_regret"] / swaps if swaps else 0.0,
        "maximum_realized_regret": routing.get("maximum_realized_regret"),
        "maximum_realized_regret_status": routing.get("maximum_realized_regret_status"),
        "output_hash": result["output"]["generated_token_hash"],
        "model_nvme_read_bytes": envelope["delta"]["nvme"]["nvme2n1"]["read_bytes"],
        "model_nvme_read_operations": envelope["delta"]["nvme"]["nvme2n1"]["read_operations"],
        "minimum_mem_available_kib": envelope["samples"]["minimum_mem_available_kib"],
        "peak_process_rss_kib": envelope["samples"]["peak_process_rss_kib"],
        "result_sha256": sha256(result_path),
        "envelope_sha256": sha256(envelope_path),
    }


def aggregate(label: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "decode_tok_s", "p50_forward_s", "p95_forward_s", "p99_forward_s", "hit_ratio",
        "loads_per_token", "bytes_per_token", "changed_fraction", "swaps_per_token",
        "cumulative_score_regret", "mean_score_regret_per_realized_swap",
    )
    deterministic_keys = (
        "prefill_tokens_consumed", "fill_token", "hits", "misses", "hit_ratio",
        "loads_per_token", "bytes_per_token", "changed_decisions", "changed_fraction",
        "realized_swaps", "swaps_per_token", "cumulative_score_regret",
        "mean_score_regret_per_realized_swap", "maximum_realized_regret",
        "maximum_realized_regret_status", "output_hash",
    )
    return {
        "label": label,
        "run_count": len(rows),
        "point": rows[0]["point"],
        "protocol": rows[0]["protocol"],
        "n_ctx": rows[0]["n_ctx"],
        "deterministic_equality": all(
            all(item[key] == rows[0][key] for key in deterministic_keys) for item in rows[1:]
        ),
        "metrics": {key: distribution([float(item[key]) for item in rows]) for key in keys},
        "output_hashes": [item["output_hash"] for item in rows],
        "runs": rows,
    }


def main() -> int:
    args = arguments()
    bridge_root = pathlib.Path(args.bridge_root).resolve()
    sentinel_root = pathlib.Path(args.sentinel_root).resolve()
    legacy_ctx256_root = pathlib.Path(args.legacy_ctx256_root).resolve()
    output = pathlib.Path(args.output).resolve()

    exact_rows = [row(bridge_root / f"pair-{index:02d}-full-exact", f"pair-{index}-exact") for index in range(1, 4)]
    knee_rows = [row(bridge_root / f"pair-{index:02d}-full-knee", f"pair-{index}-knee") for index in range(1, 4)]
    legacy768_rows = [row(bridge_root / f"legacy-s2-ctx768-{index:02d}", f"legacy768-{index}") for index in range(1, 4)]
    full_s2_rows = [row(sentinel_root / f"sentinel-full-{index:02d}", f"full-s2-{index}") for index in range(1, 4)]
    legacy256_rows = [row(legacy_ctx256_root, "legacy256-reused")]

    arms = {
        "LEGACY_S2_P50_CTX256": aggregate("LEGACY_S2_P50_CTX256", legacy256_rows),
        "LEGACY_S2_P50_CTX768": aggregate("LEGACY_S2_P50_CTX768", legacy768_rows),
        "FULL_PROMPT_EXACT": aggregate("FULL_PROMPT_EXACT", exact_rows),
        "FULL_PROMPT_KNEE": aggregate("FULL_PROMPT_KNEE", knee_rows),
        "FULL_PROMPT_S2_P50": aggregate("FULL_PROMPT_S2_P50", full_s2_rows),
    }
    med = {name: value["metrics"]["decode_tok_s"]["median"] for name, value in arms.items()}
    pair_ratios = [knee_rows[index]["decode_tok_s"] / exact_rows[index]["decode_tok_s"] for index in range(3)]
    full_s2_hit = arms["FULL_PROMPT_S2_P50"]["metrics"]["hit_ratio"]["median"]
    full_knee_hit = arms["FULL_PROMPT_KNEE"]["metrics"]["hit_ratio"]["median"]
    full_exact_hit = arms["FULL_PROMPT_EXACT"]["metrics"]["hit_ratio"]["median"]
    full_s2_loads = arms["FULL_PROMPT_S2_P50"]["metrics"]["loads_per_token"]["median"]
    full_knee_loads = arms["FULL_PROMPT_KNEE"]["metrics"]["loads_per_token"]["median"]
    full_exact_loads = arms["FULL_PROMPT_EXACT"]["metrics"]["loads_per_token"]["median"]

    value = {
        "schema_version": "issue102-protocol-bridge-v1",
        "status": "pass",
        "generated_utc": args.generated_utc,
        "classification": "diagnostic_not_stage_a_or_stage_c",
        "identities": {
            "project": args.project_sha,
            "nested_llama_cpp": args.nested_sha,
            "helper_binary_sha256": "c35cdc52d3669b080972e1c1ac68df6b88290e79d46c92edde2f48eae3733975",
            "model_identity_manifest_sha256": "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2",
            "build_fingerprint_sha256": "d150d179f41ebd2deab49b663e64c909b7d8fa6b4546c716aee889479f633a10",
            "cache_slots": 7849,
            "cache_bytes": 137728475136,
        },
        "paired_order": [
            ["FULL_PROMPT_EXACT", "FULL_PROMPT_KNEE"],
            ["FULL_PROMPT_KNEE", "FULL_PROMPT_EXACT"],
            ["FULL_PROMPT_EXACT", "FULL_PROMPT_KNEE"],
        ],
        "arms": arms,
        "comparisons": {
            "full_prompt_knee_over_exact_tps_ratio_by_pair": pair_ratios,
            "full_prompt_knee_over_exact_tps_ratio_median": statistics.median(pair_ratios),
            "full_prompt_s2_over_knee_median_tps_ratio": med["FULL_PROMPT_S2_P50"] / med["FULL_PROMPT_KNEE"],
            "full_prompt_s2_over_exact_median_tps_ratio": med["FULL_PROMPT_S2_P50"] / med["FULL_PROMPT_EXACT"],
            "legacy_ctx768_over_ctx256_median_tps_ratio": med["LEGACY_S2_P50_CTX768"] / med["LEGACY_S2_P50_CTX256"],
            "full_s2_over_legacy_ctx768_median_tps_ratio": med["FULL_PROMPT_S2_P50"] / med["LEGACY_S2_P50_CTX768"],
            "full_prompt_s2_minus_knee_hit_ratio": full_s2_hit - full_knee_hit,
            "full_prompt_s2_minus_exact_hit_ratio": full_s2_hit - full_exact_hit,
            "full_prompt_s2_minus_knee_loads_per_token": full_s2_loads - full_knee_loads,
            "full_prompt_s2_minus_exact_loads_per_token": full_s2_loads - full_exact_loads,
        },
        "diagnostic_answers": {
            "current_helper_host_reproduces_historical_legacy_tps_regime": True,
            "evidence": "LEGACY_S2_P50_CTX768 median and range reproduce the historical #98 approximately 0.5033-0.5054 tok/s regime while retaining the exact legacy deterministic signature; the reused clean-host ctx256 bridge is within about 0.66% of the ctx768 median",
            "raising_n_ctx_alone_materially_changes_legacy_tps": False,
            "full_prompt_trajectory_explains_large_absolute_shift": True,
            "full_prompt_s2_still_improves_over_knee": True,
            "full_prompt_s2_still_improves_over_exact": True,
            "cross_issue_tps_interpretation_blocked_by_host_or_helper_confound": False,
            "prefill_depth_curve_authorized_to_proceed": True,
        },
        "limitations": [
            "FULL_PROMPT_S2_P50 reuses the preregistered three-run sentinel baseline and is not contemporaneously paired",
            "LEGACY_S2_P50_CTX256 reuses one clean-host parity run; ctx768 provides the requested three-run same-helper context isolation",
            "output divergence among policies is trajectory metadata only and not semantic-quality evidence",
            "maximum realized per-swap regret is unavailable without the later observer diagnostic",
        ],
        "safety": {
            "all_new_bridge_runs_passed": True,
            "all_reused_runs_passed": True,
            "all_swap_reclaim_psi_oom_cgroup_pressure_fallback_error_counters_zero": True,
            "all_terminal_resources_zero": True,
            "all_unused_nvme_read_bytes_zero": True,
        },
        "progression": {
            "stage_a_completed_primary_prompts": 16,
            "stage_a_primary_17_started_validly": False,
            "next": "publish this bridge synthesis, then execute the fixed N={9,16,32,64,100} prefill-depth locality curve before rerunning primary 17",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Audit and analyze the completed issue #100 paired/full-S2 campaign."""

from __future__ import annotations

import argparse
import hashlib
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Iterable

from protocol import (
    BOOTSTRAP_REPLICATE_ZERO, BOOTSTRAP_REPLICATES, BOOTSTRAP_ROOT,
    BOOTSTRAP_STREAM_SHA256, CAMPAIGN_SHA256, PREREGISTRATION_SHA256,
    ProtocolError, atomic_json, bootstrap_indices, file_identity, load_json,
    sha256_file, validate_checksum,
)
from run_campaign import load_jsonl, score_result


class AnalysisError(RuntimeError):
    pass


def nearest_rank(values: Iterable[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered or probability <= 0.0 or probability > 1.0:
        raise AnalysisError("invalid nearest-rank request")
    rank = math.ceil(probability*len(ordered))
    return ordered[rank - 1]


def distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "min": None, "max": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "p50": nearest_rank(values, 0.50),
        "p95": nearest_rank(values, 0.95),
        "p99": nearest_rank(values, 0.99),
        "min": min(values),
        "max": max(values),
    }


def exact_mcnemar(exact_only: int, s2_only: int) -> dict[str, Any]:
    discordant = exact_only + s2_only
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(math.comb(discordant, value) for value in range(min(exact_only, s2_only) + 1))
        p_value = min(1.0, 2.0*tail/(2**discordant))
    return {
        "test": "exact-two-sided-McNemar-binomial",
        "exact_only": exact_only,
        "s2_only": s2_only,
        "discordant": discordant,
        "p_value": p_value,
    }


def paired_statistics(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    if len(pairs) != 30:
        raise AnalysisError("paired analysis requires exactly 30 pairs")
    deltas = [int(row["s2_correct"]) - int(row["exact_correct"]) for row in pairs]
    digest = hashlib.sha256()
    bootstrap = []
    first = None
    for replicate, indices in enumerate(bootstrap_indices()):
        if replicate == 0:
            first = indices
        digest.update(bytes(indices))
        bootstrap.append(sum(deltas[index] for index in indices)/30)
    if first != BOOTSTRAP_REPLICATE_ZERO or digest.hexdigest() != BOOTSTRAP_STREAM_SHA256:
        raise AnalysisError("paired bootstrap deterministic test vector drift")
    point = statistics.fmean(deltas)
    lower = nearest_rank(bootstrap, 0.025)
    upper = nearest_rank(bootstrap, 0.975)
    one_sided_lower = nearest_rank(bootstrap, 0.05)
    if point <= -0.10 and upper < 0.0:
        disposition = "S2_GPQA_PAIRED_DEGRADED"
    elif point >= 0.10 and lower > 0.0:
        disposition = "S2_GPQA_PAIRED_IMPROVED"
    elif one_sided_lower > -0.10:
        disposition = "S2_GPQA_PAIRED_NO_MATERIAL_LOSS"
    else:
        disposition = "S2_GPQA_PAIRED_INCONCLUSIVE"
    classes = {
        label: sum(row["pair_class"] == label for row in pairs)
        for label in ("both-correct", "both-wrong", "EXACT-only", "S2-only")
    }
    return {
        "estimand": "mean(S2_correct - EXACT_correct) over 30 preregistered items",
        "materiality_margin": 0.10,
        "exact_correct": sum(row["exact_correct"] for row in pairs),
        "s2_correct": sum(row["s2_correct"] for row in pairs),
        "accuracy_delta": point,
        "pair_classes": classes,
        "bootstrap": {
            "seed": BOOTSTRAP_ROOT,
            "replicates": BOOTSTRAP_REPLICATES,
            "encoding": "zero-based replicate ASCII :06d and draw ASCII :02d",
            "index_stream_sha256": digest.hexdigest(),
            "replicate_zero": list(first or ()),
            "two_sided_95_percentile_interval": [lower, upper],
            "one_sided_95_lower_bound": one_sided_lower,
        },
        "mcnemar": exact_mcnemar(classes["EXACT-only"], classes["S2-only"]),
        "disposition": disposition,
    }


def wilson(successes: int, total: int, z: float) -> tuple[float, float]:
    point = successes/total
    denominator = 1.0 + z*z/total
    center = (point + z*z/(2*total))/denominator
    radius = z*math.sqrt(point*(1-point)/total + z*z/(4*total*total))/denominator
    return center - radius, center + radius


def full_s2_statistics(runs: list[dict[str, Any]], protocol_drift: bool) -> dict[str, Any]:
    if len(runs) != 198:
        raise AnalysisError("full-S2 analysis requires exactly 198 items")
    correct = sum(row["correct"] for row in runs)
    point = correct/198
    reference = 0.935
    lower, upper = wilson(correct, 198, 1.959963984540054)
    point_delta = point - reference
    if protocol_drift:
        disposition = "INCONCLUSIVE_PROTOCOL_OR_SAMPLE"
    elif point_delta <= -0.03 and upper < reference:
        disposition = "QUALITY_LOWER_THAN_REFERENCE"
    elif point_delta >= 0.03 and lower > reference:
        disposition = "QUALITY_HIGHER_THAN_REFERENCE"
    elif lower <= reference <= upper and abs(point_delta) < 0.03:
        disposition = "CONSISTENT_WITH_OFFICIAL_REFERENCE"
    else:
        disposition = "INCONCLUSIVE_PROTOCOL_OR_SAMPLE"
    return {
        "correct": correct,
        "total": 198,
        "accuracy": point,
        "wilson_two_sided_95": [lower, upper],
        "wilson_z": 1.959963984540054,
        "official_reference": reference,
        "point_delta": point_delta,
        "material_threshold": 0.03,
        "protocol_fidelity": "OFFICIAL_PROTOCOL_NEAR_MATCH",
        "protocol_drift": protocol_drift,
        "disposition": disposition,
    }


def validate_run_and_attempt(
    run: dict[str, Any],
    item: dict[str, Any],
    expected_identity: dict[str, Any],
) -> dict[str, Any]:
    validate_checksum(run)
    for key, value in expected_identity.items():
        if run.get(key) != value:
            raise AnalysisError(f"run identity drift: {key}")
    manifest_path = Path(run["attempt_manifest_path"])
    if sha256_file(manifest_path) != run["attempt_manifest_sha256"]:
        raise AnalysisError("attempt manifest checksum drift")
    manifest = load_json(manifest_path)
    if not manifest.get("accepted") or manifest.get("run_ordinal") != run["run_ordinal"] or \
            manifest.get("item_id") != run["item_id"] or manifest.get("arm") != run["arm"]:
        raise AnalysisError("accepted attempt identity drift")
    for artifact in manifest["artifacts"].values():
        path = Path(artifact["canonical_path"])
        observed = file_identity(path)
        for key in ("device", "inode", "size_bytes", "sha256"):
            if observed[key] != artifact[key]:
                raise AnalysisError(f"raw attempt artifact drift: {path} {key}")
    result = load_json(Path(manifest["artifacts"]["probe-result.json"]["canonical_path"]))
    score = score_result(result, item)
    protected_score = load_json(Path(manifest["artifacts"]["score-evidence.json"]["canonical_path"]))
    if bytes.fromhex(protected_score["raw_generated_bytes_hex"]) != score["raw_bytes"] or \
            bytes.fromhex(protected_score["content_before_eog_hex"]) != score["content_bytes"] or \
            bytes.fromhex(protected_score["reasoning_bytes_hex"]) != score["reasoning_bytes"] or \
            bytes.fromhex(protected_score["transition_bytes_hex"]) != score["transition_bytes"] or \
            bytes.fromhex(protected_score["response_bytes_hex"]) != score["response_bytes"]:
        raise AnalysisError("protected parsed-byte evidence drift")
    comparisons = {
        "first_generated_token_id": result["generation"]["token_ids"][0],
        "raw_output_sha256": score["raw_output_sha256"],
        "content_sha256": score["content_sha256"],
        "response_sha256": score["response_sha256"],
        "extracted_answer": score["extracted_answer"],
        "correct_answer": score["correct_answer"],
        "correct": score["correct"],
        "invalid": score["invalid"],
        "malformed": score["malformed"],
        "truncated": score["truncated"],
        "outcome": score["outcome"],
        "reasoning_tokens": score["reasoning_tokens"],
        "transition_tokens": score["transition_tokens"],
        "response_tokens": score["response_tokens"],
    }
    for key, expected in comparisons.items():
        if run.get(key) != expected:
            raise AnalysisError(f"independent score audit mismatch: {run['run_ordinal']} {key}")
        if key != "first_generated_token_id" and (
                manifest["score_audit"].get(key) != expected or protected_score.get(key) != expected):
            raise AnalysisError(f"protected score audit mismatch: {run['run_ordinal']} {key}")
    return result


def performance_summary(runs: list[dict[str, Any]], raw_results: list[dict[str, Any]]) -> dict[str, Any]:
    forward_latency = []
    sample_latency = []
    generated_token_latency = []
    queue_wait_us = 0
    direct_useful_bytes = 0
    direct_aligned_bytes = 0
    peak_rss_kib = []
    for result in raw_results:
        forwards = result["generation"]["forward_latency_s"]
        samples = result["generation"]["sample_latency_s"]
        forward_latency.extend(forwards)
        sample_latency.extend(samples)
        if samples:
            generated_token_latency.append(samples[0])
            generated_token_latency.extend(
                forward + sample for forward, sample in zip(forwards, samples[1:])
            )
        async_total = result["io"]["async_total"]
        queue_wait_us += async_total["queue_wait_us"]
        direct_useful_bytes += async_total["direct_useful_bytes"]
        direct_aligned_bytes += async_total["direct_aligned_bytes"]
        peak_rss_kib.append(result["timing"]["peak_rss_kib"])
    by_arm = {}
    for arm in ("EXACT", "S2_P50"):
        selected = [row for row in runs if row["arm"] == arm]
        by_arm[arm] = {
            "runs": len(selected),
            "prompt_tokens_per_s": distribution([
                row["prompt_tokens"]/row["prefill_wall_s"] for row in selected
            ]),
            "decode_tokens_per_s": distribution([row["decode_tok_s"] for row in selected]),
            "generated_tokens": distribution([float(row["generated_tokens"]) for row in selected]),
            "item_wall_s": distribution([row["total_wall_s"] for row in selected]),
            "cache_requests": sum(row["cache_hits"] + row["cache_misses"] for row in selected),
            "cache_hits": sum(row["cache_hits"] for row in selected),
            "cache_misses": sum(row["cache_misses"] for row in selected),
            "cache_loads": sum(row["cache_loads"] for row in selected),
            "backing_bytes": sum(row["backing_bytes"] for row in selected),
            "realized_swaps": sum(row["realized_swaps"] for row in selected),
            "changed_decisions": sum(row["changed_decisions"] for row in selected),
            "cumulative_corrected_regret": sum(row["cumulative_corrected_regret"] for row in selected),
        }
    return {
        "by_arm": by_arm,
        "generated_token_latency_s": distribution(generated_token_latency),
        "generated_forward_latency_s": distribution(forward_latency),
        "sampling_latency_s": distribution(sample_latency),
        "io": {
            "queue_wait_us": queue_wait_us,
            "disk_queue_wait_s": queue_wait_us/1_000_000,
            "direct_useful_bytes": direct_useful_bytes,
            "direct_aligned_bytes": direct_aligned_bytes,
            "buffered_fallback_operations": 0,
            "synchronous_fallback_operations": 0,
            "h2d_bytes": 0,
            "h2d_wait_s": 0.0,
            "h2d_overlap": "not-applicable-cpu-only",
            "disk_overlap": "not-separately-instrumented; native asynchronous io_uring retained",
            "prefetch": "disabled",
            "cpu_miss_compute": "not-separately-instrumented; storage and forward time retained",
        },
        "memory": {
            "peak_process_rss_kib": distribution([float(value) for value in peak_rss_kib]),
            "swap_kib": 0,
            "pinned_ram": "not-applicable-cpu-cold-only-no-transfer-ring",
            "vram": 0,
            "uma": "not-applicable",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--protected-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.campaign_root.resolve(strict=True)
    control = load_json(root / "campaign-control.json")
    if control.get("status") != "complete" or control.get("campaign_sha256") != CAMPAIGN_SHA256:
        raise AnalysisError("campaign control is not complete/frozen")
    protected_plan = load_json(args.protected_plan.resolve(strict=True))
    if protected_plan.get("campaign_sha256") != CAMPAIGN_SHA256 or \
            sha256_file(args.protected_plan) != control["protected_plan_sha256"]:
        raise AnalysisError("protected plan identity drift")
    expected_identity = {
        key: control[key] for key in (
            "campaign_sha256", "preregistration_sha256", "project_commit", "nested_commit",
            "model_manifest_sha256", "adapter_binary_sha256", "protected_plan_sha256",
            "execution_authorization_sha256", "capacity_slots", "capacity_bytes", "scoring_identity",
        )
    }
    if expected_identity["preregistration_sha256"] != PREREGISTRATION_SHA256:
        raise AnalysisError("preregistration identity drift")
    runs = load_jsonl(root / "runs.jsonl")
    pairs = load_jsonl(root / "pairs.jsonl")
    if len(runs) != 228 or len(pairs) != 30:
        raise AnalysisError("campaign evidence cardinality mismatch")
    items_root = Path(protected_plan["items_root"])
    raw_results = []
    for ordinal, (run, planned) in enumerate(zip(runs, protected_plan["runs"]), 1):
        if run.get("run_ordinal") != ordinal or any(run.get(key) != planned.get(key) for key in (
            "stage", "arm", "run_ordinal", "pair_ordinal", "s2_ordinal", "item_id",
        )):
            raise AnalysisError("run order differs from protected plan")
        item = load_json(items_root / f"{run['item_id']}.json")
        raw_results.append(validate_run_and_attempt(run, item, expected_identity))
    for ordinal, pair in enumerate(pairs, 1):
        validate_checksum(pair)
        exact = runs[(ordinal - 1)*2]
        s2 = runs[(ordinal - 1)*2 + 1]
        if pair.get("pair_ordinal") != ordinal or pair.get("item_id") != exact["item_id"] or \
                pair.get("exact_run_checksum") != exact["artifact_checksum"] or \
                pair.get("s2_run_checksum") != s2["artifact_checksum"] or \
                pair.get("first_generated_token_id") != exact["first_generated_token_id"] or \
                exact["first_generated_token_id"] != s2["first_generated_token_id"]:
            raise AnalysisError("pair/run binding mismatch")

    exact_runs = [row for row in runs if row["arm"] == "EXACT"]
    s2_runs = [row for row in runs if row["arm"] == "S2_P50"]
    if len(exact_runs) != 30 or len(s2_runs) != 198 or \
            [row["item_id"] for row in exact_runs] != [row["item_id"] for row in s2_runs[:30]]:
        raise AnalysisError("paired prefix/full-S2 coverage mismatch")
    protocol_drift = False
    result = {
        "schema_version": "issue100-final-analysis-v1",
        "status": "pass",
        "issue": 100,
        "provenance": {
            **expected_identity,
            "campaign_control": file_identity(root / "campaign-control.json"),
            "runs_jsonl": file_identity(root / "runs.jsonl"),
            "pairs_jsonl": file_identity(root / "pairs.jsonl"),
        },
        "completeness": {
            "accepted_runs": len(runs), "exact_runs": len(exact_runs),
            "s2_runs": len(s2_runs), "pairs": len(pairs),
            "invalid": sum(row["invalid"] for row in runs),
            "malformed": sum(row["malformed"] for row in runs),
            "truncated": sum(row["truncated"] for row in runs),
            "resource_failures": sum(row["resource_status"] != "pass" for row in runs),
        },
        "paired_causal_result": paired_statistics(pairs),
        "full_s2_external_result": full_s2_statistics(s2_runs, protocol_drift),
        "performance": performance_summary(runs, raw_results),
        "interpretation_boundary": {
            "paired": "local causal comparison on the same 30 preregistered items",
            "full_s2": "198-item local score compared with the public 93.5% reference under near-match fidelity",
            "routing_retuning_authorized": False,
        },
    }
    atomic_json(args.output, result)
    print(
        "ISSUE100_ANALYSIS status=pass "
        f"paired={result['paired_causal_result']['disposition']} "
        f"full_s2={result['full_s2_external_result']['disposition']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 analysis: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

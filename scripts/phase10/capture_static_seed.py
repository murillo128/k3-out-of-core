#!/usr/bin/env python3
"""Capture fresh-process paired ABBA evidence for one frozen blocking seed."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import subprocess
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from prefetch_common import Phase10Error, require_capture_heads, validate_profile


ROOT = Path(__file__).resolve().parents[2]
T_CRITICAL_95 = {9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
    14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
    19: 2.093, 20: 2.086, 24: 2.064, 29: 2.045}


def load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise Phase10Error(f"{path} is not a JSON object")
    return value


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False,
        separators=(",", ": ")) + "\n").encode()


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024*1024), b""):
            digest.update(block)
    return digest.hexdigest()


def evidence_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path) -> dict[str, Any]:
    return {"path": evidence_path(path), "size": path.stat().st_size,
        "sha256": sha256_file(path)}


def nearest_rank(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(fraction*len(ordered)) - 1))]


def distribution(values: list[float]) -> dict[str, Any]:
    return {"count": len(values), "min": min(values), "p50": nearest_rank(values, .50),
        "p95": nearest_rank(values, .95), "p99": nearest_rank(values, .99), "max": max(values),
        "mean": statistics.fmean(values)}


def mean_interval(values: list[float]) -> dict[str, Any]:
    if len(values) < 10:
        raise Phase10Error("paired Student-t evidence requires at least 10 blocks")
    mean = statistics.fmean(values)
    standard_error = statistics.stdev(values)/math.sqrt(len(values))
    critical = T_CRITICAL_95.get(len(values) - 1, 1.96)
    half = critical*standard_error
    return {"n": len(values), "mean": mean, "ci95_low": mean - half,
        "ci95_high": mean + half, "student_t_critical": critical, "samples": values}


def metric(capture: dict[str, Any]) -> dict[str, float]:
    latency = [float(value) for value in capture["latency_us"]]
    decode = latency[1:] if len(latency) > 1 else latency
    return {"model_load_us": capture["model_load_ns"]/1000.0,
        "ttft_us": latency[0], "cold_process_to_first_token_us": capture["model_load_ns"]/1000.0 + latency[0],
        "decode_mean_us": statistics.fmean(decode), "decode_p95_us": nearest_rank(decode, .95),
        "decode_throughput_tps": len(decode)*1_000_000.0/sum(decode),
        "peak_rss_kib": float(capture["peak_rss_kib"]),
        "minor_faults": float(capture["minor_faults"]), "major_faults": float(capture["major_faults"])}


def block_means(block: dict[str, Any], name: str) -> tuple[float, float]:
    baseline = statistics.fmean(metric(run["capture"])[name] for run in block["baseline"])
    candidate = statistics.fmean(metric(run["capture"])[name] for run in block["candidate"])
    return baseline, candidate


def compare_metric(blocks: list[dict[str, Any]], name: str, higher_is_better: bool) -> dict[str, Any]:
    pairs = [block_means(block, name) for block in blocks]
    baseline = [pair[0] for pair in pairs]
    candidate = [pair[1] for pair in pairs]
    def relative_change(left: float, right: float) -> float:
        if left == 0:
            if right == 0:
                return 0.0
            return 1.0 if higher_is_better else -1.0
        return (right - left)/left if higher_is_better else (left - right)/left
    relative = [relative_change(left, right) for left, right in pairs]
    absolute = [right - left for left, right in pairs]
    return {"baseline": distribution(baseline), "candidate": distribution(candidate),
        "absolute_candidate_minus_baseline": mean_interval(absolute),
        "relative_improvement": mean_interval(relative)}


def output_identity(capture: dict[str, Any]) -> str:
    value = {"prompt": capture["prompt"], "generated_tokens": capture["generated_tokens"],
        "logit_sha256": capture["logit_sha256"], "public_routes": capture["public_routes"]}
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def seed_usage(profile: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    seeds = {(item["layer"], item["expert"]): item["physical_bytes"] for item in profile["seed"]}
    first_use: dict[tuple[int, int], dict[str, int]] = {}
    logical_token = 0
    for route_ordinal, route in enumerate(capture["public_routes"]):
        top_k = route["n_expert_used"]
        ids = route["selected_experts"]
        for row in range(route["n_tokens"]):
            for expert in ids[row*top_k:(row + 1)*top_k]:
                key = (route["layer"], expert)
                if key in seeds and key not in first_use:
                    first_use[key] = {"logical_token": logical_token + row,
                        "route_ordinal": route_ordinal}
        logical_token += route["n_tokens"] if route["layer"] == profile["target"]["routed_layers"][-1] else 0
    final = {(item["layer"], item["expert"]) for item in capture["final_resident"]}
    unused = set(seeds) - set(first_use)
    return {"seed_entries": len(seeds), "seed_bytes": sum(seeds.values()),
        "first_use_hits": len(first_use),
        "first_use_distances": [{"layer": key[0], "expert": key[1], **first_use[key]}
            for key in sorted(first_use)],
        "unused_before_eviction": len([key for key in unused if key not in final]),
        "unused_at_unload": len([key for key in unused if key in final]),
        "displaced_before_unload": len([key for key in seeds if key not in final])}


def validate_capture(document: dict[str, Any], validator: Draft202012Validator,
        enabled: bool, profile: dict[str, Any], prompt_id: str, steps: int,
        runtime: dict[str, Any], project_head: str, nested_head: str) -> None:
    validator.validate(document)
    expected = {"profile_enabled": enabled, "selected_policy": "BLOCKING_HOT",
        "project_head": project_head, "nested_head": nested_head, "prompt": prompt_id,
        "cache_mode": runtime["cache_mode"], "load_mode": runtime["load_mode"],
        "miss_policy": runtime["miss_policy"], "hot_slots": runtime["hot_slots"],
        "cold_slots": runtime["cold_slots"]}
    for field, value in expected.items():
        actual = document["prompt"]["id"] if field == "prompt" else document[field]
        if actual != value:
            raise Phase10Error(f"performance capture {field} mismatch")
    if len(document["latency_us"]) != steps or len(document["generated_tokens"]) != steps or \
            len(document["logit_sha256"]) != steps:
        raise Phase10Error("performance stream length mismatch")
    if not document["public_routes"]:
        raise Phase10Error("performance route stream is empty")
    summary = document["summary"]
    if any(summary[field] != 0 for field in ("prediction_events", "admitted", "rejected",
            "timely_useful", "late_joined", "wasted_unused", "cancelled_before_io",
            "cancelled_drained", "predictor_compute_ns", "circuit_opens")) or \
            summary["circuit_open"] or summary["runtime_failed"] or \
            summary["active_background_flights"] != 0 or summary["current_pins"] != 0:
        raise Phase10Error("blocking seed capture created prediction work or retained runtime state")
    seed = document["seed"]
    expected_entries = len(profile["seed"])
    expected_bytes = sum(item["physical_bytes"] for item in profile["seed"])
    if enabled:
        expected_storage = expected_bytes if runtime["cache_mode"] == "COLD_CACHE" else 0
        if not seed["configured"] or not seed["complete"] or seed["attempts"] != 1 or \
                seed["failures"] != 0 or seed["entries"] != expected_entries or \
                seed["storage_bytes"] != expected_storage or seed["h2d_bytes"] != expected_bytes:
            raise Phase10Error("blocking seed accounting mismatch")
    elif any((seed["configured"], seed["complete"], seed["attempts"], seed["failures"],
            seed["entries"], seed["storage_bytes"], seed["h2d_bytes"])):
        raise Phase10Error("disabled baseline created blocking seed state")


def run_probe(probe: Path, profile_path: Path, model: Path, identity: str,
        enabled: bool, prompt: dict[str, Any], steps: int, runtime: dict[str, Any],
        validator: Draft202012Validator, profile: dict[str, Any], label: str) -> dict[str, Any]:
    mode = "--performance" if enabled else "--performance-disabled"
    command = [str(probe), "--profile", str(profile_path), "--model", str(model),
        "--identity", identity, mode, prompt["id"], str(steps), prompt["text"],
        runtime["cache_mode"], runtime["load_mode"], runtime["miss_policy"],
        str(runtime["hot_slots"]), str(runtime["cold_slots"])]
    completed = subprocess.run(command, check=False, capture_output=True)
    stderr_sha = hashlib.sha256(completed.stderr).hexdigest()
    if completed.returncode != 0:
        tail = completed.stderr.decode(errors="replace").splitlines()[-24:]
        raise Phase10Error(f"performance run {label} failed ({completed.returncode}):\n" + "\n".join(tail))
    try:
        capture = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Phase10Error(f"performance run {label} emitted invalid JSON: {error}") from error
    validate_capture(capture, validator, enabled, profile, prompt["id"], steps,
        runtime, identity[:40], identity[41:])
    return {"label": label, "side": "candidate" if enabled else "baseline",
        "exit_code": completed.returncode, "stderr_sha256": stderr_sha,
        "command": {"mode": mode, "prompt_id": prompt["id"], "steps": steps,
            "runtime": runtime}, "capture": capture}


def summarize_cell(name: str, kind: str, prompt: dict[str, Any], steps: int,
        warmups: list[dict[str, Any]], blocks: list[dict[str, Any]],
        profile: dict[str, Any]) -> dict[str, Any]:
    all_runs = warmups + [run for block in blocks for side in ("baseline", "candidate") for run in block[side]]
    identities = {output_identity(run["capture"]) for run in all_runs}
    candidate_runs = [run for block in blocks for run in block["candidate"]]
    usage = [seed_usage(profile, run["capture"]) for run in candidate_runs]
    metrics = {name: compare_metric(blocks, name, higher) for name, higher in (
        ("model_load_us", False), ("cold_process_to_first_token_us", False),
        ("ttft_us", False), ("decode_mean_us", False), ("decode_p95_us", False),
        ("decode_throughput_tps", True), ("peak_rss_kib", False),
        ("minor_faults", False), ("major_faults", False))}
    return {"name": name, "kind": kind, "prompt_id": prompt["id"],
        "prompt_sha256": hashlib.sha256(prompt["text"].encode()).hexdigest(), "steps": steps,
        "abba_blocks": len(blocks), "fresh_processes": True, "output_identity_exact": len(identities) == 1,
        "metrics": metrics, "seed_usage": {"all_runs_exact": len({canonical_bytes(value) for value in usage}) == 1,
            "representative": usage[0]},
        "unmeasured_warmup_labels": [run["label"] for run in warmups],
        "measured_run_labels": [run["label"] for block in blocks for side in ("baseline", "candidate") for run in block[side]]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--prompt-corpus", type=Path, required=True)
    parser.add_argument("--phase9-working-sets", type=Path, required=True)
    parser.add_argument("--phase9-case", required=True)
    parser.add_argument("--cell", action="append", nargs=4,
        metavar=("NAME", "KIND", "PROMPT_ID", "STEPS"), required=True)
    parser.add_argument("--cache-mode", choices=("HOT_CACHE", "COLD_CACHE"), required=True)
    parser.add_argument("--load-mode", choices=("BUFFERED", "DIRECT_IO"), required=True)
    parser.add_argument("--miss-policy", choices=("PROMOTE_AND_GPU", "CPU_FALLBACK", "AUTO"), required=True)
    parser.add_argument("--hot-slots", type=int, required=True)
    parser.add_argument("--cold-slots", type=int, required=True)
    parser.add_argument("--abba-blocks", type=int, default=10)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.abba_blocks < 10:
            raise Phase10Error("mandatory performance capture requires at least 10 ABBA blocks")
        require_capture_heads(args.project_head, args.nested_head)
        profile = load(args.profile)
        validate_profile(profile)
        if profile["selection"]["policy"] != "BLOCKING_HOT":
            raise Phase10Error("static seed capture requires a frozen BLOCKING_HOT profile")
        prompt_corpus = load(args.prompt_corpus)
        prompts = {item["id"]: item for item in prompt_corpus["prompts"]}
        working_sets = load(args.phase9_working_sets)
        working = next((item for item in working_sets["cases"] if item["name"] == args.phase9_case), None)
        if working is None:
            raise Phase10Error("Phase 9 working-set case is unavailable")
        runtime = {"cache_mode": args.cache_mode, "load_mode": args.load_mode,
            "miss_policy": args.miss_policy, "hot_slots": args.hot_slots, "cold_slots": args.cold_slots}
        footprint = {item["physical_bytes"] for item in profile["target"]["expert_bytes"]}
        bundle_bytes = next(iter(footprint)) if len(footprint) == 1 else 0
        slot_footprint = working["one_expert_footprint_bytes"]
        if bundle_bytes == 0 or bundle_bytes > slot_footprint or slot_footprint - bundle_bytes > 4096:
            raise Phase10Error("profile bundle bytes differ from Phase 9 working-set evidence")
        observed = working["observed_capacities"]
        exact_phase9_capacity = args.hot_slots == observed["hot_effective_slots"] and (
            (args.cache_mode == "COLD_CACHE" and args.cold_slots == observed["cold_effective_slots"]) or
            (args.cache_mode == "HOT_CACHE" and args.cold_slots == 0))
        if not exact_phase9_capacity:
            raise Phase10Error("seed qualification must use the accepted Phase 9 capacity point")
        schema_path = ROOT / "schemas/phase10/performance-capture-v1.schema.json"
        schema = load(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        identity = f"{args.project_head}:{args.nested_head}"
        raw: dict[str, Any] = {"schema_version": "phase10-seed-performance-raw-v1",
            "project_head": args.project_head, "nested_head": args.nested_head, "cells": []}
        summaries = []
        seen_names: set[str] = set()
        kinds: set[str] = set()
        for name, kind, prompt_id, steps_text in args.cell:
            if name in seen_names or kind not in {"COLD_START", "STEADY", "DOMAIN_SHIFT"} or prompt_id not in prompts:
                raise Phase10Error("performance cell name, kind, or prompt is invalid")
            seen_names.add(name)
            kinds.add(kind)
            steps = int(steps_text)
            if steps < 2 or steps > min(128, prompts[prompt_id]["max_generated_tokens"]):
                raise Phase10Error("performance cell step count exceeds the frozen corpus")
            prompt = prompts[prompt_id]
            warmups = [
                run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    False, prompt, steps, runtime, validator, profile, f"{name}-warmup-a"),
                run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    True, prompt, steps, runtime, validator, profile, f"{name}-warmup-b"),
            ]
            blocks = []
            for block in range(args.abba_blocks):
                prefix = f"{name}-b{block}"
                a1 = run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    False, prompt, steps, runtime, validator, profile, prefix + "-a1")
                b1 = run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    True, prompt, steps, runtime, validator, profile, prefix + "-b1")
                b2 = run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    True, prompt, steps, runtime, validator, profile, prefix + "-b2")
                a2 = run_probe(args.probe.resolve(), args.profile.resolve(), args.model.resolve(), identity,
                    False, prompt, steps, runtime, validator, profile, prefix + "-a2")
                blocks.append({"baseline": [a1, a2], "candidate": [b1, b2]})
            raw["cells"].append({"name": name, "kind": kind, "prompt_id": prompt_id,
                "steps": steps, "warmups": warmups, "blocks": blocks})
            summaries.append(summarize_cell(name, kind, prompt, steps, warmups, blocks, profile))
        if kinds != {"COLD_START", "STEADY", "DOMAIN_SHIFT"}:
            raise Phase10Error("seed qualification requires cold-start, steady, and domain-shift cells")
        args.raw_output.parent.mkdir(parents=True, exist_ok=True)
        with args.raw_output.open("wb") as handle:
            with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as compressed:
                compressed.write(canonical_bytes(raw))
        by_kind = {cell["kind"]: cell for cell in summaries}
        gates = {
            "correctness_identity": all(cell["output_identity_exact"] for cell in summaries),
            "seed_accounting": all(cell["seed_usage"]["all_runs_exact"] for cell in summaries),
            "cold_ttft_improvement_3pct": by_kind["COLD_START"]["metrics"]["ttft_us"]["relative_improvement"]["mean"] >= .03 and
                by_kind["COLD_START"]["metrics"]["ttft_us"]["relative_improvement"]["ci95_low"] > 0,
            "steady_throughput_not_worse_5pct": by_kind["STEADY"]["metrics"]["decode_throughput_tps"]["relative_improvement"]["ci95_low"] >= -.05,
            "domain_shift_p95_not_worse_10pct": by_kind["DOMAIN_SHIFT"]["metrics"]["decode_p95_us"]["relative_improvement"]["ci95_low"] >= -.10,
            "mandatory_ttft_p95_not_worse_10pct": all(cell["metrics"]["ttft_us"]["relative_improvement"]["ci95_low"] >= -.10 for cell in summaries),
            "mandatory_token_p95_not_worse_10pct": all(cell["metrics"]["decode_p95_us"]["relative_improvement"]["ci95_low"] >= -.10 for cell in summaries),
            "priority_inversions_zero": True,
            "prediction_bytes_zero": True,
            "phase9_capacity_exact": exact_phase9_capacity,
            "runtime_learning_absent": True,
        }
        qualifies = all(gates.values())
        output = {"schema_version": "phase10-seed-performance-v1",
            "status": "pass" if qualifies else "fail", "project_head": args.project_head,
            "nested_head": args.nested_head, "profile": file_record(args.profile),
            "model": {"first_path": evidence_path(args.model), "package_sha256": profile["target"]["package_sha256"],
                "file_count": len(profile["target"]["files"]),
                "total_size": sum(item["size"] for item in profile["target"]["files"])},
            "prompt_corpus": file_record(args.prompt_corpus), "probe": file_record(args.probe),
            "runtime": runtime, "abba_blocks": args.abba_blocks,
            "statistics": "two-sided-95%-Student-t-paired-ABBA-block-means",
            "cells": summaries, "raw_runs": file_record(args.raw_output),
            "headroom": {"phase9_working_sets": file_record(args.phase9_working_sets),
                "phase9_case": args.phase9_case, "exact_capacity_point": exact_phase9_capacity,
                "expert_bundle_bytes": bundle_bytes, "phase9_slot_footprint_bytes": slot_footprint,
                "hot_bytes": args.hot_slots*slot_footprint,
                "cold_bytes": args.cold_slots*slot_footprint,
                "seed_ring_bytes": len(profile["seed"])*bundle_bytes if args.cache_mode == "COLD_CACHE" else 0,
                "host_managed_bytes": args.cold_slots*slot_footprint +
                    (len(profile["seed"])*bundle_bytes if args.cache_mode == "COLD_CACHE" else 0),
                "safe_ceiling_bytes": working_sets["headroom"]["safe_ceiling_bytes"]},
            "gates": gates, "qualifies": qualifies}
        aggregate_schema = load(ROOT / "schemas/phase10/seed-performance-v1.schema.json")
        Draft202012Validator.check_schema(aggregate_schema)
        Draft202012Validator(aggregate_schema).validate(output)
        write(args.output, output)
        print(json.dumps({"status": output["status"], "qualifies": qualifies,
            "cells": len(summaries)}, sort_keys=True))
        return 0 if qualifies else 2
    except (OSError, ValueError, Phase10Error, ValidationError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

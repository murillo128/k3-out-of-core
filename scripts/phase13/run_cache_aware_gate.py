#!/usr/bin/env python3
"""Capture exact top-M K3 routes and execute the preregistered offline gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from cache_aware_replay import (
    ReplayError,
    canonical_json,
    csv_numbers,
    run_replay,
    validate_capture,
)


DEFAULT_BUNDLE_BYTES = 17_547_264
KIMI_K3_ROUTED_LAYERS = 92
KIMI_K3_TOP_K = 16
KIMI_K3_EXPERTS = 896


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_case(path: Path, case_id: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    corpus = json.loads(raw)
    if corpus.get("schema_version") != "phase13-routing-corpus-v1" or not isinstance(
            corpus.get("cases"), list):
        raise ReplayError("unsupported Phase 13 corpus")
    matches = [case for case in corpus["cases"]
               if isinstance(case, dict) and case.get("id") == case_id]
    if len(matches) != 1:
        raise ReplayError("the requested corpus case must exist exactly once")
    case = matches[0]
    required_strings = ("id", "source", "source_case", "prompt")
    required_integers = (
        "expected_prompt_tokens", "max_generate", "candidate_count", "n_ctx",
        "n_batch", "n_ubatch", "threads")
    if any(not isinstance(case.get(name), str) or not case[name] for name in required_strings) or \
            any(not isinstance(case.get(name), int) or isinstance(case[name], bool) or
                case[name] <= 0 for name in required_integers) or \
            not isinstance(case.get("decision_driving"), bool):
        raise ReplayError("corpus case fields are invalid")
    if not 16 <= case["candidate_count"] <= 64 or \
            case["expected_prompt_tokens"] >= case["n_ctx"] or \
            case["n_ubatch"] > case["n_batch"]:
        raise ReplayError("corpus case bounds are invalid")
    if not isinstance(case.get("expected_generated_ids"), list) or \
            len(case["expected_generated_ids"]) != case["max_generate"] or \
            any(not isinstance(token, int) or isinstance(token, bool) or token < 0
                for token in case["expected_generated_ids"]):
        raise ReplayError("corpus expected generated IDs are invalid")
    return case, sha256_bytes(raw)


def capture_command(probe: Path, model: Path, output: Path, case: dict[str, Any]) -> list[str]:
    return [
        str(probe),
        "--model", str(model),
        "--output", str(output),
        "--prompt", case["prompt"],
        "--candidate-count", str(case["candidate_count"]),
        "--max-generate", str(case["max_generate"]),
        "--n-ctx", str(case["n_ctx"]),
        "--n-batch", str(case["n_batch"]),
        "--n-ubatch", str(case["n_ubatch"]),
        "--threads", str(case["threads"]),
    ]


def validate_decision_capture(capture: dict[str, Any], case: dict[str, Any]) -> None:
    """Validate the fixed full-K3 workload envelope, not only the generic capture shape."""
    if capture.get("cache_aware_routing", {"enabled": False}).get("enabled") is not False:
        raise ReplayError("the offline opportunity gate requires an exact route capture")
    expected_execution = {
        "backend": "CPU",
        "n_gpu_layers": 0,
        "weight_repacking": False,
        "n_ctx": case["n_ctx"],
        "n_batch": case["n_batch"],
        "n_ubatch": case["n_ubatch"],
        "threads": case["threads"],
    }
    sampling = capture.get("sampling", {})
    if capture.get("execution") != expected_execution or \
            sampling.get("temperature") != 0.0 or sampling.get("selection") != "argmax" or \
            sampling.get("teacher_forced", False) is not False:
        raise ReplayError("capture execution configuration does not match the fixed workload")
    expected_layers = set(range(1, KIMI_K3_ROUTED_LAYERS + 1))
    prefill_ubatches = (
        case["expected_prompt_tokens"] + case["n_ubatch"] - 1) // case["n_ubatch"]
    expected_ubatches = prefill_ubatches + case["max_generate"] - 1
    by_ubatch: dict[int, list[dict[str, Any]]] = defaultdict(list)
    phase_layer_tokens: dict[tuple[str, int], int] = defaultdict(int)
    expected_copy_bytes = 0

    for record in capture["routes"]:
        if record.get("request_ordinal") != 1 or record.get("n_expert_used") != KIMI_K3_TOP_K or \
                record.get("n_candidates") != case["candidate_count"] or \
                record.get("layer") not in expected_layers or \
                any(expert >= KIMI_K3_EXPERTS for expert in record["candidate_experts"]):
            raise ReplayError("capture is outside the fixed full-K3 routing envelope")
        by_ubatch[record["ubatch_ordinal"]].append(record)
        phase_layer_tokens[(record["phase"], record["layer"])] += record["n_tokens"]
        expected_copy_bytes += record["n_tokens"]*(
            KIMI_K3_TOP_K*8 + case["candidate_count"]*12)

    if set(by_ubatch) != set(range(expected_ubatches)):
        raise ReplayError("capture ubatch ordinals do not match the fixed workload")
    for ubatch, records in by_ubatch.items():
        phases = {record["phase"] for record in records}
        token_counts = {record["n_tokens"] for record in records}
        positions = {tuple(record["positions"]) for record in records}
        expected_phase = "PREFILL" if ubatch < prefill_ubatches else "DECODE"
        expected_tokens = (
            min(case["n_ubatch"], case["expected_prompt_tokens"] - ubatch*case["n_ubatch"])
            if expected_phase == "PREFILL" else 1)
        if len(records) != KIMI_K3_ROUTED_LAYERS or \
                {record["layer"] for record in records} != expected_layers or \
                phases != {expected_phase} or token_counts != {expected_tokens} or len(positions) != 1:
            raise ReplayError("capture ubatch is not a complete full-K3 routed-layer submission")

    expected_phase_tokens = {
        "PREFILL": case["expected_prompt_tokens"],
        "DECODE": case["max_generate"] - 1,
    }
    if any(phase_layer_tokens[(phase, layer)] != count
           for phase, count in expected_phase_tokens.items() for layer in expected_layers):
        raise ReplayError("capture phase token totals do not match the fixed workload")

    stats = capture["observer_stats"]
    if stats["ubatches"] != expected_ubatches or \
            stats["explicit_synchronizations"] != expected_ubatches or \
            stats["layers"] != len(capture["routes"]) or \
            stats["copy_bytes"] != expected_copy_bytes or stats["failures"] != 0:
        raise ReplayError("capture observer accounting does not match the fixed workload")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reuse-capture", action="store_true")
    parser.add_argument("--bundle-bytes", type=int, default=DEFAULT_BUNDLE_BYTES)
    parser.add_argument("--capacities-gib", default="20,32,40,60,64,80,96")
    parser.add_argument("--hot-capacity-gib", type=float, default=0.0)
    parser.add_argument("--candidate-counts", default="16,24,32")
    parser.add_argument("--max-swaps", default="0,1,2,4")
    parser.add_argument("--material-reduction", type=float, default=0.05)
    args = parser.parse_args()

    case, corpus_sha256 = load_case(args.corpus, args.case_id)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture_path = args.output_dir / f"{args.case_id}-topm{case['candidate_count']}.json"
    frontier_path = args.output_dir / f"{args.case_id}-offline-frontier.json"

    if args.reuse_capture:
        if not capture_path.is_file():
            raise ReplayError("--reuse-capture requested but capture does not exist")
    else:
        if capture_path.exists() or frontier_path.exists():
            raise ReplayError("refusing to overwrite existing gate evidence")
        subprocess.run(capture_command(args.probe, args.model, capture_path, case), check=True)

    capture_raw = capture_path.read_bytes()
    capture = validate_capture(json.loads(capture_raw))
    if len(capture["prompt_ids"]) != case["expected_prompt_tokens"]:
        raise ReplayError(
            f"prompt token count mismatch: expected {case['expected_prompt_tokens']}, "
            f"observed {len(capture['prompt_ids'])}")
    if capture["candidate_count"] != case["candidate_count"]:
        raise ReplayError("capture candidate count does not match corpus")
    if capture["generated_ids"] != case["expected_generated_ids"]:
        raise ReplayError("exact generated IDs do not match the accepted baseline")
    validate_decision_capture(capture, case)

    result = run_replay(
        capture=capture,
        bundle_bytes=args.bundle_bytes,
        capacities_gib=csv_numbers(args.capacities_gib, float),
        hot_capacity_gib=args.hot_capacity_gib,
        candidate_counts=csv_numbers(args.candidate_counts, int),
        max_swaps_values=csv_numbers(args.max_swaps, int),
        reroute_phases={"DECODE"},
        material_reduction=args.material_reduction,
    )
    result["input"] = {
        "path": str(capture_path),
        "sha256": sha256_bytes(capture_raw),
        "schema_version": capture["schema_version"],
    }
    result["corpus"] = {
        "path": str(args.corpus),
        "sha256": corpus_sha256,
        "schema_version": "phase13-routing-corpus-v1",
        "case_id": args.case_id,
    }
    result["runner_command"] = sys.argv
    if frontier_path.exists():
        raise ReplayError("refusing to overwrite existing frontier evidence")
    frontier_path.write_text(canonical_json(result))
    print(
        f"PHASE13_GATE disposition={result['disposition']} "
        f"qualifying_points={result['gate']['qualifying_points']} "
        f"capture_sha256={result['input']['sha256']} output={frontier_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

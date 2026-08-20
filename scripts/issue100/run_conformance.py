#!/usr/bin/env python3
"""Run outcome-blind EXACT/S2 conformance before issue #100 scored inference."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from protocol import (
    CACHE_BYTES, CACHE_SLOTS, DEFAULT_BINARY, MODEL_PATH, N_CTX, THREADS,
    atomic_json, file_identity, load_json, repository_identity, sha256_bytes,
    sha256_file,
)
from run_campaign import load_host_helpers, parse_progress, pressure_failures, run_with_envelope


class ConformanceError(RuntimeError):
    pass


FIXTURE_ID = "15-spanish-b8"
FIXTURE_SEED = 100_716_832
FIXTURE_MAX_GENERATED = 4


def selected_fixture(corpus: dict) -> dict:
    matches = [row for row in corpus["cases"] if row.get("id") == FIXTURE_ID]
    if len(matches) != 1:
        raise ConformanceError("frozen non-GPQA fixture is unavailable")
    return matches[0]


def validate_result(path: Path, progress: Path, arm: str, input_value: dict) -> dict:
    result = load_json(path)
    if result.get("schema_version") != "issue100-gpqa-probe-result-v1" or \
            result.get("status") != "pass" or result.get("arm") != arm or \
            result.get("seed") != FIXTURE_SEED:
        raise ConformanceError(f"{arm} result identity/status drift")
    item = result.get("item", {})
    if item.get("id") != FIXTURE_ID or item.get("scored") or \
            item.get("prompt_sha256") != input_value["prompt_sha256"] or \
            item.get("prompt_tokens") != input_value["prompt_tokens"]:
        raise ConformanceError(f"{arm} fixture identity drift")
    execution = result.get("execution", {})
    if execution.get("n_ctx") != N_CTX or execution.get("threads") != THREADS or \
            execution.get("load_mode") != "DIRECT_IO" or not execution.get("native_io_uring"):
        raise ConformanceError(f"{arm} execution envelope drift")
    protocol = result.get("protocol", {})
    if protocol.get("max_generated") != FIXTURE_MAX_GENERATED or \
            protocol.get("prefill_routing") != "EXACT" or \
            protocol.get("s2_activation") != "after-complete-prefill-before-first-generated-token-decode":
        raise ConformanceError(f"{arm} generation boundary drift")
    generation = result.get("generation", {})
    expected_forwards = FIXTURE_MAX_GENERATED - 1
    if generation.get("generated_tokens_including_eog") != FIXTURE_MAX_GENERATED or \
            generation.get("decode_forward_tokens") != expected_forwards or not generation.get("truncated"):
        raise ConformanceError(f"{arm} bounded fixture generation drift")
    routing = result.get("routing", {})
    stats = routing.get("stats", {})
    if arm == "EXACT":
        if routing.get("enabled") or any(stats.get(key, 0) for key in (
            "ubatches", "layers", "decisions", "changed_decisions", "swaps", "failures"
        )):
            raise ConformanceError("EXACT conformance observed routing activity")
    elif not routing.get("enabled") or routing.get("candidate_count") != 32 or \
            routing.get("max_swaps") != 2 or stats.get("ubatches") != expected_forwards or \
            stats.get("layers") != expected_forwards*92 or \
            stats.get("decisions") != expected_forwards*92 or \
            stats.get("failures") != 0:
        raise ConformanceError("S2 conformance route coverage drift")
    if result.get("cache", {}).get("capacity_slots") != CACHE_SLOTS or \
            result.get("cache", {}).get("capacity_bytes") != CACHE_BYTES or \
            result.get("safety", {}).get("status") != "pass" or \
            result.get("safety", {}).get("vm_swap_kib") != 0 or \
            any(result.get("safety", {}).get("terminal_references", {}).values()):
        raise ConformanceError(f"{arm} capacity/safety drift")
    parse_progress(progress, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--corpus", type=Path, default=Path("corpus/phase13/issue102-cross-prompt-v1.json"))
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve(strict=True)
    binary = args.binary.resolve(strict=True)
    model = args.model.resolve(strict=True)
    corpus = load_json(args.corpus.resolve(strict=True))
    fixture = selected_fixture(corpus)
    prompt = fixture["templated_prompt"]
    input_value = {
        "schema_version": "issue100-probe-input-v1",
        "item_id": FIXTURE_ID,
        "rendered_prompt": prompt,
        "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
        "prompt_tokens": fixture["observed_templated_prompt_tokens"],
        "scored": False,
    }
    root = args.output_root.resolve()
    if root.exists():
        raise ConformanceError("conformance output root already exists; evidence is immutable")
    root.mkdir(parents=True)
    host = load_host_helpers(repo_root)
    results = {}
    artifacts = {}
    for ordinal, arm in enumerate(("EXACT", "S2_P50"), 1):
        directory = root / arm.lower()
        directory.mkdir()
        atomic_json(directory / "input.json", input_value)
        command = [
            str(binary), "--model", str(model), "--input", str(directory / "input.json"),
            "--output", str(directory / "probe-result.json"),
            "--progress", str(directory / "progress.jsonl"), "--arm", arm,
            "--seed", str(FIXTURE_SEED), "--cold-cache-bytes", str(CACHE_BYTES),
            "--max-generated", str(FIXTURE_MAX_GENERATED), "--n-ctx", str(N_CTX),
            "--threads", str(THREADS), "--issue-mode", "BATCHED",
        ]
        returncode, timed_out, envelope = run_with_envelope(command, directory, {
            "run_ordinal": ordinal, "arm": arm, "item_id": FIXTURE_ID,
            "stage": "NON_SCORED_CONFORMANCE", "pair_ordinal": None, "s2_ordinal": None,
        }, host)
        if returncode != 0 or timed_out or pressure_failures(envelope):
            raise ConformanceError(f"{arm} probe failed or violated the host envelope")
        result = validate_result(
            directory / "probe-result.json", directory / "progress.jsonl", arm, input_value,
        )
        results[arm] = result
        artifacts[arm] = {
            name: file_identity(directory / name)
            for name in (
                "input.json", "probe-result.json", "progress.jsonl", "stdout.log",
                "stderr.log", "envelope.json",
            )
        }
    if results["EXACT"]["generation"]["token_ids"][0] != \
            results["S2_P50"]["generation"]["token_ids"][0]:
        raise ConformanceError("paired first sampled token differs before any generated decode")
    summary = {
        "schema_version": "issue100-non-scored-conformance-v1",
        "status": "pass",
        "outcome_inspected": False,
        "gpqa_item_used": False,
        "fixture_id": FIXTURE_ID,
        "fixture_prompt_sha256": input_value["prompt_sha256"],
        "fixture_prompt_tokens": input_value["prompt_tokens"],
        "fixture_seed": FIXTURE_SEED,
        "max_generated": FIXTURE_MAX_GENERATED,
        "first_sampled_token_match": True,
        "exact_routing_activity": 0,
        "warm_generated_decode_repetitions": FIXTURE_MAX_GENERATED - 1,
        "s2_decode_forwards": FIXTURE_MAX_GENERATED - 1,
        "s2_routed_layers": (FIXTURE_MAX_GENERATED - 1)*92,
        "s2_routing_decisions": (FIXTURE_MAX_GENERATED - 1)*92,
        "capacity_slots": CACHE_SLOTS,
        "capacity_bytes": CACHE_BYTES,
        "repository": repository_identity(repo_root),
        "binary": file_identity(binary),
        "model_first_shard": file_identity(model, hash_payload=False),
        "artifacts": artifacts,
    }
    atomic_json(root / "conformance.json", summary)
    print(
        "ISSUE100_CONFORMANCE status=pass scored=false first_token_match=true "
        f"exact_routes=0 s2_forwards={FIXTURE_MAX_GENERATED - 1}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 conformance: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

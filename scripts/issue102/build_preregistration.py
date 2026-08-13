#!/usr/bin/env python3
"""Freeze issue #102 corpus/helper identities before any performance result."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def command_output(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--helper-source", required=True, type=Path)
    parser.add_argument("--helper-binary", required=True, type=Path)
    parser.add_argument("--freezer-binary", required=True, type=Path)
    parser.add_argument("--helper-diff", required=True, type=Path)
    parser.add_argument("--compile-commands", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frozen-utc", required=True)
    args = parser.parse_args()

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    compile_commands = json.loads(args.compile_commands.read_text(encoding="utf-8"))
    helper_compile = next(
        item["command"] for item in compile_commands
        if item["file"].endswith("issue102-cross-prompt-probe.cpp")
    )
    cases = corpus["cases"]
    family_band = {
        f"{case['family_index']:02d}/{case['token_band']}": case["id"]
        for case in cases
    }
    if len(cases) != 128 or len(family_band) != 128:
        raise RuntimeError("corpus is not a complete 16 by 8 grid")

    frozen_libraries = Path("/mnt/nvme1/issue77/build/cpu/bin")
    library_names = [
        "libllama-common.so.0.0.10345",
        "libllama.so.0.0.10345",
        "libggml.so.0.17.0",
        "libggml-cpu.so.0.17.0",
        "libggml-base.so.0.17.0",
    ]
    result = {
        "schema_version": "issue102-checkpoint-a-preregistration-v1",
        "status": "frozen-before-performance",
        "frozen_utc": args.frozen_utc,
        "corpus": {
            "path": str(args.corpus),
            "sha256": sha256(args.corpus),
            "primary_cases": len(cases),
            "semantic_families": len({case["semantic_family"] for case in cases}),
            "token_bands": len({case["token_band"] for case in cases}),
            "templated_token_min": min(case["expected_prompt_tokens"] for case in cases),
            "templated_token_max": max(case["expected_prompt_tokens"] for case in cases),
            "prompt_table_canonical_sha256": canonical_sha([
                {
                    "id": case["id"],
                    "family": case["semantic_family"],
                    "band": case["token_band"],
                    "raw_prompt": case["raw_prompt"],
                    "templated_prompt": case["templated_prompt"],
                    "tokens": case["expected_prompt_tokens"],
                }
                for case in cases
            ]),
            "execution_order_canonical_sha256": canonical_sha(corpus["execution_order"]),
            "execution_order_entries": len(corpus["execution_order"]),
            "sentinel_id": corpus["sentinel"]["id"],
            "sentinel_tokens": corpus["sentinel"]["expected_prompt_tokens"],
        },
        "helper": {
            "scope": "measurement orchestration only; no production runtime source change",
            "source_path": str(args.helper_source),
            "source_sha256": sha256(args.helper_source),
            "binary_path": str(args.helper_binary),
            "binary_sha256": sha256(args.helper_binary),
            "freezer_binary_sha256": sha256(args.freezer_binary),
            "bounded_diff_path": str(args.helper_diff),
            "bounded_diff_sha256": sha256(args.helper_diff),
            "bounded_changes": [
                "select a frozen case ID and validate exact templated bytes/token count",
                "support legacy-first-full qualification and full-prompt protocols",
                "continue exact prompt ingestion after first-full occupancy",
                "activate the requested policy and segment counters at the decode boundary",
                "emit case, prefill, and maximum-regret availability metadata",
            ],
        },
        "build": {
            "cmake_build_type": "Release",
            "compiler": command_output("c++", "--version").splitlines()[0],
            "cmake": command_output("cmake", "--version").splitlines()[0],
            "compile_command": helper_compile,
            "host": platform.node(),
            "machine": platform.machine(),
            "linked_frozen_libraries": {
                name: sha256(frozen_libraries / name) for name in library_names
            },
        },
        "frozen_inputs": {
            "project_runtime_parent": "4d2dbaff5b4dc271b34169d11a6bb0bc80e7bba9",
            "nested_llama_cpp": "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9",
            "k3_model_manifest": "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2",
            "model_source": "moonshotai/Kimi-K3@9f62e4e9fffbd0a83ddd60e1c209d828994b3569",
            "model_path": "/mnt/nvme0/issue77/model/kimi-k3-bf16-00001-of-00033.gguf",
            "runtime_build_fingerprint": "d150d179f41ebd2deab49b663e64c909b7d8fa6b4546c716aee889479f633a10",
            "backend": "CPU-only Mode-P/BATCHED",
            "threads": 32,
            "n_ctx": 512,
            "decode_forwards": 64,
            "candidate_count": 32,
            "s2_p50": {"max_swaps": 2, "max_score_regret": 0.007303759455680847},
            "knee": {"max_swaps": 1, "max_score_regret": 0.0030885785818099976},
            "target_cache_slots": 7849,
            "target_cache_bytes": 137728475136,
            "expert_bundle_bytes": 17547264,
            "io": "native io_uring + O_DIRECT",
            "model_device": "nvme0",
        },
        "pre_primary_gates": [
            "independent Checkpoint A review",
            "three fresh n_ctx=512 capacity admission samples",
            "legacy helper parity against the frozen issue98 signature",
            "three fresh full-prompt sentinel baseline processes with deterministic equality",
        ],
        "outcome_independence": {
            "k3_performance_results_inspected": 0,
            "primary_prompt_replacement_after_performance": "forbidden",
            "remaining_order_or_policy_changes_from_intermediate_results": "forbidden",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

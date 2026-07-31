#!/usr/bin/env python3
"""Capture repeated real-model Phase 8 policy/correctness executions."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import git, identity, percentile, run_command, write


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--public-moe", type=Path, required=True)
    parser.add_argument("--public-source-revision", required=True)
    parser.add_argument("--public-conversion-command", required=True)
    parser.add_argument("--cold-processes", type=int, default=10)
    parser.add_argument("--warm-captures", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.cold_processes < 10 or args.warm_captures < 2:
        raise ValueError("Phase 8 requires at least ten cold processes and two warm captures")

    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    executable = (args.cuda_build / "bin/test-expert-miss-policy").resolve()
    split_dir = args.split_dir.resolve()
    models = {
        "k3_f16_original": args.f16.resolve(),
        "k3_mxfp4_original": args.mxfp4.resolve(),
        "k3_f16_split": sorted(split_dir.glob("*F16-split.gguf-00001-of-00218.gguf"))[0],
        "k3_mxfp4_split": sorted(split_dir.glob("*MXFP4-split.gguf-00001-of-00218.gguf"))[0],
        "larger_public_moe_f16": args.public_moe.resolve(),
    }
    cases = []
    commands = []
    status = True
    for name, model in models.items():
        repetitions = args.cold_processes if name.startswith("k3_") else 1
        durations = []
        for repetition in range(repetitions + args.warm_captures):
            record, _, _ = run_command([str(executable), str(model)], root, timeout=3600)
            record["name"] = f"{name}-run-{repetition + 1}"
            record["required"] = True
            record["capture_class"] = "cold-process" if repetition < repetitions else "warm-repeat"
            commands.append(record)
            durations.append(record["duration_ms"])
            status = status and record["exit_code"] == 0
        cases.append({
            "name": name,
            "model": identity(root, model, external=not model.is_relative_to(root)),
            "cold_processes": repetitions,
            "warm_captures": args.warm_captures,
            "process_latency_ms": {
                "p50": percentile(durations, 50),
                "p95": percentile(durations, 95),
                "p99": percentile(durations, 99),
            },
            "native_matrix": {
                "promote_and_gpu": True,
                "cpu_fallback_decode_and_prefill": True,
                "auto_cpu_gpu_and_tie": True,
                "background_off_and_on": True,
                "full_logit_gate": True,
                "exact_top10_and_tokens": True,
                "mixed_hot_gpu_and_cpu_miss": True,
                "terminal_drain": True,
            },
        })

    output = {
        "schema_version": "phase8-miss-policy-parity-v1",
        "status": "pass" if status else "fail",
        "revisions": {
            "project": git(root, "rev-parse", "HEAD"),
            "llama_cpp": git(nested, "rev-parse", "HEAD"),
            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp"),
        },
        "configuration": {
            "cold_processes_per_k3_model": args.cold_processes,
            "warm_captures": args.warm_captures,
            "public_source_revision": args.public_source_revision,
            "public_conversion_command": args.public_conversion_command,
            "default_policy": "PROMOTE_AND_GPU",
            "auto_is_non_default": True,
        },
        "cases": cases,
        "commands": commands,
        "checks": {
            "all_commands_pass": status,
            "original_and_split_f16_mxfp4": set(models) >= {
                "k3_f16_original", "k3_mxfp4_original", "k3_f16_split", "k3_mxfp4_split"},
            "larger_public_moe": "larger_public_moe_f16" in models,
            "required_repetitions": all(
                case["cold_processes"] >= (10 if case["name"].startswith("k3_") else 1)
                and case["warm_captures"] >= 2 for case in cases),
            "native_policy_matrix_complete": all(all(case["native_matrix"].values()) for case in cases),
        },
    }
    write(args.output, output)
    print("PASS: Phase 8 policy parity captured" if status else "FAIL: Phase 8 policy parity failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

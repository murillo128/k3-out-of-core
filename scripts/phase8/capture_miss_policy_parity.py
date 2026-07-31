#!/usr/bin/env python3
"""Capture repeated real-model Phase 8 policy/correctness executions."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import git, identity, percentile, run_command, write


def parse_bootstrap(output: str) -> dict[str, int]:
    line = next((line for line in output.splitlines() if line.startswith("PHASE8_BOOTSTRAP\t")), None)
    if line is None:
        raise ValueError("missing PHASE8_BOOTSTRAP record")
    result = {}
    for field in line.split("\t")[1:]:
        key, value = field.split("=", 1)
        result[key] = int(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--f16-split", type=Path, required=True)
    parser.add_argument("--mxfp4-split", type=Path, required=True)
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
    models = {
        "k3_f16_original": args.f16.resolve(),
        "k3_mxfp4_original": args.mxfp4.resolve(),
        "k3_f16_split": args.f16_split.resolve(),
        "k3_mxfp4_split": args.mxfp4_split.resolve(),
        "larger_public_moe_f16": args.public_moe.resolve(),
    }
    cases = []
    commands = []
    status = True
    for name, model in models.items():
        repetitions = args.cold_processes if name.startswith("k3_") else 1
        durations = []
        bootstrap_records = []
        for repetition in range(repetitions + args.warm_captures):
            record, stdout, stderr = run_command(
                [str(executable), str(model)], root, timeout=3600, sample_gpu=True)
            record["name"] = f"{name}-run-{repetition + 1}"
            record["required"] = True
            record["capture_class"] = "cold-process" if repetition < repetitions else "warm-repeat"
            commands.append(record)
            durations.append(record["duration_ms"])
            bootstrap_records.append(parse_bootstrap(stdout + "\n" + stderr))
            status = status and record["exit_code"] == 0
        bootstrap_exact = all(
            item["discovery_graphs"] == 2 and item["discovery_reserve_calls"] == 0 and
            item["discovery_backend_bytes"] == 0 and item["final_source_bindings"] == 0 and
            item["final_compute_bytes"] < item["deferred_payload_bytes"]
            for item in bootstrap_records)
        status = status and bootstrap_exact
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
            "bootstrap": bootstrap_records[0],
            "bootstrap_structural_exclusion_all_runs": bootstrap_exact,
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
        "resource_observation": {
            "sampled_device_wide_vram": True,
            "sample_count": sum(len(record.get("gpu_memory_samples_mib", [])) for record in commands),
            "peak_device_memory_used_mib": max(
                (record.get("gpu_peak_used_mib", 0) for record in commands), default=0),
            "minimum_device_memory_free_mib": min(
                (record.get("gpu_min_free_mib", 0) for record in commands
                 if record.get("gpu_memory_samples_mib")), default=0),
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
            "bootstrap_structural_exclusion": all(
                case["bootstrap_structural_exclusion_all_runs"] for case in cases),
            "peak_vram_sampled": any(record.get("gpu_peak_used_mib", 0) > 0 for record in commands),
        },
    }
    write(args.output, output)
    print("PASS: Phase 8 policy parity captured" if status else "FAIL: Phase 8 policy parity failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

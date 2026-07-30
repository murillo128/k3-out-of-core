#!/usr/bin/env python3
"""Run the predeclared issue #13 ABBA provider overhead protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    LLAMA_BASE,
    MODELS,
    cmake_configuration,
    compile_cpp,
    ensure_baseline,
    git,
    parse_fields,
    sha256,
    validate_models,
)


T_CRITICAL_ONE_SIDED_95_DF9 = 1.833113
ABBA = ("a", "b", "b", "a")
BUDGETS = {
    "f16-cpu": {"decode": 0.01377163, "prompt": 0.03485397},
    "f16-cuda": {"decode": 0.00988906, "prompt": 0.10027158},
    "mxfp4-cpu": {"decode": 0.02127630, "prompt": 0.10531247},
    "mxfp4-cuda": {"decode": 0.00988906, "prompt": 0.02400604},
}
REQUIRED_TELEMETRY = (
    "load_seconds", "token_latency_p50_seconds", "token_latency_p95_seconds",
    "token_latency_p99_seconds", "peak_rss_kib", "gpu_memory_bytes", "graphs_reused",
)
PROVIDER_COUNTERS = (
    "provider_objects", "provider_bind_calls", "provider_prepare_calls",
    "provider_handles_acquired", "provider_handles_released", "provider_allocations",
    "provider_callbacks", "provider_tensor_copies", "provider_synchronizations",
)
FINAL_CAPTURE_RULE = "single-complete-standing-capture-v1"
FINAL_CAPTURE_APPROVAL_COMMENT_ID = 5127588494


def run_probe(binary: Path, model: Path, gpu_layers: int, mode: str | None) -> dict[str, Any]:
    environment = dict(os.environ)
    library_path = str(binary.parent)
    if environment.get("LD_LIBRARY_PATH"):
        library_path += ":" + environment["LD_LIBRARY_PATH"]
    environment["LD_LIBRARY_PATH"] = library_path
    command = [str(binary), "--model", str(model), "--gpu-layers", str(gpu_layers)]
    if mode is not None:
        command += ["--expert-weights", mode]
    completed = subprocess.run(command, text=True, capture_output=True, env=environment, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"overhead probe failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}")
    metric = parse_fields(completed.stdout, "METRIC")
    if "RESULT\texit=0" not in completed.stdout or metric.get("prompt_tokens") != 5 or metric.get("generated_tokens") != 49:
        raise RuntimeError("overhead probe did not reproduce the deterministic fixture")
    missing_telemetry = [name for name in REQUIRED_TELEMETRY if name not in metric]
    if missing_telemetry:
        raise RuntimeError(f"overhead probe omitted required telemetry: {missing_telemetry}")
    if mode is None:
        metric.update({name: None for name in PROVIDER_COUNTERS})
        provider_counter_availability = "unavailable-pinned-baseline"
    else:
        provider_counter_availability = "reported"
    if mode == "disabled":
        if any(metric.get(name) != 0 for name in PROVIDER_COUNTERS):
            raise RuntimeError(f"disabled structural counter is nonzero: {metric}")
    if mode == "resident":
        if metric.get("provider_objects") != 1 or metric.get("provider_handles_acquired") != metric.get("provider_handles_released"):
            raise RuntimeError(f"resident provider counters are invalid: {metric}")
        if any(metric.get(name) != 0 for name in (
            "provider_allocations", "provider_callbacks", "provider_tensor_copies", "provider_synchronizations"
        )):
            raise RuntimeError(f"resident provider performed prohibited work: {metric}")
    return {
        "command": command,
        "metric": metric,
        "provider_counter_availability": provider_counter_availability,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def analyze(runs: list[dict[str, Any]], metric: str, budget: float, latency: bool) -> dict[str, Any]:
    slowdowns: list[float] = []
    pairs = []
    for offset in range(0, len(runs), 2):
        pair = runs[offset:offset + 2]
        if {item["side"] for item in pair} != {"a", "b"}:
            raise RuntimeError("ABBA adjacency did not produce one A and one B sample")
        a = next(item for item in pair if item["side"] == "a")
        b = next(item for item in pair if item["side"] == "b")
        a_value = float(a["metric"][metric])
        b_value = float(b["metric"][metric])
        slowdown = b_value/a_value - 1.0 if latency else 1.0 - b_value/a_value
        slowdowns.append(slowdown)
        pairs.append({
            "a_run": a["run_ordinal"], "b_run": b["run_ordinal"],
            "a_value": a_value, "b_value": b_value, "relative_b_slowdown": slowdown,
        })
    if len(slowdowns) != 10:
        raise RuntimeError("expected ten adjacent ABBA pairs")
    mean = statistics.fmean(slowdowns)
    deviation = statistics.stdev(slowdowns)
    upper = mean + T_CRITICAL_ONE_SIDED_95_DF9*deviation/math.sqrt(len(slowdowns))
    return {
        "metric": metric,
        "direction": "latency" if latency else "throughput",
        "a_mean": statistics.fmean(float(item["metric"][metric]) for item in runs if item["side"] == "a"),
        "b_mean": statistics.fmean(float(item["metric"][metric]) for item in runs if item["side"] == "b"),
        "paired_mean_relative_b_slowdown": mean,
        "paired_slowdown_sample_standard_deviation": deviation,
        "one_sided_95_percent_upper_bound": upper,
        "critical_value": T_CRITICAL_ONE_SIDED_95_DF9,
        "degrees_of_freedom": 9,
        "fixed_budget": budget,
        "passed": upper <= budget,
        "pairs": pairs,
    }


def comparison(
    name: str,
    model: Path,
    gpu_layers: int,
    a: tuple[Path, str | None, str],
    b: tuple[Path, str | None, str],
    budget: dict[str, float],
) -> dict[str, Any]:
    warmups = []
    for side, specification in (("a", a), ("b", b)):
        sample = run_probe(specification[0], model, gpu_layers, specification[1])
        sample.update({"side": side, "label": specification[2]})
        warmups.append(sample)
    runs = []
    for ordinal, side in enumerate(ABBA*5):
        specification = a if side == "a" else b
        sample = run_probe(specification[0], model, gpu_layers, specification[1])
        sample.update({"side": side, "label": specification[2], "run_ordinal": ordinal})
        runs.append(sample)
    if len({item["metric"]["generated_tokens"] for item in runs}) != 1:
        raise RuntimeError("generated count changed within an overhead comparison")
    analyses = {
        "decode_tokens_per_second": analyze(runs, "decode_tokens_per_second", budget["decode"], False),
        "prompt_tokens_per_second": analyze(runs, "prompt_tokens_per_second", budget["prompt"], False),
        "ttft_seconds": analyze(runs, "ttft_seconds", budget["prompt"], True),
    }
    reported = {}
    for metric in REQUIRED_TELEMETRY:
        reported[metric] = {
            side: statistics.fmean(float(item["metric"][metric]) for item in runs if item["side"] == side)
            for side in ("a", "b")
        }
    return {
        "name": name,
        "a": {"label": a[2], "binary": str(a[0]), "binary_sha256": sha256(a[0]), "mode": a[1]},
        "b": {"label": b[2], "binary": str(b[0]), "binary_sha256": sha256(b[0]), "mode": b[1]},
        "warmups": warmups,
        "order": list(ABBA*5),
        "runs": runs,
        "analysis": analyses,
        "reported_non_gated_means": reported,
        "passed": all(item["passed"] for item in analyses.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--pairs", type=int, required=True)
    parser.add_argument("--order", required=True)
    parser.add_argument("--standing-final-capture", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.baseline_ref != LLAMA_BASE or args.pairs != 10 or args.order != "ABBA":
        raise RuntimeError("performance protocol differs from the issue #13 declaration")
    if not args.standing_final_capture:
        raise RuntimeError("the approved standing-final-capture contract was not acknowledged")
    candidate_revision = git(root / "llama.cpp", "rev-parse", args.candidate_ref)
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    candidate_builds = {"cpu": args.cpu_build.resolve(), "cuda": args.cuda_build.resolve()}
    baseline_builds = ensure_baseline(root)

    with tempfile.TemporaryDirectory(prefix="k3-phase3-overhead-") as temporary_name:
        temporary = Path(temporary_name)
        binaries: dict[tuple[str, str], Path] = {}
        compilations = {}
        matching_configurations = {}
        for backend in ("cpu", "cuda"):
            baseline_binary = temporary / f"overhead-baseline-{backend}"
            candidate_binary = temporary / f"overhead-candidate-{backend}"
            compilations[f"baseline-{backend}"] = compile_cpp(
                root, baseline_builds[backend], baseline_binary,
                [root / "scripts/phase3/overhead_probe_baseline.cpp"],
                baseline_builds[backend].parent / "llama.cpp",
            )
            compilations[f"candidate-{backend}"] = compile_cpp(
                root, candidate_builds[backend], candidate_binary, [root / "scripts/phase2/overhead_probe.cpp"], root / "llama.cpp"
            )
            base_configuration = cmake_configuration(baseline_builds[backend])
            candidate_configuration = cmake_configuration(candidate_builds[backend])
            if base_configuration != candidate_configuration:
                raise RuntimeError(f"baseline and candidate {backend} build configurations differ")
            matching_configurations[backend] = base_configuration
            binaries[("baseline", backend)] = baseline_binary
            binaries[("candidate", backend)] = candidate_binary

        combinations = []
        for artifact, model in models.items():
            for backend in ("cpu", "cuda"):
                budget = BUDGETS[f"{artifact}-{backend}"]
                candidate = binaries[("candidate", backend)]
                comparisons = [
                    comparison(
                        "baseline-vs-disabled", model, 0 if backend == "cpu" else 999,
                        (binaries[("baseline", backend)], None, "isolated-baseline"),
                        (candidate, "disabled", "candidate-disabled"), budget,
                    ),
                    comparison(
                        "disabled-vs-resident", model, 0 if backend == "cpu" else 999,
                        (candidate, "disabled", "candidate-disabled"),
                        (candidate, "resident", "candidate-resident"), budget,
                    ),
                ]
                combinations.append({
                    "artifact": artifact,
                    "backend": backend,
                    "model": {**MODELS[artifact], "path": str(model)},
                    "fixed_budgets": budget,
                    "comparisons": comparisons,
                    "passed": all(item["passed"] for item in comparisons),
                })

        report = {
            "schema_version": "phase3-provider-overhead-v1",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "pass" if all(item["passed"] for item in combinations) else "fail",
            "revisions": {"llama_baseline": LLAMA_BASE, "llama_candidate": candidate_revision},
            "validation_contract": {
                "rule": FINAL_CAPTURE_RULE,
                "approval_comment_id": FINAL_CAPTURE_APPROVAL_COMMENT_ID,
                "complete_capture_count": 1,
                "retry_or_cross_attempt_selection": "forbidden",
                "result_stands": True,
            },
            "protocol": {
                "prompt": "According to all known laws", "context": 512, "generation_cap": 128,
                "temperature": 0, "threads": 8, "warmups_per_side": 1,
                "measured_runs_per_side": 10, "process_order": list(ABBA*5),
                "pairing": "adjacent A/B observations", "critical_value": T_CRITICAL_ONE_SIDED_95_DF9,
                "confidence_bound": "paired mean slowdown + t(0.95, df=9) * standard error",
            },
            "matching_build_configuration": matching_configurations,
            "compilations": compilations,
            "combinations": combinations,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "status": report["status"], "combinations": len(combinations)}, sort_keys=True))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

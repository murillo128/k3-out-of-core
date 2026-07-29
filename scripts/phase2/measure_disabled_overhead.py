#!/usr/bin/env python3
"""Run the issue #10 trace-disabled ABBA overhead protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


T_CRITICAL_ONE_SIDED_95_DF9 = 1.833113
ABBA = ("base", "candidate", "candidate", "base")
LLAMA_BASE_REVISION = "84245db4c790af22135f34992689edcc11877003"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_metric(output: str) -> dict[str, float | int]:
    lines = [line for line in output.splitlines() if line.startswith("METRIC\t")]
    if len(lines) != 1 or "RESULT\texit=0" not in output:
        raise RuntimeError("overhead probe did not produce one successful metric")
    result: dict[str, float | int] = {}
    for field in lines[0].split("\t")[1:]:
        name, value = field.split("=", 1)
        result[name] = float(value) if any(char in value for char in ".eE") else int(value)
    if result.get("prompt_tokens") != 5 or result.get("generated_tokens") != 49:
        raise RuntimeError("overhead probe did not reproduce the Phase 1 token counts")
    return result


def run_probe(binary: Path, model: Path, gpu_layers: int) -> dict[str, Any]:
    environment = dict(os.environ)
    library_path = str(binary.parent)
    if environment.get("LD_LIBRARY_PATH"):
        library_path += ":" + environment["LD_LIBRARY_PATH"]
    environment["LD_LIBRARY_PATH"] = library_path
    command = [str(binary), "--model", str(model), "--gpu-layers", str(gpu_layers)]
    completed = subprocess.run(command, text=True, capture_output=True, env=environment, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"overhead probe failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr}"
        )
    return {
        "command": command,
        "metric": parse_metric(completed.stdout),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def coefficient_of_variation(values: list[float]) -> float:
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / mean


def paired_analysis(
    runs: list[dict[str, Any]], metric: str, phase1_cv: float
) -> dict[str, Any]:
    base_values = [float(run["metric"][metric]) for run in runs if run["binary_kind"] == "base"]
    candidate_values = [
        float(run["metric"][metric]) for run in runs if run["binary_kind"] == "candidate"
    ]
    if len(base_values) != 10 or len(candidate_values) != 10:
        raise RuntimeError("ABBA protocol did not produce ten samples per binary")

    slowdowns: list[float] = []
    pairs: list[dict[str, Any]] = []
    for offset in range(0, len(runs), 2):
        pair = runs[offset : offset + 2]
        if {item["binary_kind"] for item in pair} != {"base", "candidate"}:
            raise RuntimeError("ABBA pair does not contain one base and one candidate run")
        base = next(item for item in pair if item["binary_kind"] == "base")
        candidate = next(item for item in pair if item["binary_kind"] == "candidate")
        base_value = float(base["metric"][metric])
        candidate_value = float(candidate["metric"][metric])
        slowdown = 1.0 - candidate_value / base_value
        slowdowns.append(slowdown)
        pairs.append(
            {
                "base_run": base["run_ordinal"],
                "candidate_run": candidate["run_ordinal"],
                "base_value": base_value,
                "candidate_value": candidate_value,
                "relative_candidate_slowdown": slowdown,
            }
        )

    mean_slowdown = statistics.fmean(slowdowns)
    standard_error = statistics.stdev(slowdowns) / math.sqrt(len(slowdowns))
    upper_bound = mean_slowdown + T_CRITICAL_ONE_SIDED_95_DF9 * standard_error
    control_cv = coefficient_of_variation(base_values)
    budget = 3.0 * max(phase1_cv, control_cv)
    return {
        "base_mean": statistics.fmean(base_values),
        "candidate_mean": statistics.fmean(candidate_values),
        "phase1_cv": phase1_cv,
        "refreshed_control_cv": control_cv,
        "noise_budget": budget,
        "paired_mean_relative_candidate_slowdown": mean_slowdown,
        "paired_slowdown_sample_standard_deviation": statistics.stdev(slowdowns),
        "one_sided_95_percent_upper_bound": upper_bound,
        "critical_value": T_CRITICAL_ONE_SIDED_95_DF9,
        "degrees_of_freedom": 9,
        "passed": upper_bound < budget,
        "pairs": pairs,
    }


def phase1_cvs(path: Path) -> dict[str, float]:
    evidence = json.loads(path.read_text())
    result: dict[str, float] = {}
    for metric in ("prompt_tokens_per_second", "decode_tokens_per_second"):
        cvs = []
        for combination in evidence["combinations"].values():
            aggregation = combination["aggregation"][metric]
            cvs.append(aggregation["population_standard_deviation"] / aggregation["mean"])
        result[metric] = max(cvs)
    return result


def relevant_cmake_configuration(binary: Path) -> dict[str, str]:
    cache = binary.resolve().parent.parent / "CMakeCache.txt"
    result = {}
    for line in cache.read_text().splitlines():
        if line.startswith(("#", "//")) or "=" not in line:
            continue
        key_and_type, value = line.split("=", 1)
        if ":" not in key_and_type:
            continue
        key, _ = key_and_type.split(":", 1)
        if key.startswith("GGML_") or key in {
            "CMAKE_BUILD_TYPE",
            "CMAKE_C_COMPILER",
            "CMAKE_CXX_COMPILER",
            "CMAKE_CUDA_COMPILER",
            "CMAKE_CUDA_ARCHITECTURES",
        }:
            result[key] = value
    return dict(sorted(result.items()))


def measure_combination(
    name: str,
    model: Path,
    gpu_layers: int,
    base_binary: Path,
    candidate_binary: Path,
    cvs: dict[str, float],
) -> dict[str, Any]:
    binaries = {"base": base_binary, "candidate": candidate_binary}
    warmups = []
    for kind in ("base", "candidate"):
        warmup = run_probe(binaries[kind], model, gpu_layers)
        warmup["binary_kind"] = kind
        warmups.append(warmup)

    runs = []
    order = ABBA * 5
    for ordinal, kind in enumerate(order):
        run = run_probe(binaries[kind], model, gpu_layers)
        run["run_ordinal"] = ordinal
        run["binary_kind"] = kind
        runs.append(run)

    analyses = {
        metric: paired_analysis(runs, metric, cvs[metric])
        for metric in ("prompt_tokens_per_second", "decode_tokens_per_second")
    }
    return {
        "name": name,
        "model": {"path": str(model), "size_bytes": model.stat().st_size, "sha256": sha256(model)},
        "gpu_layers": gpu_layers,
        "binaries": {
            kind: {"path": str(binary), "sha256": sha256(binary)}
            for kind, binary in binaries.items()
        },
        "warmups": warmups,
        "order": list(order),
        "runs": runs,
        "analysis": analyses,
        "passed": all(result["passed"] for result in analyses.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--base-cpu", type=Path, required=True)
    parser.add_argument("--candidate-cpu", type=Path, required=True)
    parser.add_argument("--base-cuda", type=Path, required=True)
    parser.add_argument("--candidate-cuda", type=Path, required=True)
    parser.add_argument("--phase1-benchmarks", type=Path, required=True)
    parser.add_argument("--llama-base-revision", required=True)
    parser.add_argument("--llama-candidate-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.project_root.resolve()
    if args.llama_base_revision != LLAMA_BASE_REVISION:
        raise RuntimeError("immutable llama.cpp base revision mismatch")
    if len(args.llama_candidate_revision) != 40 or any(
        character not in "0123456789abcdef" for character in args.llama_candidate_revision
    ):
        raise RuntimeError("candidate llama.cpp revision is not an exact commit")

    build_configurations = {}
    for backend, base_binary, candidate_binary in (
        ("cpu", args.base_cpu, args.candidate_cpu),
        ("cuda", args.base_cuda, args.candidate_cuda),
    ):
        base_configuration = relevant_cmake_configuration(base_binary)
        candidate_configuration = relevant_cmake_configuration(candidate_binary)
        if base_configuration != candidate_configuration:
            raise RuntimeError(f"base and candidate {backend} build configurations differ")
        build_configurations[backend] = base_configuration
    expected_models = {
        "f16": (
            root / "models/gguf/Kimi-K3-0.40B-F16.gguf",
            784318432,
            "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        ),
        "mxfp4": (
            root / "models/gguf/Kimi-K3-0.40B-MXFP4.gguf",
            751976576,
            "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        ),
    }
    for model, size, digest in expected_models.values():
        if model.stat().st_size != size or sha256(model) != digest:
            raise RuntimeError(f"immutable model identity mismatch: {model}")

    cvs = phase1_cvs(args.phase1_benchmarks)
    combinations = []
    for model_name, (model, _, _) in expected_models.items():
        combinations.append(
            measure_combination(
                f"{model_name}-cpu", model, 0, args.base_cpu.resolve(), args.candidate_cpu.resolve(), cvs
            )
        )
        combinations.append(
            measure_combination(
                f"{model_name}-cuda",
                model,
                999,
                args.base_cuda.resolve(),
                args.candidate_cuda.resolve(),
                cvs,
            )
        )

    report = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "prompt": "According to all known laws",
            "context": 512,
            "generation_cap": 128,
            "temperature": 0,
            "threads": 8,
            "warmups_per_binary": 1,
            "measured_runs_per_binary": 10,
            "process_order": list(ABBA * 5),
            "noise_budget": "3 * max(Phase 1 CV, refreshed control CV)",
            "confidence_bound": "mean paired slowdown + t(0.95, df=9) * standard error",
        },
        "phase1_cv": cvs,
        "revisions": {
            "llama_cpp_base": args.llama_base_revision,
            "llama_cpp_candidate": args.llama_candidate_revision,
        },
        "matching_build_configuration": build_configurations,
        "combinations": combinations,
        "passed": all(combination["passed"] for combination in combinations),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "passed": report["passed"]}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

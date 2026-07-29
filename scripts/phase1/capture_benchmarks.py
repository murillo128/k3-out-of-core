#!/usr/bin/env python3
"""Capture Phase 1 warm stability and baseline resource measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


SCHEMA_VERSION = 1
LLAMA_CPP_COMMIT = "84245db4c790af22135f34992689edcc11877003"
PROMPT = "According to all known laws"
EXPECTED_PROMPT_IDS = [18805, 308, 799, 5624, 12524]
SEED = 1
TEMPERATURE = 0
CONTEXT = 512
GENERATED_TOKENS = 128
THREADS = 8
MEASURED_RUNS = 5
ARTIFACTS = {
    "f16": {
        "path": "models/gguf/Kimi-K3-0.40B-F16.gguf",
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
    },
    "mxfp4": {
        "path": "models/gguf/Kimi-K3-0.40B-MXFP4.gguf",
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
    },
}
BACKENDS = {
    "cpu": {"build": "llama.cpp/build-cpu", "gpu_layers": 0},
    "cuda": {"build": "llama.cpp/build-cuda", "gpu_layers": 999},
}


class BenchmarkError(RuntimeError):
    """Raised when the benchmark contract or evidence fails."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise BenchmarkError(f"command did not complete: {command[0]}: {error}") from error
    return {
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def compile_probe(repo_root: Path, backend: str, destination: Path) -> dict[str, Any]:
    build_bin = repo_root / BACKENDS[backend]["build"] / "bin"
    source = repo_root / "scripts/phase1/benchmark_probe.cpp"
    command = [
        "c++",
        "-std=c++17",
        "-O2",
        "-I",
        str(repo_root / "llama.cpp/include"),
        "-I",
        str(repo_root / "llama.cpp/ggml/include"),
        str(source),
        "-L",
        str(build_bin),
        f"-Wl,-rpath,{build_bin}",
        "-lllama",
        "-lggml",
        "-lggml-base",
        "-o",
        str(destination),
    ]
    result = run_command(command, cwd=repo_root, timeout_seconds=120)
    if result["exit_code"] != 0:
        raise BenchmarkError(f"{backend} probe compilation failed: {result['stderr'].strip()}")
    return {
        "backend": backend,
        "source": source.relative_to(repo_root).as_posix(),
        "build": BACKENDS[backend]["build"],
        "exit_code": 0,
        "duration_seconds": result["duration_seconds"],
    }


def key_values(parts: list[str]) -> dict[str, str]:
    try:
        return dict(part.split("=", 1) for part in parts)
    except ValueError as error:
        raise BenchmarkError(f"invalid key/value record: {parts}") from error


def parse_ids(text: str) -> list[int]:
    try:
        return [int(value) for value in text.split(",")] if text else []
    except ValueError as error:
        raise BenchmarkError(f"invalid token ID record: {text}") from error


def parse_probe_stdout(stdout: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {"devices": [], "runs": []}
    latency_map: dict[tuple[str, int], list[float]] = {}
    id_map: dict[tuple[str, int], list[int]] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "CONFIG":
            parsed["config"] = key_values(parts[1:])
        elif parts[0] == "DEVICE" and len(parts) == 7:
            parsed["devices"].append(
                {
                    "index": int(parts[1]),
                    "name": parts[2],
                    "description": parts[3],
                    "type": int(parts[4]),
                    "free_bytes_at_discovery": int(parts[5]),
                    "total_bytes": int(parts[6]),
                }
            )
        elif parts[0] == "LOAD":
            values = key_values(parts[1:])
            parsed["load"] = {
                "seconds": float(values["seconds"]),
                "peak_rss_kib": int(values["peak_rss_kib"]),
                "gpu_baseline_used_bytes": int(values["gpu_baseline_used_bytes"]),
                "gpu_used_after_load_bytes": int(values["gpu_used_after_load_bytes"]),
            }
        elif parts[0] == "PROMPT_IDS" and len(parts) == 2:
            parsed["prompt_ids"] = parse_ids(parts[1])
        elif parts[0] == "RUN":
            values = key_values(parts[3:])
            parsed["runs"].append(
                {
                    "kind": parts[1],
                    "index": int(parts[2]),
                    "prompt_tokens": int(values["prompt_tokens"]),
                    "generated_tokens": int(values["generated_tokens"]),
                    "terminal_eog": values["terminal_eog"] == "1",
                    "ttft_seconds": float(values["ttft_seconds"]),
                    "prompt_tokens_per_second": float(values["prompt_tokens_per_second"]),
                    "decode_tokens_per_second": float(values["decode_tokens_per_second"]),
                    "peak_rss_kib": int(values["peak_rss_kib"]),
                    "gpu_used_peak_bytes": int(values["gpu_used_peak_bytes"]),
                }
            )
        elif parts[0] == "LATENCIES" and len(parts) == 4:
            latency_map[(parts[1], int(parts[2]))] = [
                float(value) for value in parts[3].split(",") if value
            ]
        elif parts[0] == "IDS" and len(parts) == 4:
            id_map[(parts[1], int(parts[2]))] = parse_ids(parts[3])
        elif parts[0] == "RESULT":
            parsed["result"] = key_values(parts[1:])
    required = ("config", "load", "prompt_ids", "result")
    missing = [name for name in required if name not in parsed]
    if missing:
        raise BenchmarkError(f"benchmark stdout missing records: {missing}")
    for run in parsed["runs"]:
        key = (run["kind"], run["index"])
        if key not in latency_map or key not in id_map:
            raise BenchmarkError(f"benchmark run is missing latency or ID data: {key}")
        run["decode_token_latencies_seconds"] = latency_map[key]
        run["generated_ids"] = id_map[key]
    return parsed


def validate_probe_contract(parsed: dict[str, Any], backend: str) -> dict[str, bool]:
    warmups = [run for run in parsed["runs"] if run["kind"] == "warmup"]
    measured = [run for run in parsed["runs"] if run["kind"] == "measured"]
    expected_config = {
        "prompt": PROMPT,
        "seed": str(SEED),
        "temperature": str(TEMPERATURE),
        "context": str(CONTEXT),
        "generate": str(GENERATED_TOKENS),
        "threads": str(THREADS),
        "gpu_layers": str(BACKENDS[backend]["gpu_layers"]),
    }
    checks = {
        "configuration_exact": parsed["config"] == expected_config,
        "prompt_ids_exact": parsed["prompt_ids"] == EXPECTED_PROMPT_IDS,
        "one_model_load": parsed["result"].get("load_calls") == "1",
        "one_discarded_warmup": len(warmups) == 1 and parsed["result"].get("warmups") == "1",
        "five_measured_runs": len(measured) == MEASURED_RUNS
        and [run["index"] for run in measured] == list(range(MEASURED_RUNS))
        and parsed["result"].get("measured") == str(MEASURED_RUNS),
        "result_exit_zero": parsed["result"].get("exit") == "0",
        "all_runs_respect_128_token_cap": all(
            1 <= run["generated_tokens"] <= GENERATED_TOKENS
            and len(run["generated_ids"]) == run["generated_tokens"]
            and (run["generated_tokens"] == GENERATED_TOKENS or run["terminal_eog"])
            for run in parsed["runs"]
        ),
        "all_runs_have_one_less_decode_latency_than_generated_tokens": all(
            len(run["decode_token_latencies_seconds"]) == run["generated_tokens"] - 1
            for run in parsed["runs"]
        ),
        "all_metrics_finite_positive": all(
            run["ttft_seconds"] > 0
            and run["prompt_tokens_per_second"] > 0
            and run["decode_tokens_per_second"] > 0
            and all(value > 0 and math.isfinite(value) for value in run["decode_token_latencies_seconds"])
            for run in parsed["runs"]
        ),
    }
    if not all(checks.values()):
        raise BenchmarkError(f"{backend} benchmark contract failed: {checks}")
    return checks


def hard_failure_scan(stderr: str) -> dict[str, Any]:
    patterns = {
        "nan_or_inf": r"(?i)(?:^|[^a-z])(nan|inf)(?:[^a-z]|$)",
        "invalid_expert": r"(?i)invalid expert(?: id)?",
        "cuda_unavailable": r"(?i)(no usable GPU|CUDA error|failed to initialize CUDA)",
        "allocation_failure": r"(?i)(failed to allocate|out of memory)",
        "hidden_fallback_warning": r"(?i)warn(?:ing)?.*fallback",
        "probe_error": r"BENCH_ERROR",
    }
    matches = {
        name: re.findall(pattern, stderr)
        for name, pattern in patterns.items()
        if re.search(pattern, stderr)
    }
    return {"matches": matches, "passed": not matches}


def normalized_text(value: str, temporary: Path) -> str:
    replaced = value.replace(str(temporary), "<temporary>")
    result = "\n".join(line.rstrip() for line in replaced.splitlines())
    return result + ("\n" if replaced.endswith("\n") else "")


def write_log(path: Path, run: dict[str, Any], temporary: Path) -> None:
    command = " ".join(normalized_text(value, temporary) for value in run["command"])
    content = (
        f"command: {command}\n"
        f"exit_code: {run['exit_code']}\n"
        f"duration_seconds: {run['duration_seconds']}\n"
        "\n=== stdout ===\n"
        f"{normalized_text(run['stdout'], temporary)}"
        "\n=== stderr ===\n"
        f"{normalized_text(run['stderr'], temporary)}"
    )
    path.write_text(content, encoding="utf-8")


def metric_summary(values: list[float]) -> dict[str, float]:
    if not values:
        raise BenchmarkError("cannot summarize an empty metric")
    return {
        "mean": statistics.fmean(values),
        "minimum": min(values),
        "maximum": max(values),
        "population_standard_deviation": statistics.pstdev(values),
    }


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        raise BenchmarkError("cannot calculate percentiles for empty values")
    result = np.percentile(np.asarray(values, dtype=np.float64), [50, 95, 99], method="linear")
    return {"p50": float(result[0]), "p95": float(result[1]), "p99": float(result[2])}


def aggregate_runs(parsed: dict[str, Any], phase5_ids: list[int]) -> dict[str, Any]:
    warmup = next(run for run in parsed["runs"] if run["kind"] == "warmup")
    measured = [run for run in parsed["runs"] if run["kind"] == "measured"]
    reference = warmup["generated_ids"]
    token_stability = {
        "warmup_and_all_measured_exact": all(run["generated_ids"] == reference for run in measured),
        "five_measured_exact": all(
            run["generated_ids"] == measured[0]["generated_ids"] for run in measured
        ),
        "phase5_32_token_prefix_exact": reference[:32] == phase5_ids,
        "reference_generated_ids": reference,
    }
    if not all(value for key, value in token_stability.items() if key != "reference_generated_ids"):
        raise BenchmarkError(f"warm-run token stability failed: {token_stability}")
    pooled_latencies = [
        latency
        for run in measured
        for latency in run["decode_token_latencies_seconds"]
    ]
    gpu_peaks = [run["gpu_used_peak_bytes"] for run in measured if run["gpu_used_peak_bytes"] >= 0]
    gpu_baseline = parsed["load"]["gpu_baseline_used_bytes"]
    return {
        "warmup": warmup,
        "measured_runs": measured,
        "termination": {
            "maximum_generated_tokens": GENERATED_TOKENS,
            "observed_generated_tokens": len(reference),
            "terminal_eog": warmup["terminal_eog"],
            "terminal_token_id": reference[-1],
            "all_runs_same_count_and_termination": all(
                len(run["generated_ids"]) == len(reference)
                and run["terminal_eog"] == warmup["terminal_eog"]
                and run["generated_ids"][-1] == reference[-1]
                for run in measured
            ),
        },
        "aggregation": {
            "prompt_tokens_per_second": metric_summary(
                [run["prompt_tokens_per_second"] for run in measured]
            ),
            "decode_tokens_per_second": metric_summary(
                [run["decode_tokens_per_second"] for run in measured]
            ),
            "ttft_seconds": {
                **metric_summary([run["ttft_seconds"] for run in measured]),
                **percentiles([run["ttft_seconds"] for run in measured]),
            },
            "pooled_decode_token_latency_seconds": {
                "sample_count": len(pooled_latencies),
                **percentiles(pooled_latencies),
                "mean": statistics.fmean(pooled_latencies),
            },
            "peak_rss_kib": max(run["peak_rss_kib"] for run in measured),
            "peak_rss_bytes": max(run["peak_rss_kib"] for run in measured) * 1024,
            "peak_device_vram_used_bytes": max(gpu_peaks) if gpu_peaks else None,
            "peak_device_vram_delta_from_preload_baseline_bytes": (
                max(gpu_peaks) - gpu_baseline if gpu_peaks else None
            ),
        },
        "token_stability": token_stability,
    }


def run_probe(
    repo_root: Path,
    temporary: Path,
    executable: Path,
    artifact: str,
    backend: str,
    output_dir: Path,
    phase5_ids: list[int],
) -> dict[str, Any]:
    command = [
        str(executable),
        "--model",
        str(repo_root / ARTIFACTS[artifact]["path"]),
        "--gpu-layers",
        str(BACKENDS[backend]["gpu_layers"]),
    ]
    environment = os.environ.copy()
    build_bin = repo_root / BACKENDS[backend]["build"] / "bin"
    environment["LD_LIBRARY_PATH"] = str(build_bin)
    raw = run_command(
        command,
        cwd=repo_root,
        environment=environment,
        timeout_seconds=1200,
    )
    log_path = output_dir / f"benchmark-{artifact}-{backend}.log"
    write_log(log_path, raw, temporary)
    if raw["exit_code"] != 0:
        raise BenchmarkError(f"{artifact}/{backend} exited {raw['exit_code']}; see {log_path}")
    parsed = parse_probe_stdout(raw["stdout"])
    checks = validate_probe_contract(parsed, backend)
    failures = hard_failure_scan(raw["stderr"])
    if not failures["passed"]:
        raise BenchmarkError(f"{artifact}/{backend} hard-failure scan failed: {failures}")
    gpu_devices = [device for device in parsed["devices"] if device["type"] == 1]
    if backend == "cuda" and not any("GTX 1650" in device["description"] for device in gpu_devices):
        raise BenchmarkError(f"{artifact}/cuda did not discover the GTX 1650")
    return {
        "artifact": artifact,
        "backend": backend,
        "exit_code": raw["exit_code"],
        "wall_duration_seconds": raw["duration_seconds"],
        "log": log_path.relative_to(repo_root).as_posix(),
        "devices": parsed["devices"],
        "load": parsed["load"],
        "contract_checks": checks,
        "hard_failure_scan": failures,
        **aggregate_runs(parsed, phase5_ids),
    }


def capture(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    for artifact, metadata in ARTIFACTS.items():
        path = repo_root / metadata["path"]
        if not path.is_file() or sha256_file(path) != metadata["sha256"]:
            raise BenchmarkError(f"{artifact} artifact is missing or has the wrong SHA-256")
    submodule_sha = subprocess.run(
        ["git", "-C", "llama.cpp", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if submodule_sha != LLAMA_CPP_COMMIT:
        raise BenchmarkError(f"llama.cpp is {submodule_sha}, expected {LLAMA_CPP_COMMIT}")
    phase5 = json.loads(
        (output_dir / "inference.json").read_text(encoding="utf-8")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="k3-phase1-benchmark-") as directory:
        temporary = Path(directory)
        executables: dict[str, Path] = {}
        compilations = []
        for backend in BACKENDS:
            executable = temporary / f"benchmark-probe-{backend}"
            compilations.append(compile_probe(repo_root, backend, executable))
            executables[backend] = executable
        combinations = {}
        for artifact in ARTIFACTS:
            for backend in BACKENDS:
                key = f"{artifact}-{backend}"
                combinations[key] = run_probe(
                    repo_root,
                    temporary,
                    executables[backend],
                    artifact,
                    backend,
                    output_dir,
                    phase5["runs"][key]["generated_ids"],
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "configuration": {
            "prompt": PROMPT,
            "prompt_ids": EXPECTED_PROMPT_IDS,
            "seed": SEED,
            "temperature": TEMPERATURE,
            "sampling": "greedy argmax; seed has no stochastic effect at temperature 0",
            "context": CONTEXT,
            "maximum_generated_tokens": GENERATED_TOKENS,
            "threads": THREADS,
            "per_combination": {
                "fresh_process": True,
                "model_loads": 1,
                "discarded_warmups": 1,
                "measured_warm_runs": MEASURED_RUNS,
                "model_retained_across_warmup_and_measured_runs": True,
                "context_recreated_before_each_inference": True,
            },
        },
        "methodology": {
            "cold_load": "first and only llama_model_load_from_file call in a fresh process; OS page cache was not flushed",
            "ttft": "prompt decode plus finite greedy argmax for the first generated token; context creation excluded",
            "decode_latency": "each subsequent token decode plus finite greedy argmax; memory sampling excluded",
            "prompt_throughput": "prompt token count divided by TTFT",
            "decode_throughput": "generated tokens after the first divided by their summed decode latency; natural EOG is preserved",
            "percentiles": "NumPy linear interpolation; decode p50/p95/p99 pool all post-first-token latencies from five measured runs",
            "run_aggregation": "arithmetic mean/min/max and population standard deviation across five measured runs",
            "rss": "Linux getrusage(RUSAGE_SELF).ru_maxrss process high-water mark, converted from KiB",
            "vram": "device-wide total-minus-free sampled before load, after load/context, and after each timed decode; sampling occurs outside latency intervals",
            "warmup": "executed and retained for stability comparison but excluded from performance aggregation",
            "claims": "descriptive Phase 1 baseline only; no optimization or cross-format performance claim",
        },
        "inputs": {
            "llama_cpp_commit": LLAMA_CPP_COMMIT,
            "artifacts": ARTIFACTS,
            "phase5_evidence": "results/2026-07-29/skynet/phase1-closeout-clean/inference.json",
        },
        "probe_compilation": compilations,
        "combinations": combinations,
        "summary": {
            "combination_count": len(combinations),
            "total_model_loads": len(combinations),
            "total_discarded_warmups": len(combinations),
            "total_measured_runs": len(combinations) * MEASURED_RUNS,
            "total_successful_inferences": len(combinations) * (MEASURED_RUNS + 1),
            "all_contract_checks_pass": all(
                all(result["contract_checks"].values()) for result in combinations.values()
            ),
            "all_hard_failure_scans_pass": all(
                result["hard_failure_scan"]["passed"] for result in combinations.values()
            ),
            "all_token_stability_checks_pass": all(
                all(
                    value
                    for key, value in result["token_stability"].items()
                    if key != "reference_generated_ids"
                )
                for result in combinations.values()
            ),
        },
    }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/2026-07-29/skynet/phase1-closeout-clean"),
    )
    parser.add_argument("--output", default="benchmarks.json")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    try:
        document = capture(repo_root, output_dir)
        (output_dir / args.output).write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (BenchmarkError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"benchmark capture failed: {error}", file=sys.stderr)
        return 1
    compact = {
        key: {
            "load_seconds": result["load"]["seconds"],
            "prompt_tps_mean": result["aggregation"]["prompt_tokens_per_second"]["mean"],
            "decode_tps_mean": result["aggregation"]["decode_tokens_per_second"]["mean"],
            "ttft_p50_seconds": result["aggregation"]["ttft_seconds"]["p50"],
            "decode_latency_p95_seconds": result["aggregation"]["pooled_decode_token_latency_seconds"]["p95"],
        }
        for key, result in document["combinations"].items()
    }
    print(json.dumps(compact, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

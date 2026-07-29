#!/usr/bin/env python3
"""Capture deterministic CPU/CUDA inference, logits, and placement evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
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
SEED = 1
TEMPERATURE = 0
CONTEXT = 512
MAX_GENERATED_TOKENS = 32
THREADS = 8
TOP_K = 10
EXPECTED_PROMPT_IDS = [18805, 308, 799, 5624, 12524]
LOGIT_THRESHOLDS = {
    "maximum_absolute_difference": 0.10,
    "mean_absolute_difference": 0.02,
    "cosine_similarity": 0.999,
}
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
LAYER_ASSIGNMENT = re.compile(r"load_tensors: layer\s+(\d+) assigned to device\s+(\S+)")
BUFFER_SIZE = re.compile(
    r":\s+(.+?)\s+(model|KV|compute|output) buffer size\s+(?:=|is)\s+([0-9.]+) MiB"
)


class InferenceError(RuntimeError):
    """Raised when deterministic inference evidence fails its contract."""


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
        raise InferenceError(f"command did not complete: {command[0]}: {error}") from error
    return {
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def compile_probe(repo_root: Path, backend: str, destination: Path) -> dict[str, Any]:
    build_bin = repo_root / BACKENDS[backend]["build"] / "bin"
    source = repo_root / "scripts/phase1/inference_probe.cpp"
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
        raise InferenceError(f"{backend} probe compilation failed: {result['stderr'].strip()}")
    return {
        "backend": backend,
        "compiler": command[0],
        "source": source.relative_to(repo_root).as_posix(),
        "build": BACKENDS[backend]["build"],
        "exit_code": result["exit_code"],
        "duration_seconds": result["duration_seconds"],
    }


def parse_ids(text: str) -> list[int]:
    if not text:
        return []
    try:
        return [int(value) for value in text.split(",")]
    except ValueError as error:
        raise InferenceError(f"invalid token ID record: {text}") from error


def parse_probe_stdout(stdout: str) -> dict[str, Any]:
    result: dict[str, Any] = {"devices": [], "steps": []}
    for raw_line in stdout.splitlines():
        parts = raw_line.split("\t")
        if parts[0] == "CONFIG":
            result["config"] = dict(part.split("=", 1) for part in parts[1:])
        elif parts[0] == "DEVICE" and len(parts) == 7:
            result["devices"].append(
                {
                    "index": int(parts[1]),
                    "name": parts[2],
                    "description": parts[3],
                    "type": int(parts[4]),
                    "free_bytes_at_discovery": int(parts[5]),
                    "total_bytes": int(parts[6]),
                }
            )
        elif parts[0] == "MODEL":
            result["model"] = {
                key: int(value)
                for key, value in (part.split("=", 1) for part in parts[1:])
            }
        elif parts[0] == "PROMPT_IDS" and len(parts) == 2:
            result["prompt_ids"] = parse_ids(parts[1])
        elif parts[0] == "STEP" and len(parts) == 5:
            top = []
            for pair in parts[4].split(","):
                token_id, logit = pair.split(":", 1)
                top.append({"token_id": int(token_id), "logit": float(logit)})
            result["steps"].append(
                {
                    "index": int(parts[1]),
                    "generated_id": int(parts[2]),
                    "is_eog": parts[3] == "1",
                    "top_10": top,
                }
            )
        elif parts[0] == "GENERATED_IDS" and len(parts) == 2:
            result["generated_ids"] = parse_ids(parts[1])
        elif parts[0] == "RESULT":
            result["result"] = dict(part.split("=", 1) for part in parts[1:])
    required = ("config", "model", "prompt_ids", "generated_ids", "result")
    missing = [name for name in required if name not in result]
    if missing:
        raise InferenceError(f"probe stdout is missing records: {missing}")
    if len(result["steps"]) != len(result["generated_ids"]):
        raise InferenceError("step count and generated ID count disagree")
    if any(len(step["top_10"]) != TOP_K for step in result["steps"]):
        raise InferenceError("a probe step does not contain ten selected logits")
    return result


def validate_probe_contract(parsed: dict[str, Any], backend: str) -> dict[str, bool]:
    expected_config = {
        "prompt": PROMPT,
        "seed": str(SEED),
        "temperature": str(TEMPERATURE),
        "context": str(CONTEXT),
        "generate": str(MAX_GENERATED_TOKENS),
        "threads": str(THREADS),
        "gpu_layers": str(BACKENDS[backend]["gpu_layers"]),
    }
    checks = {
        "configuration_exact": parsed["config"] == expected_config,
        "prompt_ids_exact": parsed["prompt_ids"] == EXPECTED_PROMPT_IDS,
        "generated_exactly_32_tokens": len(parsed["generated_ids"]) == MAX_GENERATED_TOKENS,
        "step_indexes_contiguous": [step["index"] for step in parsed["steps"]]
        == list(range(MAX_GENERATED_TOKENS)),
        "step_ids_match_generated_ids": [step["generated_id"] for step in parsed["steps"]]
        == parsed["generated_ids"],
        "probe_result_exact": parsed["result"]
        == {"steps": str(MAX_GENERATED_TOKENS), "exit": "0"},
    }
    if not all(checks.values()):
        raise InferenceError(f"{backend} probe contract checks failed: {checks}")
    return checks


def parse_placement(stderr: str, devices: list[dict[str, Any]]) -> dict[str, Any]:
    assignments: dict[str, list[int]] = {}
    for match in LAYER_ASSIGNMENT.finditer(stderr):
        assignments.setdefault(match.group(2).rstrip(","), []).append(int(match.group(1)))
    buffers = [
        {
            "backend_buffer": match.group(1).strip(),
            "kind": match.group(2),
            "size_mib": float(match.group(3)),
        }
        for match in BUFFER_SIZE.finditer(stderr)
    ]
    interesting = []
    markers = (
        "CUDA",
        "offload",
        "buffer size",
        "assigned to device",
        "fallback",
        "using CPU instead",
        "missing support",
    )
    for line in stderr.splitlines():
        if any(marker.lower() in line.lower() for marker in markers):
            interesting.append(line)
    return {
        "devices": devices,
        "layer_assignments": {name: sorted(set(layers)) for name, layers in assignments.items()},
        "buffers": buffers,
        "explicit_cpu_fallback_lines": [
            line
            for line in stderr.splitlines()
            if re.search(r"(?i)(using CPU instead|assigned to device CPU|missing support|fallback)", line)
        ],
        "placement_log_lines": interesting,
    }


def hard_failure_scan(stderr: str, logits: np.ndarray) -> dict[str, Any]:
    patterns = {
        "nan_or_inf_log_text": r"(?i)(?:^|[^a-z])(nan|inf)(?:[^a-z]|$)",
        "invalid_expert_id": r"(?i)invalid expert(?: id)?",
        "cuda_unavailable": r"(?i)(no usable GPU|CUDA error|failed to initialize CUDA)",
        "allocation_failure": r"(?i)(failed to allocate|out of memory)",
        "hidden_fallback_warning": r"(?i)warn(?:ing)?.*fallback",
        "probe_error": r"PROBE_ERROR",
    }
    matches = {
        name: re.findall(pattern, stderr)
        for name, pattern in patterns.items()
        if re.search(pattern, stderr)
    }
    finite = bool(np.isfinite(logits).all())
    return {
        "all_logits_finite": finite,
        "log_scan_matches": matches,
        "passed": finite and not matches,
    }


def write_log(path: Path, command: list[str], run: dict[str, Any], temporary: Path) -> None:
    def normalized(value: str) -> str:
        replaced = value.replace(str(temporary), "<temporary>")
        lines = replaced.splitlines()
        result = "\n".join(line.rstrip() for line in lines)
        return result + ("\n" if replaced.endswith("\n") else "")

    displayed = " ".join(normalized(value) for value in command)
    content = (
        f"command: {displayed}\n"
        f"exit_code: {run['exit_code']}\n"
        f"duration_seconds: {run['duration_seconds']}\n"
        "\n=== stdout ===\n"
        f"{normalized(run['stdout'])}"
        "\n=== stderr ===\n"
        f"{normalized(run['stderr'])}"
    )
    path.write_text(content, encoding="utf-8")


def run_probe(
    repo_root: Path,
    temporary: Path,
    executable: Path,
    artifact: str,
    backend: str,
    output_dir: Path,
) -> tuple[dict[str, Any], np.ndarray]:
    model_path = repo_root / ARTIFACTS[artifact]["path"]
    raw_path = temporary / f"{artifact}-{backend}.f32"
    command = [
        str(executable),
        "--model",
        str(model_path),
        "--raw-logits",
        str(raw_path),
        "--gpu-layers",
        str(BACKENDS[backend]["gpu_layers"]),
    ]
    environment = os.environ.copy()
    build_bin = repo_root / BACKENDS[backend]["build"] / "bin"
    environment["LD_LIBRARY_PATH"] = str(build_bin)
    run = run_command(
        command,
        cwd=repo_root,
        environment=environment,
        timeout_seconds=600,
    )
    log_path = output_dir / f"inference-{artifact}-{backend}.log"
    write_log(log_path, command, run, temporary)
    if run["exit_code"] != 0:
        raise InferenceError(f"{artifact}/{backend} exited {run['exit_code']}; see {log_path}")
    parsed = parse_probe_stdout(run["stdout"])
    contract_checks = validate_probe_contract(parsed, backend)
    vocabulary = parsed["model"]["vocabulary"]
    steps = len(parsed["steps"])
    logits = np.fromfile(raw_path, dtype=np.float32)
    expected_values = steps * vocabulary
    if logits.size != expected_values:
        raise InferenceError(
            f"{artifact}/{backend} wrote {logits.size} logits, expected {expected_values}"
        )
    logits = logits.reshape(steps, vocabulary)
    failures = hard_failure_scan(run["stderr"], logits)
    if not failures["passed"]:
        raise InferenceError(f"{artifact}/{backend} hard-failure scan did not pass: {failures}")
    parsed.update(
        {
            "artifact": artifact,
            "backend": backend,
            "exit_code": run["exit_code"],
            "duration_seconds": run["duration_seconds"],
            "log": log_path.relative_to(repo_root).as_posix(),
            "contract_checks": contract_checks,
            "placement": parse_placement(run["stderr"], parsed.pop("devices")),
            "hard_failure_scan": failures,
            "raw_logit_shape": list(logits.shape),
        }
    )
    return parsed, logits


def vector_metrics(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    if left.shape != right.shape or left.size == 0:
        raise InferenceError(f"cannot compare logit arrays {left.shape} and {right.shape}")
    left64 = left.astype(np.float64, copy=False).reshape(-1)
    right64 = right.astype(np.float64, copy=False).reshape(-1)
    differences = np.abs(left64 - right64)
    denominator = math.sqrt(float(np.dot(left64, left64)) * float(np.dot(right64, right64)))
    if denominator == 0.0:
        raise InferenceError("logit cosine denominator is zero")
    return {
        "maximum_absolute_difference": float(differences.max()),
        "mean_absolute_difference": float(differences.mean()),
        "cosine_similarity": float(np.dot(left64, right64) / denominator),
    }


def compare_same_artifact(
    cpu: dict[str, Any],
    cuda: dict[str, Any],
    cpu_logits: np.ndarray,
    cuda_logits: np.ndarray,
) -> dict[str, Any]:
    prompt_exact = cpu["prompt_ids"] == cuda["prompt_ids"]
    generated_exact = cpu["generated_ids"] == cuda["generated_ids"]
    top_ids_cpu = [[item["token_id"] for item in step["top_10"]] for step in cpu["steps"]]
    top_ids_cuda = [[item["token_id"] for item in step["top_10"]] for step in cuda["steps"]]
    top_id_sets_exact = len(top_ids_cpu) == len(top_ids_cuda) and all(
        set(cpu_ids) == set(cuda_ids)
        for cpu_ids, cuda_ids in zip(top_ids_cpu, top_ids_cuda)
    )
    ordered_rankings_exact = top_ids_cpu == top_ids_cuda
    if cpu_logits.shape != cuda_logits.shape:
        raise InferenceError("same-artifact CPU/CUDA logit shapes differ")
    if not top_id_sets_exact:
        raise InferenceError("same-artifact CPU/CUDA top-10 token ID sets differ")
    selected_cpu = np.array(
        [cpu_logits[step, token] for step, ids in enumerate(top_ids_cpu) for token in ids],
        dtype=np.float32,
    )
    selected_cuda = np.array(
        [cuda_logits[step, token] for step, ids in enumerate(top_ids_cpu) for token in ids],
        dtype=np.float32,
    )
    selected_metrics = vector_metrics(selected_cpu, selected_cuda)
    full_metrics = vector_metrics(cpu_logits, cuda_logits)
    threshold_checks = {
        "maximum_absolute_difference": (
            selected_metrics["maximum_absolute_difference"]
            <= LOGIT_THRESHOLDS["maximum_absolute_difference"]
        ),
        "mean_absolute_difference": (
            selected_metrics["mean_absolute_difference"]
            <= LOGIT_THRESHOLDS["mean_absolute_difference"]
        ),
        "cosine_similarity": (
            selected_metrics["cosine_similarity"]
            >= LOGIT_THRESHOLDS["cosine_similarity"]
        ),
    }
    passed = prompt_exact and generated_exact and top_id_sets_exact and all(threshold_checks.values())
    if not passed:
        raise InferenceError(
            "same-artifact comparison failed: "
            f"prompt={prompt_exact}, generated={generated_exact}, top10={top_id_sets_exact}, "
            f"thresholds={threshold_checks}, metrics={selected_metrics}"
        )
    return {
        "prompt_ids_exact": prompt_exact,
        "generated_ids_exact": generated_exact,
        "top_10_id_sets_exact_each_step": top_id_sets_exact,
        "top_10_ordered_rankings_exact_each_step": ordered_rankings_exact,
        "top_10_ordered_ranking_differences": [
            {
                "step": step,
                "cpu": cpu_ids,
                "cuda": cuda_ids,
            }
            for step, (cpu_ids, cuda_ids) in enumerate(zip(top_ids_cpu, top_ids_cuda))
            if cpu_ids != cuda_ids
        ],
        "selected_top_10_logit_metrics": selected_metrics,
        "full_vocabulary_diagnostic_metrics": full_metrics,
        "thresholds": LOGIT_THRESHOLDS,
        "threshold_checks": threshold_checks,
        "status": "pass",
    }


def compare_cross_format(
    left: dict[str, Any],
    right: dict[str, Any],
    left_logits: np.ndarray,
    right_logits: np.ndarray,
) -> dict[str, Any]:
    common_steps = min(left_logits.shape[0], right_logits.shape[0])
    return {
        "thresholded": False,
        "reason": "F16/MXFP4 cross-format equality is not required by issue #7",
        "prompt_ids_exact": left["prompt_ids"] == right["prompt_ids"],
        "generated_ids_exact": left["generated_ids"] == right["generated_ids"],
        "common_steps": common_steps,
        "full_vocabulary_diagnostic_metrics": vector_metrics(
            left_logits[:common_steps], right_logits[:common_steps]
        ),
    }


def mxfp4_inventory(repo_root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(repo_root / "llama.cpp/gguf-py"))
    try:
        from gguf import GGUFReader
    except ImportError as error:
        raise InferenceError("pinned GGUF reader is unavailable") from error
    finally:
        sys.path.pop(0)
    reader = GGUFReader(repo_root / ARTIFACTS["mxfp4"]["path"], "r")
    names = [tensor.name for tensor in reader.tensors if int(tensor.tensor_type) == 39]
    layers = sorted(
        {
            int(match.group(1))
            for name in names
            if (match := re.match(r"blk\.(\d+)\.", name)) is not None
        }
    )
    return {"tensor_type": 39, "tensor_count": len(names), "layers": layers, "names": names}


def assert_placement(runs: dict[str, dict[str, Any]], inventory: dict[str, Any]) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for artifact in ARTIFACTS:
        cpu_assignments = runs[f"{artifact}-cpu"]["placement"]["layer_assignments"]
        cuda_run = runs[f"{artifact}-cuda"]
        cuda_assignments = cuda_run["placement"]["layer_assignments"]
        cpu_layers = sorted(layer for layers in cpu_assignments.values() for layer in layers)
        cuda_layers = sorted(
            layer
            for device, layers in cuda_assignments.items()
            if device.upper().startswith("CUDA")
            for layer in layers
        )
        gpu_devices = [
            device
            for device in cuda_run["placement"]["devices"]
            if device["name"].upper().startswith("CUDA")
        ]
        cpu_buffers = [
            buffer
            for buffer in cuda_run["placement"]["buffers"]
            if "CPU" in buffer["backend_buffer"].upper()
        ]
        cpu_fallback_lines = cuda_run["placement"]["explicit_cpu_fallback_lines"]
        artifact_checks = {
            "cpu_layers_explicit": all(layer in cpu_layers for layer in range(8)),
            "cuda_layers_explicit": all(layer in cuda_layers for layer in range(8)),
            "gtx_1650_detected": any(
                "GTX 1650" in device["description"] for device in gpu_devices
            ),
            "cuda_cpu_resident_buffers_disclosed": bool(cpu_buffers),
            "cuda_cpu_fallback_disclosed": bool(cpu_fallback_lines),
        }
        if artifact == "mxfp4":
            artifact_checks["all_mxfp4_layers_assigned_cuda"] = all(
                layer in cuda_layers for layer in inventory["layers"]
            )
        if not all(artifact_checks.values()):
            raise InferenceError(f"{artifact} placement checks failed: {artifact_checks}")
        checks[artifact] = {
            "checks": artifact_checks,
            "cpu_layers": cpu_layers,
            "cuda_layers": cuda_layers,
            "cuda_devices": gpu_devices,
            "cuda_cpu_resident_buffers": cpu_buffers,
            "cuda_cpu_fallback_lines": cpu_fallback_lines,
        }
    return {"status": "pass", "artifacts": checks, "mxfp4_inventory": inventory}


def capture(repo_root: Path, output_dir: Path) -> dict[str, Any]:
    for artifact, metadata in ARTIFACTS.items():
        path = repo_root / metadata["path"]
        if not path.is_file() or sha256_file(path) != metadata["sha256"]:
            raise InferenceError(f"{artifact} artifact is missing or has the wrong SHA-256")
    submodule = subprocess.run(
        ["git", "-C", "llama.cpp", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if submodule != LLAMA_CPP_COMMIT:
        raise InferenceError(f"llama.cpp is {submodule}, expected {LLAMA_CPP_COMMIT}")

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="k3-phase1-inference-") as directory:
        temporary = Path(directory)
        executables: dict[str, Path] = {}
        compilations = []
        for backend in BACKENDS:
            executable = temporary / f"inference-probe-{backend}"
            compilations.append(compile_probe(repo_root, backend, executable))
            executables[backend] = executable

        runs: dict[str, dict[str, Any]] = {}
        logits: dict[str, np.ndarray] = {}
        for artifact in ARTIFACTS:
            for backend in BACKENDS:
                key = f"{artifact}-{backend}"
                runs[key], logits[key] = run_probe(
                    repo_root,
                    temporary,
                    executables[backend],
                    artifact,
                    backend,
                    output_dir,
                )

        same_artifact = {
            artifact: compare_same_artifact(
                runs[f"{artifact}-cpu"],
                runs[f"{artifact}-cuda"],
                logits[f"{artifact}-cpu"],
                logits[f"{artifact}-cuda"],
            )
            for artifact in ARTIFACTS
        }
        cross_format = {
            backend: compare_cross_format(
                runs[f"f16-{backend}"],
                runs[f"mxfp4-{backend}"],
                logits[f"f16-{backend}"],
                logits[f"mxfp4-{backend}"],
            )
            for backend in BACKENDS
        }
        placement = assert_placement(runs, mxfp4_inventory(repo_root))

    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "configuration": {
            "prompt": PROMPT,
            "seed": SEED,
            "temperature": TEMPERATURE,
            "sampling": "greedy argmax; seed recorded but has no stochastic effect at temperature 0",
            "context": CONTEXT,
            "maximum_generated_tokens": MAX_GENERATED_TOKENS,
            "threads": THREADS,
            "top_k_logits": TOP_K,
        },
        "inputs": {
            "llama_cpp_commit": LLAMA_CPP_COMMIT,
            "artifacts": ARTIFACTS,
        },
        "probe_compilation": compilations,
        "runs": runs,
        "same_artifact_cpu_cuda": same_artifact,
        "cross_format_diagnostic": cross_format,
        "placement": placement,
        "hard_failures": {
            "all_four_runs_exit_zero": all(run["exit_code"] == 0 for run in runs.values()),
            "all_full_vocabulary_logits_finite": all(
                run["hard_failure_scan"]["all_logits_finite"] for run in runs.values()
            ),
            "no_log_hard_failures": all(
                not run["hard_failure_scan"]["log_scan_matches"] for run in runs.values()
            ),
            "invalid_expert_ids_observed": False,
            "unstable_same_artifact_generation": False,
            "changed_source_hashes": False,
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
    parser.add_argument("--output", default="inference.json")
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
    except (InferenceError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"inference capture failed: {error}", file=sys.stderr)
        return 1
    summary = {
        "status": document["status"],
        "runs": len(document["runs"]),
        "same_artifact": {
            name: result["selected_top_10_logit_metrics"]
            for name, result in document["same_artifact_cpu_cuda"].items()
        },
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

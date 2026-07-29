#!/usr/bin/env python3
"""Measure Phase 2 trace-enabled inference without imposing a pass threshold."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PUBLISHED_GGUF_REVISION = "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"
PROJECT_MEASUREMENT_BASE = "6916466ef98efeb30632563161743cc4487949a6"
EXPECTED_PROMPT_IDS = [18805, 308, 799, 5624, 12524]
EXPECTED_GENERATED_IDS = [
    318, 57195, 11, 1459, 387, 1495, 2189, 261, 56207, 1765, 413, 3700, 308,
    16028, 13, 15149, 40841, 554, 3143, 3307, 308, 922, 1682, 12138, 3572,
    4120, 1468, 276, 7519, 13, 646, 56207,
]
ARTIFACTS = {
    "f16": {
        "name": "Kimi-K3-0.40B-F16.gguf",
        "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
    },
    "mxfp4": {
        "name": "Kimi-K3-0.40B-MXFP4.gguf",
        "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    },
}
BACKENDS = {"cpu": 0, "cuda": 999}
PERFORMANCE_METRICS = (
    "ttft_seconds",
    "prompt_tokens_per_second",
    "decode_tokens_per_second",
    "finalize_seconds",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        check=False,
    )


def compile_probe(root: Path, temporary: Path, backend: str) -> tuple[Path, list[str]]:
    build_bin = root / f"llama.cpp/build-{backend}/bin"
    binary = temporary / f"route-probe-{backend}"
    command = [
        "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
        f"-I{root / 'llama.cpp/include'}",
        f"-I{root / 'llama.cpp/ggml/include'}",
        str(root / "scripts/phase2/route_probe.cpp"),
        str(root / "scripts/phase2/route_trace.cpp"),
        f"-L{build_bin}", f"-Wl,-rpath,{build_bin}",
        "-lllama", "-lggml", "-lggml-base", "-o", str(binary),
    ]
    completed = run(command, root)
    if completed.returncode != 0:
        raise RuntimeError(f"{backend} probe compilation failed: {completed.stderr}")
    return binary, command


def parse_fields(output: str, prefix: str) -> dict[str, float | int]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise RuntimeError(f"expected exactly one {prefix} line")
    result: dict[str, float | int] = {}
    for field in lines[0].split("\t")[1:]:
        name, value = field.split("=", 1)
        result[name] = float(value) if any(character in value for character in ".eE") else int(value)
    return result


def parse_ids(output: str, prefix: str) -> list[int]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise RuntimeError(f"expected exactly one {prefix} line")
    return [int(value) for value in lines[0].split("\t", 1)[1].split(",")]


def trace_arguments(metadata: dict[str, Any], revision: str, trace: Path, run_id: str) -> list[str]:
    return [
        "--trace", str(trace),
        "--model-name", metadata["name"],
        "--model-size", str(metadata["size"]),
        "--model-sha256", metadata["sha256"],
        "--model-source-revision", metadata["source_revision"],
        "--published-gguf-revision", PUBLISHED_GGUF_REVISION,
        "--llama-cpp-revision", revision,
        "--run-id", run_id,
        "--max-ubatch-payload", "131072",
    ]


def measure_sample(
    root: Path,
    temporary: Path,
    binary: Path,
    artifact: str,
    backend: str,
    revision: str,
    ordinal: int,
) -> dict[str, Any]:
    metadata = ARTIFACTS[artifact]
    model = root / "models/gguf" / metadata["name"]
    prefix = temporary / f"{artifact}-{backend}-{ordinal}"
    trace = Path(str(prefix) + ".trace")
    command = [
        str(binary),
        "--model", str(model),
        "--gpu-layers", str(BACKENDS[backend]),
        "--logits", str(prefix) + ".logits",
        "--performance-sample",
    ] + trace_arguments(metadata, revision, trace, f"{artifact}-{backend}-perf-{ordinal}")
    completed = run(command, root)
    if completed.returncode != 0 or "RESULT\texit=0" not in completed.stdout:
        raise RuntimeError(
            f"{artifact}/{backend} sample {ordinal} failed ({completed.returncode}): {completed.stderr}"
        )
    performance = parse_fields(completed.stdout, "TRACE_PERF")
    route = parse_fields(completed.stdout, "ROUTE_STATS")
    expected_route = {
        "ubatches": 32,
        "layers": 224,
        "copy_bytes": 4032,
        "synchronizations": 32,
        "failures": 0,
        "records": 252,
        "flushes": 1,
        "graphs_reused": 30,
    }
    if any(route.get(name) != value for name, value in expected_route.items()):
        raise RuntimeError(f"{artifact}/{backend} route statistics changed: {route}")
    if route.get("trace_bytes") != trace.stat().st_size:
        raise RuntimeError(f"{artifact}/{backend} trace byte accounting mismatch")
    if performance.get("prompt_tokens") != 5 or performance.get("generated_tokens") != 32:
        raise RuntimeError(f"{artifact}/{backend} timing token counts changed")
    if any(float(performance[name]) <= 0.0 for name in PERFORMANCE_METRICS):
        raise RuntimeError(f"{artifact}/{backend} timing sample is not positive")
    if parse_ids(completed.stdout, "PROMPT_IDS") != EXPECTED_PROMPT_IDS:
        raise RuntimeError(f"{artifact}/{backend} prompt IDs changed")
    if parse_ids(completed.stdout, "GENERATED_IDS") != EXPECTED_GENERATED_IDS:
        raise RuntimeError(f"{artifact}/{backend} generated IDs changed")
    return {
        "ordinal": ordinal,
        "performance": performance,
        "route_observer": route,
        "trace_sha256": sha256(trace),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
    }


def aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for metric in PERFORMANCE_METRICS:
        values = [float(sample["performance"][metric]) for sample in samples]
        result[metric] = {
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "minimum": min(values),
            "maximum": max(values),
            "population_standard_deviation": statistics.pstdev(values),
        }
    return result


def hardware() -> dict[str, Any]:
    result: dict[str, Any] = {"node": platform.node(), "platform": platform.platform()}
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        model_names = [
            line.split(":", 1)[1].strip()
            for line in cpuinfo.read_text().splitlines()
            if line.startswith("model name")
        ]
        if model_names:
            result["cpu"] = model_names[0]
    command = ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except FileNotFoundError:
        completed = None
    if completed is not None and completed.returncode == 0:
        result["cuda_devices"] = completed.stdout.strip().splitlines()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--project-measurement-base", required=True)
    parser.add_argument("--llama-revision", required=True)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.project_root.resolve()
    if args.project_measurement_base != PROJECT_MEASUREMENT_BASE:
        raise RuntimeError("project measurement base is not the reviewed Phase 2 head")
    if args.samples < 2:
        raise RuntimeError("at least two measured samples are required")
    nested_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root / "llama.cpp", text=True, capture_output=True, check=True
    ).stdout.strip()
    if nested_head != args.llama_revision:
        raise RuntimeError("llama.cpp worktree does not match the requested exact revision")

    model_evidence = {}
    for artifact, metadata in ARTIFACTS.items():
        model = root / "models/gguf" / metadata["name"]
        if model.stat().st_size != metadata["size"] or sha256(model) != metadata["sha256"]:
            raise RuntimeError(f"{artifact} model identity mismatch")
        model_evidence[artifact] = {
            "path": str(model),
            "size_bytes": model.stat().st_size,
            "sha256": metadata["sha256"],
            "source_revision": metadata["source_revision"],
        }

    combinations = {}
    with tempfile.TemporaryDirectory(prefix="phase2-trace-enabled-") as temporary_name:
        temporary = Path(temporary_name)
        binaries = {}
        compile_commands = {}
        for backend in BACKENDS:
            binaries[backend], compile_commands[backend] = compile_probe(root, temporary, backend)
        for artifact in ARTIFACTS:
            for backend in BACKENDS:
                warmup = measure_sample(
                    root, temporary, binaries[backend], artifact, backend, args.llama_revision, 0
                )
                samples = [
                    measure_sample(
                        root, temporary, binaries[backend], artifact, backend, args.llama_revision, ordinal
                    )
                    for ordinal in range(1, args.samples + 1)
                ]
                combinations[f"{artifact}-{backend}"] = {
                    "artifact": artifact,
                    "backend": backend,
                    "gpu_layers": BACKENDS[backend],
                    "warmup": warmup,
                    "samples": samples,
                    "aggregation": aggregate(samples),
                    "bound_observer_accounting": {
                        name: samples[0]["route_observer"][name]
                        for name in ("copy_bytes", "trace_bytes", "flushes", "synchronizations")
                    },
                }
        binary_evidence = {
            backend: {
                "sha256": sha256(binary),
                "compile_command": compile_commands[backend],
            }
            for backend, binary in binaries.items()
        }

    report = {
        "schema_version": 1,
        "status": "OBSERVED",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "project_measurement_base": args.project_measurement_base,
        "llama_cpp_revision": args.llama_revision,
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "protocol": {
            "warmups_per_combination": 1,
            "measured_samples_per_combination": args.samples,
            "threshold": None,
            "interpretation": "Descriptive trace-enabled measurement; issue #10 defines no arbitrary pass percentage.",
            "timing_boundary": (
                "TTFT begins immediately before prefill annotation and ends after first-token argmax. "
                "Decode throughput covers the remaining 31 annotation/decode/argmax operations. "
                "Trace finalization is excluded from token throughput and reported separately."
            ),
            "probe_note": "Performance mode suppresses logits-file writes; routing observation and trace writes remain enabled.",
        },
        "hardware": hardware(),
        "models": model_evidence,
        "probe_binaries": binary_evidence,
        "combinations": combinations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "combinations": len(combinations), "status": "OBSERVED"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

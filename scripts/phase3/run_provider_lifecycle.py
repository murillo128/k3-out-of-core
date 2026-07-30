#!/usr/bin/env python3
"""Run the issue #13 provider lifecycle, failure, and stress matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import MODELS, cmake_configuration, git, run, sha256, validate_models


def execute(command: list[str], root: Path, name: str) -> dict[str, Any]:
    started = time.monotonic()
    completed = run(command, root, check=False)
    elapsed = time.monotonic() - started
    combined = completed.stdout + "\n" + completed.stderr
    forbidden = [needle for needle in ("AddressSanitizer", "runtime error:", "use-after-free", "double free") if needle in combined]
    if completed.returncode != 0 or forbidden:
        raise RuntimeError(
            f"lifecycle case {name} failed ({completed.returncode}, {forbidden}):\n{completed.stdout}\n{completed.stderr}"
        )
    return {
        "name": name,
        "command": command,
        "returncode": completed.returncode,
        "elapsed_seconds": elapsed,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "forbidden_diagnostics": forbidden,
    }


def ensure_sanitizer_build(root: Path, revision: str) -> Path:
    reusable = sorted(Path("/tmp").glob("k3-phase3-sanitized-*/CMakeCache.txt"))
    build = reusable[-1].parent if reusable else Path("/tmp") / f"k3-phase3-sanitized-{revision[:12]}"
    configure = [
        "cmake", "-S", str(root / "llama.cpp"), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo", "-DBUILD_SHARED_LIBS=ON",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_CURL=OFF", "-DGGML_CUDA=OFF",
        "-DLLAMA_SANITIZE_ADDRESS=ON", "-DGGML_SANITIZE_ADDRESS=ON",
        "-DLLAMA_SANITIZE_UNDEFINED=ON", "-DGGML_SANITIZE_UNDEFINED=ON",
    ]
    run(configure, root)
    run(["cmake", "--build", str(build), "--target", "test-expert-weight-provider", "-j4"], root)
    return build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--cpu-load-cycles", type=int, required=True)
    parser.add_argument("--cuda-load-cycles", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    if args.cpu_load_cycles < 20 or args.cuda_load_cycles < 10:
        raise RuntimeError("stress cycle counts are below the issue #13 minimum")

    revision = git(root / "llama.cpp", "rev-parse", "HEAD")
    builds = {"cpu": args.cpu_build.resolve(), "cuda": args.cuda_build.resolve()}
    binaries = {name: build / "bin/test-expert-weight-provider" for name, build in builds.items()}
    for binary in binaries.values():
        if not binary.is_file():
            raise RuntimeError(f"missing focused test binary: {binary}")

    cases: list[dict[str, Any]] = []
    for backend in ("cpu", "cuda"):
        cases.append(execute([str(binaries[backend])], root, f"{backend}-unit-and-fault-injection"))
        cases.append(execute(
            [str(binaries[backend]), str(models["f16"]), "0" if backend == "cpu" else "999", str(models["mxfp4"])],
            root,
            f"{backend}-mixed-models-interleaved",
        ))

    cycle_results: dict[str, list[dict[str, Any]]] = {"cpu": [], "cuda": []}
    for backend, count in (("cpu", args.cpu_load_cycles), ("cuda", args.cuda_load_cycles)):
        for index in range(count):
            artifact = "f16" if index % 2 == 0 else "mxfp4"
            result = execute(
                [str(binaries[backend]), str(models[artifact]), "0" if backend == "cpu" else "999"],
                root,
                f"{backend}-load-cycle-{index + 1:02d}-{artifact}",
            )
            result["artifact"] = artifact
            result["cycle"] = index + 1
            cycle_results[backend].append(result)

    sanitizer_build = ensure_sanitizer_build(root, revision)
    sanitizer = execute(
        [str(sanitizer_build / "bin/test-expert-weight-provider")],
        root,
        "cpu-asan-ubsan-focused-unit",
    )

    os_release = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    cpu = run(["lscpu", "--json"], root, check=False)
    gpu = run([
        "nvidia-smi", "--query-gpu=name,driver_version,memory.total,compute_cap",
        "--format=csv,noheader,nounits",
    ], root, check=False)

    coverage = {
        "disabled_lifecycle": True,
        "resident_lifecycle": True,
        "shared_two_contexts_sequential_and_interleaved": True,
        "mixed_f16_mxfp4_models_and_modes_one_process": True,
        "repeated_context_create_destroy": True,
        "cpu_model_load_decode_unload_cycles": len(cycle_results["cpu"]),
        "cuda_model_load_decode_unload_cycles": len(cycle_results["cuda"]),
        "partial_initialization_failure": True,
        "graph_binding_failure_and_no_reuse": True,
        "graph_binding_storage_reserved_before_construction": True,
        "request_allocation_and_preparation_failure": True,
        "one_resident_lease_per_nonempty_ubatch": True,
        "empty_binding_set_has_no_lease": True,
        "invalid_key_and_descriptor": True,
        "resident_bundle_first_registration_and_fast_path": True,
        "resident_bundle_conflict_rejected": True,
        "resident_cached_binding_fault_injection": True,
        "resident_registration_thread_safe": True,
        "cancellation_before_submission": True,
        "abort_while_handles_held": True,
        "context_destroy_after_async_submission": True,
        "model_unload_after_contexts": True,
        "backend_recreation_across_processes": True,
        "handles_released_exactly_once": True,
        "asan_ubsan_focused_unit": True,
    }
    report = {
        "schema_version": "phase3-lifecycle-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "revision": revision,
        "models": {
            name: {**MODELS[name], "path": str(path), "observed_sha256": sha256(path)}
            for name, path in models.items()
        },
        "builds": {
            name: {"path": str(build), "configuration": cmake_configuration(build), "binary_sha256": sha256(binaries[name])}
            for name, build in builds.items()
        },
        "environment": {
            "hostname": os.uname().nodename,
            "kernel": os.uname().release,
            "machine": os.uname().machine,
            "os_release": os_release,
            "lscpu_json": json.loads(cpu.stdout) if cpu.returncode == 0 else None,
            "nvidia_smi": gpu.stdout.strip() if gpu.returncode == 0 else None,
        },
        "coverage": coverage,
        "cases": cases,
        "cycles": cycle_results,
        "sanitizer": {**sanitizer, "build": str(sanitizer_build), "configuration": cmake_configuration(sanitizer_build)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "status": "pass", "cases": len(cases), "cycles": sum(map(len, cycle_results.values()))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

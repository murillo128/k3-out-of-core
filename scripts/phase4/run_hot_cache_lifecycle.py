#!/usr/bin/env python3
"""Run issue #17 native lifecycle, fault, sanitizer, and warm-epoch evidence."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from common import (MODELS, cmake_configuration, git, gpu_identity, json_write,
                    parse_fields, run, run_monitored, sha256, validate_models)


def execute(command: list[str], root: Path, name: str, monitored: bool = False) -> dict:
    begin = time.monotonic()
    if monitored:
        completed, peak_gpu_mib = run_monitored(command, root)
    else:
        completed, peak_gpu_mib = run(command, root, check=False), 0.0
    elapsed = time.monotonic() - begin
    combined = completed.stdout + completed.stderr
    forbidden = [value for value in ("AddressSanitizer", "runtime error:", "use-after-free", "double free") if value in combined]
    if completed.returncode != 0 or forbidden:
        raise RuntimeError(f"{name} failed ({completed.returncode}, {forbidden})\n{combined}")
    return {"name": name, "command": command, "returncode": completed.returncode,
            "elapsed_seconds": elapsed, "peak_gpu_memory_mib": peak_gpu_mib,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "forbidden_diagnostics": forbidden, "stdout": completed.stdout}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--warm-epochs", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.warm_epochs < 20:
        raise RuntimeError("warm epochs below issue minimum")
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    builds = {"cpu": args.cpu_build.resolve(), "cuda": args.cuda_build.resolve()}
    focused = {name: build / "bin/test-hot-expert-cache" for name, build in builds.items()}
    provider = {name: build / "bin/test-expert-weight-provider" for name, build in builds.items()}
    probe = builds["cuda"] / "bin/phase4-hot-cache-probe"
    asan = root / "llama.cpp/build-asan/bin/test-hot-expert-cache"
    for binary in [*focused.values(), *provider.values(), probe, asan]:
        if not binary.is_file(): raise FileNotFoundError(binary)

    cases = []
    for backend in ("cpu", "cuda"):
        cases.append(execute([str(provider[backend])], root, f"{backend}-provider-faults"))
        cases.append(execute([str(focused[backend])], root, f"{backend}-hot-cache-mechanism"))
    cases.append(execute([str(asan)], root, "asan-ubsan-hot-cache"))
    for artifact, model in models.items():
        cases.append(execute([str(focused["cuda"]), str(model)], root,
                             f"cuda-real-{artifact}-integration", monitored=True))

    warm = []
    with tempfile.TemporaryDirectory(prefix="k3-phase4-warm-") as name:
        temporary = Path(name)
        for artifact, model in models.items():
            command = [str(probe), "--model", str(model), "--mode", "hot", "--capacity", "56",
                       "--n-ubatch", "5", "--max-generate", str(args.warm_epochs),
                       "--routes", str(temporary / f"{artifact}.routes"),
                       "--logits", str(temporary / f"{artifact}.logits")]
            result = execute(command, root, f"warm-{artifact}-{args.warm_epochs}", monitored=True)
            final = parse_fields(result.pop("stdout"), "PHASE4_FINAL_HOT")
            checks = {"warm_hits": final["hits"] > 0, "pins_balanced": final["current_pins"] == 0 and final["pin_acquires"] == final["pin_releases"],
                      "no_integrity_failures": final["stale_failures"] == final["copy_failures"] == 0,
                      "bounded_directory": final["admissions"] <= 56 and final["generation_changes"] == final["misses"],
                      "no_callback_allocations": final["remap_dynamic_allocations"] == 0}
            if not all(checks.values()): raise RuntimeError(f"warm failure {artifact}: {checks}")
            warm.append({**result, "artifact": artifact, "diagnostics": final, "checks": checks})
    for case in cases:
        case.pop("stdout", None)

    coverage = {
        "configuration_and_context_extent": True, "directory_lru_generation_and_pins": True,
        "whole_bundle_projection_sidecar_copy": True, "precopy_and_copy_failure_cleanup": True,
        "trim_surrender_reinitialize_epoch": True, "cross_context_busy_retry": True,
        "abort_cleanup": True, "graph_reuse": True, "disabled_resident_regression": True,
        "f16_mxfp4_cuda": True, "asan_ubsan": True, "warm_epochs": args.warm_epochs,
    }
    report = {
        "schema_version": "phase4-hot-cache-lifecycle-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": git(root / "llama.cpp", "rev-parse", "HEAD"),
        "models": {name: {**MODELS[name], "path": str(path), "observed_sha256": sha256(path)} for name, path in models.items()},
        "builds": {name: {"path": str(build), "configuration": cmake_configuration(build),
                           "focused_sha256": sha256(focused[name]), "provider_sha256": sha256(provider[name])}
                   for name, build in builds.items()},
        "gpu": gpu_identity(root), "coverage": coverage, "cases": cases, "warm_runs": warm,
    }
    json_write(args.output, report)
    print(f"PASS: wrote {args.output} with {len(cases)} native cases and {len(warm)} warm runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

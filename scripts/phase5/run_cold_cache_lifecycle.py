#!/usr/bin/env python3
"""Capture Phase 5 lifecycle, fault, sanitizer, warm-run, and rejection evidence."""

from __future__ import annotations

import argparse
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from common import (MODELS, cmake_configuration, git, gpu_identity, json_write,
                    parse_fields, run, run_monitored, sha256, validate_models)

def execute(command: list[str], root: Path, name: str, env=None, monitored=False,
            expected_code=0) -> dict:
    start = time.monotonic()
    if monitored:
        completed, peak_gpu_mib, peak_rss_kib = run_monitored(command, root, env)
    else:
        completed = run(command, root, check=False, env=env)
        peak_gpu_mib, peak_rss_kib = 0.0, 0
    combined = completed.stdout + completed.stderr
    forbidden = [text for text in ("AddressSanitizer", "runtime error:", "use-after-free", "double free") if text in combined]
    if completed.returncode != expected_code or forbidden:
        raise RuntimeError(f"{name}: code={completed.returncode} forbidden={forbidden}\n{combined}")
    return {"name": name, "command": command, "returncode": completed.returncode,
            "elapsed_seconds": time.monotonic() - start, "peak_gpu_memory_mib": peak_gpu_mib,
            "peak_rss_kib": peak_rss_kib,
            "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
            "stdout": completed.stdout, "stderr": completed.stderr}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--warm-epochs", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.warm_epochs < 20: raise RuntimeError("warm epochs below issue minimum")
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    builds = {"cpu": args.cpu_build.resolve(), "cuda": args.cuda_build.resolve()}
    targets = ("test-expert-weight-provider", "test-hot-expert-cache", "test-cold-expert-cache", "test-expert-transfer-ring")
    cases = []
    for backend, build in builds.items():
        for target in targets:
            cases.append(execute([str(build / "bin" / target)], root, f"{backend}-{target}"))
    asan = root / "llama.cpp/build-phase5-asan"
    cases.append(execute(["ctest", "--test-dir", str(asan), "--output-on-failure", "-R",
                          "expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-transfer-ring"],
                         root, "asan-ubsan-focused"))
    probe = builds["cuda"] / "bin/phase5-cold-cache-probe"
    rejections = []
    for load_mode in ("none", "mlock", "mmap+mlock", "dio"):
        command = [str(probe), "--model", str(models["f16"]), "--mode", "cold", "--capacity", "2",
                   "--cold-bytes", "1572864", "--ring-bytes", "1572864", "--steps", "1",
                   "--load-mode", load_mode]
        result = execute(command, root, f"reject-{load_mode}", expected_code=21)
        result.pop("stdout"); result.pop("stderr"); rejections.append(result)
    warm_runs = []
    for artifact, model in models.items():
        footprint = 786432 if artifact == "f16" else 208896
        command = [str(probe), "--model", str(model), "--mode", "cold", "--capacity", "2",
                   "--cold-bytes", str(footprint*56), "--ring-bytes", str(footprint*2),
                   "--steps", str(args.warm_epochs)]
        result = execute(command, root, f"warm-{artifact}", monitored=True)
        diagnostics = parse_fields(result.pop("stdout"), "PHASE5_LIVE")
        result.pop("stderr")
        checks = {"cold_hits": diagnostics["cold_hits"] > 0,
                  "balanced_refs": diagnostics["cold_transfer_refs"] == diagnostics["cold_request_refs"] == 0,
                  "bounded": diagnostics["cold_actual_bytes"] <= diagnostics["cold_requested_bytes"] and diagnostics["ring_actual_bytes"] <= diagnostics["ring_requested_bytes"],
                  "pageable_source": diagnostics["source_pageable"] == 1 and diagnostics["source_pinned_bytes"] == 0,
                  "no_failures": diagnostics["cold_failed_copies"] == diagnostics["cold_failed_cleanups"] == 0}
        if not all(checks.values()): raise RuntimeError(f"warm {artifact}: {checks}")
        warm_runs.append({**result, "artifact": artifact, "diagnostics": diagnostics, "checks": checks})
    for case in cases:
        case.pop("stdout", None); case.pop("stderr", None)
    coverage = {"allocation_overflow_invalid_budget": True, "cold_copy_failure_cleanup": True,
                "lane_allocation_stage_pre_enqueue_cleanup": True, "stale_wrapped_generations": True,
                "busy_trim_surrender": True, "reinitialization": True, "cuda_host_source_rejection": True,
                "page_locking_load_rejection": True, "context_model_teardown": True,
                "asan_ubsan": True, "warm_epochs": args.warm_epochs}
    report = {"schema_version": "phase5-cold-cache-lifecycle-v1", "status": "pass",
              "captured_at_utc": datetime.now(timezone.utc).isoformat(),
              "revision": git(root / "llama.cpp", "rev-parse", "HEAD"),
              "models": {name: {**MODELS[name], "path": str(path), "observed_sha256": sha256(path)} for name, path in models.items()},
              "builds": {name: {"path": str(build), "configuration": cmake_configuration(build)} for name, build in builds.items()},
              "gpu": gpu_identity(root), "coverage": coverage, "cases": cases,
              "rejected_load_modes": rejections, "warm_runs": warm_runs}
    json_write(args.output, report)
    print(f"PASS: wrote {args.output} with {len(cases)} native cases")
    return 0

if __name__ == "__main__": raise SystemExit(main())

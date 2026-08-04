#!/usr/bin/env python3
"""Capture or verify Phase 11 Checkpoint C policy, pressure, and lifecycle evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = {
    "f16": {"name": "Kimi-K3-0.40B-F16.gguf", "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "pool_bytes": 1572864},
    "mxfp4": {"name": "Kimi-K3-0.40B-MXFP4.gguf", "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "pool_bytes": 417792},
}
IDENTITY = ("prompt_ids", "tokens", "logits_hash", "route_hash", "route_records")
TEST_PATTERN = "^(test-expert-uma|test-expert-uma-provider|test-cold-expert-cache|test-expert-cache-policy|test-expert-prefetch|test-expert-scheduler)$"
TSAN_PATTERN = "^(test-expert-uma|test-cold-expert-cache|test-expert-cache-policy|test-expert-prefetch|test-expert-scheduler)$"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, require_success: bool = True,
        environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    import os
    env = os.environ.copy()
    if environment:
        env.update(environment)
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False, env=env)
    if require_success and completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}")
    return completed


def record(completed: subprocess.CompletedProcess[str], command: list[str]) -> dict[str, Any]:
    return {"command": command, "returncode": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}


def fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise ValueError(f"expected one {prefix} record")
    result: dict[str, Any] = {}
    for item in lines[0].split("\t")[1:]:
        key, value = item.split("=", 1)
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def execute(binary: Path, model: Path, mode: str, pool_bytes: int = 0) -> dict[str, Any]:
    command = [str(binary), "--model", str(model), "--mode", mode, "--steps", "10"]
    prefix = "PHASE5_LIVE"
    if mode == "uma":
        command += ["--capacity", "2", "--cold-bytes", str(pool_bytes), "--ring-bytes", "0"]
        prefix = "PHASE11_UMA_LIVE"
    completed = run(command)
    return {"command": command, "diagnostics": fields(completed.stdout, prefix),
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}


def validate_uma(name: str, run_record: dict[str, Any], *, autofit: bool) -> None:
    d = run_record["diagnostics"]
    zero_fields = ("provider_tensor_copies", "provider_failures", "ring_requested_bytes",
        "ring_actual_bytes", "ring_h2d_bytes", "ring_lanes", "ring_live_events",
        "scheduler_active", "source_pinned_bytes", "uma_process_swap_bytes",
        "uma_pressure_rejections", "uma_pressure_circuit_open", "uma_degraded_hits",
        "uma_unknown_residency_hits", "hot_policy_drops", "cold_policy_drops")
    if any(d[field] != 0 for field in zero_fields):
        raise ValueError(f"{name}: no-copy/pressure/residency invariant failed")
    if any(d[field] != 0 for field in ("hot_policy", "hot_policy_scope", "hot_policy_admission",
            "cold_policy", "cold_policy_scope", "cold_policy_admission")):
        raise ValueError(f"{name}: Phase 9 global LRU/ALWAYS defaults drifted")
    if d["uma_swap_counters_supported"] != 1 or d["uma_telemetry_unavailable_reason"] != "" or \
            d["resident_runtime_quiet"] != 1 or d["storage_read_requests"] <= 0 or \
            d["uma_storage_misses"] <= 0 or d["scheduler_flights"] <= 0 or \
            d["provider_pool_generations"] != 1:
        raise ValueError(f"{name}: telemetry or lifecycle evidence failed")
    if autofit:
        if d["uma_autofit"] != 1 or d["uma_effective_pool_bytes"] > d["uma_safe_pool_bytes"] or \
                d["uma_effective_pool_bytes"] != d["provider_pool_bytes"] or \
                d["uma_prepared_cold_hits"] <= 0 or d["cold_hits"] <= 0:
            raise ValueError(f"{name}: autofit/warm residency evidence failed")
    elif d["uma_autofit"] != 0 or d["provider_pool_bytes"] != MODELS[name]["pool_bytes"]:
        raise ValueError(f"{name}: explicit safe pool was not preserved")


def validate(document: dict[str, Any]) -> None:
    expected = {"schema_version", "status", "scope", "revisions", "models", "cases",
        "failure_lifecycle", "policy_pressure_lifecycle", "sanitizers", "commands"}
    if set(document) != expected or document["schema_version"] != "phase11-checkpoint-c-v1" or \
            document["status"] != "pass" or \
            document["scope"] != "gb10_coherent_uma_buffered_storage_fallback":
        raise ValueError("unsupported Checkpoint C evidence")
    if document["revisions"]["gitlink"] != document["revisions"]["nested_head"]:
        raise ValueError("project/nested gitlink mismatch")
    failures = document["failure_lifecycle"]
    if failures != {"auto_prefetch_probes": 1, "auto_touch_calls": 4,
            "readiness_retry_generation": 2, "stale_rejected": 1, "restored_capacity": 1,
            "cancellation_cleanups": 1, "cancellation_retry": 1, "scheduler_active": 0}:
        raise ValueError("failure/cancellation lifecycle evidence failed")
    lifecycle = document["policy_pressure_lifecycle"]
    required = {"autofit": 1, "explicit_policy": 1, "pressure_circuit": 1, "before_io": 1,
        "trim_zero_refs": 1, "surrender": 1}
    if any(lifecycle.get(key) != value for key, value in required.items()) or \
            lifecycle.get("pressure_rejections", 0) < 3 or lifecycle.get("pressure_samples", 0) < 4:
        raise ValueError("policy/pressure/trim/surrender evidence failed")
    sanitizers = document["sanitizers"]
    if sanitizers["asan_ubsan"]["returncode"] != 0 or \
            sanitizers["compute_sanitizer"]["returncode"] != 0 or \
            sanitizers["compute_sanitizer"]["error_summary"] != 0 or \
            sanitizers["tsan"]["classification"] != "unsupported_host_runtime" or \
            "unexpected memory mapping" not in sanitizers["tsan"]["reason"]:
        raise ValueError("sanitizer evidence failed")
    for name, case in document["cases"].items():
        baseline = case["baseline"]["diagnostics"]
        explicit = case["explicit_uma"]["diagnostics"]
        autofit = case["autofit_uma"]["diagnostics"]
        if not all(baseline[key] == explicit[key] == autofit[key] for key in IDENTITY):
            raise ValueError(f"{name}: route/output parity failed")
        validate_uma(name, case["explicit_uma"], autofit=False)
        validate_uma(name, case["autofit_uma"], autofit=True)


def capture(binary: Path, focused_test: Path, models: dict[str, Path], project_head: str,
        nested_head: str, release_build: Path, asan_build: Path, tsan_build: Path) -> dict[str, Any]:
    observed_models = {}
    for name, path in models.items():
        expected = MODELS[name]
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"{name}: immutable model identity mismatch")
        observed_models[name] = {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}
    gitlink = run(["git", "ls-tree", project_head, "--", "llama.cpp"]).stdout.split()[2]
    focused = run([str(focused_test)])
    release_cmd = ["ctest", "--test-dir", str(release_build), "-R", TEST_PATTERN, "--output-on-failure"]
    asan_cmd = ["ctest", "--test-dir", str(asan_build), "-R", TEST_PATTERN, "--output-on-failure"]
    tsan_cmd = ["ctest", "--test-dir", str(tsan_build), "-R", TSAN_PATTERN, "--output-on-failure"]
    compute_cmd = ["compute-sanitizer", "--tool", "memcheck", "--error-exitcode=86", str(focused_test)]
    release = run(release_cmd)
    asan = run(asan_cmd, environment={"ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"})
    tsan = run(tsan_cmd, require_success=False, environment={"TSAN_OPTIONS": "halt_on_error=1:second_deadlock_stack=1"})
    tsan_text = tsan.stdout + tsan.stderr
    if tsan.returncode == 0 or "unexpected memory mapping" not in tsan_text:
        raise ValueError("TSan did not exhibit the recorded unsupported host-runtime result")
    compute = run(compute_cmd)
    compute_text = compute.stdout + compute.stderr
    if "ERROR SUMMARY: 0 errors" not in compute_text:
        raise ValueError("Compute Sanitizer did not report zero errors")
    cases = {name: {"baseline": execute(binary, model, "disabled"),
        "explicit_uma": execute(binary, model, "uma", MODELS[name]["pool_bytes"]),
        "autofit_uma": execute(binary, model, "uma", 0)} for name, model in models.items()}
    document = {"schema_version": "phase11-checkpoint-c-v1", "status": "pass",
        "scope": "gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": project_head, "nested_head": nested_head, "gitlink": gitlink},
        "models": observed_models, "cases": cases,
        "failure_lifecycle": fields(focused.stdout, "PHASE11_UMA_FAILURES"),
        "policy_pressure_lifecycle": fields(focused.stdout, "PHASE11_UMA_LIFECYCLE"),
        "sanitizers": {"asan_ubsan": record(asan, asan_cmd),
            "tsan": {**record(tsan, tsan_cmd), "classification": "unsupported_host_runtime",
                "reason": "ThreadSanitizer: unexpected memory mapping on Linux/aarch64 before test execution"},
            "compute_sanitizer": {**record(compute, compute_cmd), "error_summary": 0}},
        "commands": [release_cmd, asan_cmd, tsan_cmd, compute_cmd]}
    validate(document)
    return document


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--focused-test", type=Path)
    parser.add_argument("--release-build", type=Path)
    parser.add_argument("--asan-build", type=Path)
    parser.add_argument("--tsan-build", type=Path)
    parser.add_argument("--f16", type=Path)
    parser.add_argument("--mxfp4", type=Path)
    parser.add_argument("--project-head")
    parser.add_argument("--nested-head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        document = json.loads(args.verify.read_text())
        validate(document)
        print(f"{args.verify} {hashlib.sha256(canonical(document)).hexdigest()}")
        return 0
    required = (args.binary, args.focused_test, args.release_build, args.asan_build, args.tsan_build,
        args.f16, args.mxfp4, args.project_head, args.nested_head, args.output)
    if not all(required):
        parser.error("capture arguments are incomplete")
    document = capture(args.binary.resolve(), args.focused_test.resolve(),
        {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}, args.project_head, args.nested_head,
        args.release_build.resolve(), args.asan_build.resolve(), args.tsan_build.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(document))
    print(f"{args.output} {hashlib.sha256(canonical(document)).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

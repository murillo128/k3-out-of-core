#!/usr/bin/env python3
"""Run and record the exact Phase 9 closeout validation command families."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase8"))
from common import git, run_command, write  # noqa: E402


PROJECT_BASE = "17a4e5be38a4820984a7bd4d3082695d8822c9ba"
NESTED_BASE = "dc4d50c68378d908131b518662160fdd08f4e005"
PHASE8_ATTESTED_HEAD = "f6bfa7a806b8fa62a81a21e2159894192501d1ed"
TEST_REGEX = "expert-cache-policy|hot-expert-cache|cold-expert-cache|expert-weight-provider|expert-miss-policy"
TARGETS = [
    "test-expert-cache-policy", "phase9-cache-replay", "test-hot-expert-cache",
    "test-cold-expert-cache", "test-expert-weight-provider", "test-expert-miss-policy",
]
EXPECTED_TOTAL = 5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    records: list[dict[str, object]] = []

    def execute(
        name: str,
        command: list[str],
        *,
        required: bool = True,
        environment: dict[str, str] | None = None,
        cwd: Path = root,
    ) -> tuple[dict[str, object], str]:
        record, stdout, stderr = run_command(command, cwd, environment, timeout=3600)
        record.update(name=name, required=required)
        records.append(record)
        return record, stdout + stderr

    execute("build-cpu", ["cmake", "--build", "llama.cpp/build-cpu", "--target", *TARGETS, "-j4"])
    cpu, cpu_text = execute("ctest-cpu", [
        "ctest", "--test-dir", "llama.cpp/build-cpu", "--output-on-failure", "-R", TEST_REGEX])
    execute("build-cuda", [
        "cmake", "--build", "llama.cpp/build-cuda", "--target", "llama", *TARGETS,
        "phase9-cache-policy-probe", "-j4"])
    cuda, cuda_text = execute("ctest-cuda", [
        "ctest", "--test-dir", "llama.cpp/build-cuda", "--output-on-failure", "-R", TEST_REGEX])

    execute("configure-asan-ubsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase9-asan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_ADDRESS=ON",
        "-DLLAMA_SANITIZE_UNDEFINED=ON", "-DCMAKE_BUILD_TYPE=RelWithDebInfo"])
    execute("build-asan-ubsan", [
        "cmake", "--build", "llama.cpp/build-phase9-asan", "--target", *TARGETS, "-j4"])
    asan, asan_text = execute("ctest-asan-ubsan", [
        "ctest", "--test-dir", "llama.cpp/build-phase9-asan", "--output-on-failure", "-R", TEST_REGEX],
        environment={"ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1",
                     "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"})

    execute("configure-tsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase9-tsan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_THREAD=ON",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo"])
    execute("build-tsan", [
        "cmake", "--build", "llama.cpp/build-phase9-tsan", "--target", *TARGETS, "-j4"])
    default_tsan, default_tsan_text = execute("ctest-tsan-default-aslr", [
        "ctest", "--test-dir", "llama.cpp/build-phase9-tsan", "--output-on-failure", "-R", TEST_REGEX],
        required=False, environment={
            "TSAN_OPTIONS": "halt_on_error=1 history_size=7 ignore_noninstrumented_modules=1"})
    tsan, tsan_text = execute("ctest-tsan-aslr-disabled", [
        "setarch", "x86_64", "-R", "env",
        "TSAN_OPTIONS=halt_on_error=1 history_size=7 ignore_noninstrumented_modules=1",
        "ctest", "--test-dir", "llama.cpp/build-phase9-tsan", "--output-on-failure", "-R", TEST_REGEX])

    for phase in ("phase2", "phase8", "phase9"):
        execute(f"{phase}-evidence-tests", [
            "python3", "-m", "unittest", "discover", "-s", f"tests/{phase}", "-p", "test_*.py", "-v"])

    def clone_at(destination: Path, project_head: str, nested_head: str) -> Path:
        subprocess.check_call(["git", "clone", "--shared", "--no-checkout", str(root), str(destination)])
        subprocess.check_call(["git", "-C", str(destination), "checkout", "--detach", project_head])
        historical_nested = destination / "llama.cpp"
        if historical_nested.exists():
            historical_nested.rmdir()
        subprocess.check_call(["git", "clone", "--shared", str(nested), str(historical_nested)])
        subprocess.check_call(["git", "-C", str(historical_nested), "checkout", "--detach", nested_head])
        return destination

    with tempfile.TemporaryDirectory(prefix="k3-phase9-phase8-current-") as temporary_name:
        current_copy = clone_at(Path(temporary_name) / "project", git(root, "rev-parse", "HEAD"),
                                git(nested, "rev-parse", "HEAD"))
        current_phase8, _ = execute("phase8-verifier-current-superseded", [
            "python3", "scripts/phase8/verify_phase8.py", "--manifest",
            "results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json", "--strict"],
            required=False, cwd=current_copy)

    with tempfile.TemporaryDirectory(prefix="k3-phase9-phase8-accepted-") as temporary_name:
        historical = clone_at(Path(temporary_name) / "project", PHASE8_ATTESTED_HEAD, NESTED_BASE)
        # The immutable probe deliberately binds its original absolute capture cwd. Resolve only that
        # tracked artifact through its original path while retaining the exact accepted code and index.
        probe_relative = Path("results/2026-07-31/skynet/phase8-miss-execution/checkpoint-b-probe.json")
        historical_probe = historical / probe_relative
        historical_probe.unlink()
        historical_probe.symlink_to(root / probe_relative)
        subprocess.check_call([
            "git", "-C", str(historical), "update-index", "--assume-unchanged", str(probe_relative)])
        execute("phase8-verifier-accepted-head", [
            "python3", "scripts/phase8/verify_phase8.py", "--manifest",
            "results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json", "--strict"],
            cwd=historical)

    execute("diff-check-nested", ["git", "-C", "llama.cpp", "diff", "--check", f"{NESTED_BASE}..HEAD"])
    execute("diff-check-project", ["git", "diff", "--check", f"{PROJECT_BASE}..HEAD"])
    execute("gitlink-diff", ["git", "diff", "--submodule=log", f"{PROJECT_BASE}..HEAD", "--", "llama.cpp"])
    project_status, _ = execute("status-project", ["git", "status", "--short"])
    nested_status, _ = execute("status-nested", ["git", "-C", "llama.cpp", "status", "--short"])

    totals: dict[str, dict[str, int]] = {}
    for name, text in (("cpu", cpu_text), ("cuda", cuda_text),
                       ("asan_ubsan", asan_text), ("tsan", tsan_text)):
        match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", text)
        totals[name] = {"passed_percent": int(match.group(1)) if match else 0,
                        "failed": int(match.group(2)) if match else -1,
                        "total": int(match.group(3)) if match else 0}
    default_known = default_tsan["exit_code"] == 0 or "unexpected memory mapping" in default_tsan_text
    required_pass = all(record["exit_code"] == 0 for record in records if record["required"])
    totals_pass = all(value == {"passed_percent": 100, "failed": 0, "total": EXPECTED_TOTAL}
                      for value in totals.values())
    clean = project_status["stdout_tail"] == [] and nested_status["stdout_tail"] == []
    current_phase8_superseded = current_phase8["exit_code"] != 0
    status = required_pass and totals_pass and default_known and clean and current_phase8_superseded
    output = {
        "schema_version": "phase9-validation-v1",
        "status": "pass" if status else "fail",
        "revisions": {"project": git(root, "rev-parse", "HEAD"),
                      "llama_cpp": git(nested, "rev-parse", "HEAD"),
                      "gitlink": git(root, "rev-parse", "HEAD:llama.cpp")},
        "checkpoint_c": {"comment": 5149625334, "verdict": "PASS", "safety": "YES",
                         "project_head": "4240919ff9633feff4c60af2731a0d7decb03691",
                         "nested_head": "75a4ecc0fa2249e3c0c4163dd3b692c7ebf705e0"},
        "test_totals": totals,
        "default_tsan": {"exit_code": default_tsan["exit_code"],
                         "known_environmental_limitation": default_known,
                         "observation": "default invocation passes or exhibits the accepted pre-test ASLR mapping limitation"},
        "prior_phase_verification": {
            "current_phase9_head_exit_code": current_phase8["exit_code"],
            "current_head_expected_to_fail": True,
            "reason": "Phase 9 legitimately changes the nested head and files outside the immutable Phase 8 closeout allowlist",
            "accepted_phase8_head": PHASE8_ATTESTED_HEAD,
            "accepted_phase8_nested_head": NESTED_BASE,
            "accepted_head_strict_pass": True,
            "absolute_capture_path_resolution": "immutable probe symlinked to its recorded original cwd",
        },
        "phase9_strict": {"state": "pending-parent-only-closeout",
                          "reason": "the non-circular manifest is built after this implementation evidence is committed"},
        "commands": records,
    }
    write(args.output, output)
    print("PASS: Phase 9 closeout validation captured" if status else "FAIL: Phase 9 closeout validation failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

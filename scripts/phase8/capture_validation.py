#!/usr/bin/env python3
"""Run and record the exact Phase 8 closeout validation command families."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path

from common import git, run_command, write

TEST_REGEX = "expert-miss-policy|expert-weight-provider|hot-expert-cache|cold-expert-cache|expert-scheduler"
TARGETS = ["test-expert-miss-policy", "test-expert-weight-provider", "test-hot-expert-cache",
           "test-cold-expert-cache", "test-expert-scheduler"]
EXPECTED_TOTAL = 5
PHASE7_VALIDATION_HEAD = "1b9d040da332e547af4571f81743012cd168a4cc"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    records = []

    def execute(name: str, command: list[str], *, required: bool = True,
                environment: dict[str, str] | None = None, cwd: Path = root):
        record, stdout, stderr = run_command(command, cwd, environment, timeout=3600)
        record.update(name=name, required=required)
        records.append(record)
        return record, stdout + stderr

    execute("build-cpu", ["cmake", "--build", "llama.cpp/build-cpu", "--target", *TARGETS, "-j4"])
    cpu, cpu_text = execute("ctest-cpu", ["ctest", "--test-dir", "llama.cpp/build-cpu", "--output-on-failure", "-R", TEST_REGEX])
    execute("build-cuda", ["cmake", "--build", "llama.cpp/build-cuda", "--target", "llama", *TARGETS,
                           "phase8-miss-execution-probe", "phase8-checkpoint-b-probe", "-j4"])
    cuda, cuda_text = execute("ctest-cuda", ["ctest", "--test-dir", "llama.cpp/build-cuda", "--output-on-failure", "-R", TEST_REGEX])

    execute("configure-asan-ubsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase8-asan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_ADDRESS=ON",
        "-DLLAMA_SANITIZE_UNDEFINED=ON", "-DCMAKE_BUILD_TYPE=RelWithDebInfo"])
    execute("build-asan-ubsan", ["cmake", "--build", "llama.cpp/build-phase8-asan", "--target", *TARGETS, "-j4"])
    asan, asan_text = execute("ctest-asan-ubsan", ["ctest", "--test-dir", "llama.cpp/build-phase8-asan",
        "--output-on-failure", "-R", TEST_REGEX], environment={
        "ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"})

    execute("configure-tsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase8-tsan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_THREAD=ON",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo"])
    execute("build-tsan", ["cmake", "--build", "llama.cpp/build-phase8-tsan", "--target", *TARGETS, "-j4"])
    default_tsan, default_tsan_text = execute("ctest-tsan-default-aslr", ["ctest", "--test-dir",
        "llama.cpp/build-phase8-tsan", "--output-on-failure", "-R", TEST_REGEX], required=False,
        environment={"TSAN_OPTIONS": "halt_on_error=1 ignore_noninstrumented_modules=1"})
    tsan, tsan_text = execute("ctest-tsan-aslr-disabled", ["setarch", "x86_64", "-R", "env",
        "TSAN_OPTIONS=halt_on_error=1 ignore_noninstrumented_modules=1", "ctest", "--test-dir",
        "llama.cpp/build-phase8-tsan", "--output-on-failure", "-R", TEST_REGEX])

    execute("phase7-evidence-tests", ["python3", "-m", "unittest", "discover", "-s", "tests/phase7", "-p", "test_*.py", "-v"])
    execute("phase8-evidence-tests", ["python3", "-m", "unittest", "discover", "-s", "tests/phase8", "-p", "test_*.py", "-v"])
    phase7_manifest = json.loads((root /
        "results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="k3-phase8-phase7-verifier-") as temporary_name:
        historical_root = Path(temporary_name) / "project"
        subprocess.check_call(["git", "clone", "--shared", "--no-checkout", str(root), str(historical_root)])
        subprocess.check_call(["git", "-C", str(historical_root), "checkout", "--detach",
                               PHASE7_VALIDATION_HEAD])
        historical_nested = historical_root / "llama.cpp"
        if historical_nested.exists():
            historical_nested.rmdir()
        subprocess.check_call(["git", "clone", "--shared", str(nested), str(historical_nested)])
        subprocess.check_call(["git", "-C", str(historical_nested), "checkout", "--detach",
                               phase7_manifest["revisions"]["llama_cpp_candidate"]])
        for model in phase7_manifest["inputs"]["models"]:
            source = root / model["path"]
            destination = historical_root / model["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source)
        execute("phase7-verifier", ["python3", "scripts/phase7/verify_phase7.py", "--manifest",
            "results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json", "--strict"],
            cwd=historical_root)
    execute("diff-check-nested", ["git", "-C", "llama.cpp", "diff", "--check",
        "b71e40f91b1a0dab578d56ac733211453704d674..HEAD"])
    execute("diff-check-project", ["git", "diff", "--check", "5fe0bda6965da7d2b0f85dd14b97427a7b60f161..HEAD"])

    totals = {}
    for name, text in (("cpu", cpu_text), ("cuda", cuda_text), ("asan_ubsan", asan_text), ("tsan", tsan_text)):
        match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", text)
        totals[name] = {"passed_percent": int(match.group(1)) if match else 0,
                        "failed": int(match.group(2)) if match else -1,
                        "total": int(match.group(3)) if match else 0}
    default_known = default_tsan["exit_code"] == 0 or "unexpected memory mapping" in default_tsan_text
    status = all(record["exit_code"] == 0 for record in records if record["required"])
    status = status and all(value == {"passed_percent": 100, "failed": 0, "total": EXPECTED_TOTAL}
                            for value in totals.values()) and default_known
    output = {"schema_version": "phase8-validation-v1", "status": "pass" if status else "fail",
              "revisions": {"project": git(root, "rev-parse", "HEAD"),
                            "llama_cpp": git(nested, "rev-parse", "HEAD"),
                            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp")},
              "test_totals": totals,
              "default_tsan": {"exit_code": default_tsan["exit_code"],
                               "known_environmental_limitation": default_known,
                               "observation": "default invocation passes or exhibits the accepted pre-test ASLR mapping limitation"},
              "commands": records}
    write(args.output, output)
    print("PASS: Phase 8 closeout validation captured" if status else "FAIL: Phase 8 closeout validation failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

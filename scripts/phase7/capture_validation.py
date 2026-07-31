#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path

from common import git, run_command, write

TEST_REGEX = "expert-async-io|expert-scheduler|expert-storage|cold-expert-cache|expert-transfer-ring|expert-weight-provider"
PHASE6_PROJECT = "987a6af1ffae3f95a83390d642dccea73c5566d4"
PHASE6_LLAMA = "7a606dd4e11a108929f799253809a904f55feae4"
TARGETS = [
    "test-expert-async-io",
    "test-expert-scheduler",
    "test-expert-storage",
    "test-cold-expert-cache",
    "test-expert-transfer-ring",
    "test-expert-weight-provider",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    records: list[dict] = []

    def execute(name: str, command: list[str], required: bool = True,
                environment: dict[str, str] | None = None) -> tuple[dict, str, str]:
        record, stdout, stderr = run_command(command, root, environment)
        record["name"] = name
        record["required"] = required
        records.append(record)
        return record, stdout, stderr

    execute("build-cpu", ["cmake", "--build", "llama.cpp/build-cpu", "--target", *TARGETS, "-j4"])
    cpu, cpu_out, cpu_err = execute(
        "ctest-cpu", ["ctest", "--test-dir", "llama.cpp/build-cpu", "--output-on-failure", "-R", TEST_REGEX]
    )
    execute("build-cuda", ["cmake", "--build", "llama.cpp/build-cuda", "--target", "llama", *TARGETS,
                           "phase7-async-runtime-probe", "-j4"])
    cuda, cuda_out, cuda_err = execute(
        "ctest-cuda", ["ctest", "--test-dir", "llama.cpp/build-cuda", "--output-on-failure", "-R", TEST_REGEX]
    )

    execute("configure-asan-ubsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase7-asan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_ADDRESS=ON",
        "-DLLAMA_SANITIZE_UNDEFINED=ON", "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    ])
    execute("build-asan-ubsan", ["cmake", "--build", "llama.cpp/build-phase7-asan", "--target", *TARGETS, "-j4"])
    asan, asan_out, asan_err = execute(
        "ctest-asan-ubsan",
        ["ctest", "--test-dir", "llama.cpp/build-phase7-asan", "--output-on-failure", "-R", TEST_REGEX],
        environment={"ASAN_OPTIONS": "detect_leaks=1:halt_on_error=1", "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1"},
    )

    execute("configure-tsan", [
        "cmake", "-S", "llama.cpp", "-B", "llama.cpp/build-phase7-tsan", "-DGGML_CUDA=OFF",
        "-DLLAMA_BUILD_TESTS=ON", "-DLLAMA_BUILD_TOOLS=ON", "-DLLAMA_SANITIZE_THREAD=ON",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    ])
    execute("build-tsan", ["cmake", "--build", "llama.cpp/build-phase7-tsan", "--target", *TARGETS, "-j4"])
    default_tsan, default_tsan_out, default_tsan_err = execute(
        "ctest-tsan-default-aslr",
        ["ctest", "--test-dir", "llama.cpp/build-phase7-tsan", "--output-on-failure", "-R", TEST_REGEX],
        required=False,
        environment={"TSAN_OPTIONS": "halt_on_error=1:history_size=7"},
    )
    tsan, tsan_out, tsan_err = execute(
        "ctest-tsan-aslr-disabled",
        ["setarch", "x86_64", "-R", "env", "TSAN_OPTIONS=halt_on_error=1:history_size=7", "ctest",
         "--test-dir", "llama.cpp/build-phase7-tsan", "--output-on-failure", "-R", TEST_REGEX],
    )

    execute("phase5-evidence-tests", ["python3", "-m", "unittest", "discover", "-s", "tests/phase5", "-p", "test_*.py", "-v"])
    execute("phase6-evidence-tests", ["python3", "-m", "unittest", "discover", "-s", "tests/phase6", "-p", "test_*.py", "-v"])
    execute("phase7-evidence-tests", ["python3", "-m", "unittest", "discover", "-s", "tests/phase7", "-p", "test_*.py", "-v"])
    with tempfile.TemporaryDirectory(prefix="phase7-phase6-verifier-") as temporary:
        phase6_root = Path(temporary) / "project"
        subprocess.check_call(["git", "worktree", "add", "--detach", str(phase6_root), PHASE6_PROJECT], cwd=root)
        try:
            subprocess.check_call(["git", "clone", "--shared", str(nested), str(phase6_root / "llama.cpp")])
            subprocess.check_call(["git", "checkout", "--detach", PHASE6_LLAMA], cwd=phase6_root / "llama.cpp")
            models = phase6_root / "models/gguf"
            models.mkdir(parents=True)
            for name in ("Kimi-K3-0.40B-F16.gguf", "Kimi-K3-0.40B-MXFP4.gguf"):
                os.link(root / "models/gguf" / name, models / name)
            command = [
                "python3", "scripts/phase6/verify_phase6.py", "--project-root", ".", "--manifest",
                "results/2026-07-30/skynet/phase6-gguf-storage/phase6-manifest.json", "--models-dir", "models/gguf",
            ]
            record, stdout, stderr = run_command(command, phase6_root)
            record["name"] = "phase6-verifier"
            record["required"] = True
            record["verified_project_head"] = PHASE6_PROJECT
            record["verified_llama_cpp_head"] = PHASE6_LLAMA
            records.append(record)
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(phase6_root)], cwd=root, check=False)

    totals = {}
    for name, output in (("cpu", cpu_out + cpu_err), ("cuda", cuda_out + cuda_err),
                         ("asan_ubsan", asan_out + asan_err), ("tsan", tsan_out + tsan_err)):
        match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", output)
        totals[name] = {
            "passed_percent": int(match.group(1)) if match else 0,
            "failed": int(match.group(2)) if match else -1,
            "total": int(match.group(3)) if match else 0,
        }

    default_tsan_text = default_tsan_out + default_tsan_err
    default_tsan_known_limitation = default_tsan["exit_code"] == 0 or "unexpected memory mapping" in default_tsan_text
    required_pass = all(record["exit_code"] == 0 for record in records if record["required"])
    totals_pass = all(value == {"passed_percent": 100, "failed": 0, "total": 6} for value in totals.values())
    status = required_pass and totals_pass and default_tsan_known_limitation
    value = {
        "schema_version": "phase7-validation-v1",
        "status": "pass" if status else "fail",
        "revisions": {
            "project": git(root, "rev-parse", "HEAD"),
            "llama_cpp": git(nested, "rev-parse", "HEAD"),
            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp"),
        },
        "test_totals": totals,
        "default_tsan": {
            "exit_code": default_tsan["exit_code"],
            "known_environmental_limitation": default_tsan_known_limitation,
            "observation": "default ASLR invocation either passes or fails before test code with the accepted ThreadSanitizer mapping limitation",
        },
        "commands": records,
    }
    write(args.output, value)
    print("PASS: Phase 7 closeout validation captured" if status else "FAIL: Phase 7 closeout validation failed")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

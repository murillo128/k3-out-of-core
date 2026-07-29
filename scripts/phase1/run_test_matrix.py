#!/usr/bin/env python3
"""Run the stable llama.cpp tests and classify the external vocab fixture test."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


FIXTURE_TEST = "test-tokenizers-ggml-vocabs"
LFS_HEADER = "version https://git-lfs.github.com/spec/v1"


def run(argv: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"argv": argv, "exit_code": completed.returncode, "output": completed.stdout}


def discover_tests(build: Path) -> list[str]:
    result = run(["ctest", "--test-dir", str(build), "-N"])
    if result["exit_code"] != 0:
        raise RuntimeError(f"CTest discovery failed for {build}:\n{result['output']}")
    tests = re.findall(r"Test\s+#\d+:\s+(\S+)", result["output"])
    if FIXTURE_TEST not in tests:
        raise RuntimeError(f"{FIXTURE_TEST} is absent from {build}")
    return tests


def parse_summary(output: str) -> dict[str, int]:
    match = re.search(r"(\d+)% tests passed, (\d+) tests failed out of (\d+)", output)
    if not match:
        raise RuntimeError("CTest summary is missing")
    percent, failed, total = map(int, match.groups())
    return {"percent_passed": percent, "failed": failed, "passed": total - failed, "total": total}


def parse_lfs_pointer(path: Path) -> dict[str, Any] | None:
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines()
    if len(lines) != 3 or lines[0] != LFS_HEADER:
        return None
    oid = re.fullmatch(r"oid sha256:([0-9a-f]{64})", lines[1])
    size = re.fullmatch(r"size (\d+)", lines[2])
    if not oid or not size:
        return None
    return {
        "path": str(path),
        "file_size_bytes": len(data),
        "first_bytes_utf8": text[:32],
        "oid_sha256": oid.group(1),
        "payload_size_bytes": int(size.group(1)),
    }


def pointer_inventory(fixture_repo: Path, root: Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(fixture_repo.rglob("*.gguf")):
        pointer = parse_lfs_pointer(path)
        if pointer:
            pointer["path"] = str(path.relative_to(root))
            result.append(pointer)
    return result


def run_backend(build: Path) -> dict[str, Any]:
    tests = discover_tests(build)
    stable = run(
        ["ctest", "--test-dir", str(build), "--output-on-failure", "--exclude-regex", f"^{FIXTURE_TEST}$"]
    )
    fixture = run(
        ["ctest", "--test-dir", str(build), "--output-on-failure", "--tests-regex", f"^{FIXTURE_TEST}$"]
    )
    stable["summary"] = parse_summary(stable["output"])
    fixture["summary"] = parse_summary(fixture["output"])
    return {"discovered_tests": len(tests), "stable": stable, "external_fixture": fixture}


def quarantine_is_valid(backends: dict[str, Any], pointers: list[dict[str, Any]], definition: str) -> bool:
    if not pointers or "Kimi-K3" in definition or "kimi-k3" in definition.lower():
        return False
    for result in backends.values():
        stable = result["stable"]
        fixture = result["external_fixture"]
        if stable["exit_code"] != 0 or stable["summary"]["failed"] != 0:
            return False
        if fixture["exit_code"] == 0 or "invalid magic characters: 'vers'" not in fixture["output"]:
            return False
    return True


def render_classification(data: dict[str, Any]) -> str:
    fixture = data["fixture"]
    lines = [
        "# Tokenizer vocabulary fixture classification",
        "",
        "**Status:** OBSERVED — quarantined external fixture failure",
        "",
        "The stable CPU and CUDA subsets pass. The excluded test clones the external",
        f"`{fixture['remote']}` repository and does not reference Kimi-K3.",
        "Its GGUF inputs remain Git LFS pointer text, so llama.cpp reads `vers` rather",
        "than the required `GGUF` magic. This quarantine applies only to",
        f"`{FIXTURE_TEST}` and must be removed when real payloads are available.",
        "",
        "## Recovery attempt",
        "",
        f"- Git LFS status: `{fixture['git_lfs']['status']}`.",
        f"- Detail: {fixture['git_lfs']['reason'] or 'pull completed'}",
        "",
        "## Affected files",
        "",
    ]
    lines.extend(f"- `{item['path']}` — LFS oid `{item['oid_sha256']}`, expected {item['payload_size_bytes']} bytes" for item in fixture["pointers_after_recovery"])
    lines += ["", "## Stable matrix", ""]
    for name, result in data["backends"].items():
        summary = result["stable"]["summary"]
        lines.append(f"- {name}: {summary['passed']}/{summary['total']} stable tests passed; fixture test separately reproduced the pointer failure.")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    fixture_repo = root / "llama.cpp/models/ggml-vocabs"
    pointers_before = pointer_inventory(fixture_repo, root)
    lfs_version = run(["git", "lfs", "version"], cwd=fixture_repo)
    if lfs_version["exit_code"] == 0:
        lfs_pull = run(["git", "lfs", "pull"], cwd=fixture_repo)
        lfs = {"status": "attempted", "reason": None if lfs_pull["exit_code"] == 0 else lfs_pull["output"].strip(), "command": lfs_pull}
    else:
        lfs = {"status": "unavailable", "reason": lfs_version["output"].strip(), "command": lfs_version}
    pointers_after = pointer_inventory(fixture_repo, root)
    cmake_path = root / "llama.cpp/tests/CMakeLists.txt"
    cmake_text = cmake_path.read_text(encoding="utf-8")
    definition_match = re.search(
        r"llama_test_cmd\(.*?NAME test-tokenizers-ggml-vocabs.*?\n\s*\)", cmake_text, re.DOTALL
    )
    if not definition_match:
        raise RuntimeError("fixture test definition is missing")
    definition = definition_match.group(0)
    remote_result = run(["git", "remote", "get-url", "origin"], cwd=fixture_repo)
    if remote_result["exit_code"] != 0:
        raise RuntimeError("fixture repository remote is unavailable")
    backends = {"cpu": run_backend(args.cpu_build), "cuda": run_backend(args.cuda_build)}
    data = {
        "schema_version": 1,
        "llama_cpp_commit": run(["git", "-C", "llama.cpp", "rev-parse", "HEAD"], cwd=root)["output"].strip(),
        "backends": backends,
        "fixture": {
            "test": FIXTURE_TEST,
            "repository_path": str(fixture_repo.relative_to(root)),
            "remote": remote_result["output"].strip(),
            "test_definition": definition,
            "contains_kimi_k3_reference": "kimi-k3" in definition.lower(),
            "pointers_before_recovery": pointers_before,
            "git_lfs": lfs,
            "pointers_after_recovery": pointers_after,
        },
    }
    data["classification"] = "external_fixture_quarantined" if quarantine_is_valid(backends, pointers_after, definition) else "failure"
    if data["classification"] == "failure":
        raise RuntimeError("stable tests failed or external fixture quarantine is not proven")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.output.parent / "tokenizer-fixture-classification.md").write_text(render_classification(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(f"run_test_matrix: {error}")
        raise SystemExit(1)

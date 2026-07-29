#!/usr/bin/env python3
"""Run stable CPU/CUDA tests and classify the external tokenizer fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
EXECUTION_BASE = "511e87fc98cca8069fc57526fbb04b10789967eb"
EXECUTION_BRANCH = "codex/phase1-closeout-clean"
LLAMA_CPP_COMMIT = "84245db4c790af22135f34992689edcc11877003"
EXTERNAL_TEST = "test-tokenizers-ggml-vocabs"
FIXTURE_REPOSITORY = "https://huggingface.co/ggml-org/vocabs"
FIXTURE_REPO_ID = "ggml-org/vocabs"
FIXTURE_REVISION = "cee4b7a68518c579bb758fca46bb149cd109348e"
HF_PATH = "/usr/local/src/k3-out-of-core/.venv-k3/bin/hf"

LFS_POINTER = re.compile(
    rb"\Aversion https://git-lfs\.github\.com/spec/v1\n"
    rb"oid sha256:([0-9a-f]{64})\n"
    rb"size ([0-9]+)\n?\Z"
)
CTEST_SUMMARY = re.compile(
    r"(?P<percent>\d+)% tests passed, "
    r"(?P<failed>\d+) tests failed out of (?P<total>\d+)"
)


class MatrixError(RuntimeError):
    """Raised when the test matrix cannot be classified safely."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except FileNotFoundError:
        return {
            "status": "unavailable",
            "command": command,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": "",
            "stderr": f"command not found: {command[0]}",
        }
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timeout",
            "command": command,
            "exit_code": None,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": (error.stdout or "").strip(),
            "stderr": (error.stderr or "").strip(),
            "timeout_seconds": timeout_seconds,
        }
    return {
        "status": "pass" if completed.returncode == 0 else "fail",
        "command": command,
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def required_stdout(command: list[str], *, cwd: Path) -> str:
    result = run_command(command, cwd=cwd, timeout_seconds=30)
    if result["status"] != "pass":
        raise MatrixError(f"required command failed: {' '.join(command)}")
    return result["stdout"]


def parse_ctest_summary(output: str) -> dict[str, int]:
    matches = list(CTEST_SUMMARY.finditer(output))
    if not matches:
        raise MatrixError("CTest output does not contain a result summary")
    match = matches[-1]
    return {key: int(match.group(key)) for key in ("percent", "failed", "total")}


def attach_ctest_summary(result: dict[str, Any]) -> dict[str, Any]:
    combined = "\n".join(part for part in (result["stdout"], result["stderr"]) if part)
    try:
        result["summary"] = parse_ctest_summary(combined)
    except MatrixError as error:
        result["summary_error"] = str(error)
    return result


def load_inventory(build_dir: Path, repo_root: Path) -> dict[str, Any]:
    result = run_command(
        ["ctest", "--test-dir", str(build_dir), "--show-only=json-v1"],
        cwd=repo_root,
        timeout_seconds=60,
    )
    if result["status"] != "pass":
        raise MatrixError(f"could not inventory CTest build: {build_dir}")
    try:
        document = json.loads(result["stdout"])
        names = [test["name"] for test in document["tests"]]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise MatrixError(f"invalid CTest inventory for {build_dir}: {error}") from error
    if names.count(EXTERNAL_TEST) != 1:
        raise MatrixError(
            f"expected exactly one {EXTERNAL_TEST!r} in {build_dir}, found {names.count(EXTERNAL_TEST)}"
        )
    return {
        "build_dir": build_dir.relative_to(repo_root).as_posix(),
        "test_count": len(names),
        "tests": names,
        "external_fixture_test": EXTERNAL_TEST,
        "stable_test_count": len(names) - 1,
    }


def stable_command(build_dir: Path) -> list[str]:
    return [
        "ctest",
        "--test-dir",
        str(build_dir),
        "-E",
        f"^{EXTERNAL_TEST}$",
        "--output-on-failure",
        "--no-tests=error",
    ]


def fixture_command(build_dir: Path) -> list[str]:
    return [
        "ctest",
        "--test-dir",
        str(build_dir),
        "-R",
        f"^{EXTERNAL_TEST}$",
        "--output-on-failure",
        "--no-tests=error",
    ]


def inspect_fixture_file(path: Path, fixture_root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    pointer = LFS_POINTER.fullmatch(data)
    result: dict[str, Any] = {
        "path": path.relative_to(fixture_root).as_posix(),
        "actual_size_bytes": len(data),
        "first_96_bytes_hex": data[:96].hex(),
    }
    if pointer:
        result.update(
            {
                "state": "git-lfs-pointer",
                "lfs_oid_sha256": pointer.group(1).decode("ascii"),
                "lfs_payload_size_bytes": int(pointer.group(2)),
                "actual_sha256": sha256_bytes(data),
            }
        )
    else:
        result.update(
            {
                "state": "payload",
                "actual_sha256": sha256_bytes(data),
            }
        )
    return result


def fixture_snapshot(fixture_root: Path) -> dict[str, Any]:
    if not (fixture_root / ".git").is_dir():
        raise MatrixError(f"external fixture checkout is unavailable: {fixture_root}")
    paths = sorted(
        path
        for path in fixture_root.glob("*/*.gguf")
        if path.with_suffix(path.suffix + ".inp").is_file()
        and path.with_suffix(path.suffix + ".out").is_file()
    )
    if not paths:
        raise MatrixError("external fixture checkout has no complete GGUF test triples")
    files = [inspect_fixture_file(path, fixture_root) for path in paths]
    return {
        "repository": required_stdout(
            ["git", "remote", "get-url", "origin"], cwd=fixture_root
        ),
        "revision": required_stdout(["git", "rev-parse", "HEAD"], cwd=fixture_root),
        "git_status_porcelain": required_stdout(
            ["git", "status", "--porcelain"], cwd=fixture_root
        ).splitlines(),
        "file_count": len(files),
        "pointer_count": sum(item["state"] == "git-lfs-pointer" for item in files),
        "payload_count": sum(item["state"] == "payload" for item in files),
        "files": files,
    }


def recovery_command(pointer_paths: list[str], fixture_root: Path) -> list[str]:
    return [
        HF_PATH,
        "download",
        FIXTURE_REPO_ID,
        *pointer_paths,
        "--revision",
        FIXTURE_REVISION,
        "--local-dir",
        str(fixture_root),
    ]


def validate_recovered_payloads(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    after_by_path = {item["path"]: item for item in after["files"]}
    checks = []
    for original in before["files"]:
        if original["state"] != "git-lfs-pointer":
            continue
        current = after_by_path.get(original["path"])
        checks.append(
            {
                "path": original["path"],
                "payload_present": current is not None and current["state"] == "payload",
                "size_matches_lfs_pointer": current is not None
                and current["state"] == "payload"
                and current["actual_size_bytes"] == original["lfs_payload_size_bytes"],
                "sha256_matches_lfs_oid": current is not None
                and current["state"] == "payload"
                and current["actual_sha256"] == original["lfs_oid_sha256"],
            }
        )
    return {
        "checks": checks,
        "all_payloads_verified": bool(checks)
        and all(
            check["payload_present"]
            and check["size_matches_lfs_pointer"]
            and check["sha256_matches_lfs_oid"]
            for check in checks
        ),
    }


def fixture_failure_mentions_pointer(
    result: dict[str, Any], remaining_pointer_paths: list[str]
) -> bool:
    output = "\n".join((result.get("stdout", ""), result.get("stderr", "")))
    return any(path in output for path in remaining_pointer_paths)


def classify_fixture(
    fixture_results: dict[str, dict[str, Any]],
    after: dict[str, Any],
    recovery: dict[str, Any],
) -> tuple[str, str]:
    statuses = [result["status"] for result in fixture_results.values()]
    if statuses and all(status == "pass" for status in statuses):
        return "resolved", "external fixture test passed for CPU and CUDA"

    remaining = [
        item["path"] for item in after["files"] if item["state"] == "git-lfs-pointer"
    ]
    failures_are_pointer_specific = bool(remaining) and all(
        result["status"] == "fail"
        and fixture_failure_mentions_pointer(result, remaining)
        for result in fixture_results.values()
    )
    recovery_unavailable = recovery["status"] in {"fail", "timeout", "unavailable"}
    if failures_are_pointer_specific and recovery_unavailable:
        return (
            "quarantined-external-fixture",
            "fixture test failures are confined to verified Git LFS pointer payloads after a recorded recovery failure",
        )
    return (
        "hard-failure",
        "fixture failure is not fully explained by remaining Git LFS pointers and a failed recovery attempt",
    )


def validate_stable_results(
    inventories: dict[str, dict[str, Any]],
    stable_results: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    inventory_sets = [set(inventory["tests"]) for inventory in inventories.values()]
    if len(inventory_sets) != 2 or inventory_sets[0] != inventory_sets[1]:
        errors.append("CPU and CUDA test inventories differ")
    for backend, inventory in inventories.items():
        result = stable_results[backend]
        summary = result.get("summary")
        if result["status"] != "pass":
            errors.append(f"{backend} stable test command failed")
            continue
        if summary is None:
            errors.append(f"{backend} stable test summary is missing")
            continue
        if summary["failed"] != 0:
            errors.append(f"{backend} stable tests reported failures")
        if summary["total"] != inventory["stable_test_count"]:
            errors.append(f"{backend} stable test count does not match inventory")
    return errors


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output_dir = (
        args.output_dir
        if args.output_dir.is_absolute()
        else repo_root / args.output_dir
    ).resolve()
    captured_at = datetime.now(timezone.utc).isoformat()
    builds = {
        "cpu": repo_root / "llama.cpp/build-cpu",
        "cuda": repo_root / "llama.cpp/build-cuda",
    }
    fixture_root = repo_root / "llama.cpp/models/ggml-vocabs"

    try:
        branch = required_stdout(["git", "branch", "--show-current"], cwd=repo_root)
        if branch != EXECUTION_BRANCH:
            raise MatrixError(f"unexpected execution branch: {branch}")
        llama_root = repo_root / "llama.cpp"
        if required_stdout(["git", "rev-parse", "HEAD"], cwd=llama_root) != LLAMA_CPP_COMMIT:
            raise MatrixError("llama.cpp revision changed")
        if required_stdout(["git", "status", "--porcelain"], cwd=llama_root):
            raise MatrixError("llama.cpp submodule is not clean before testing")

        inventories = {
            backend: load_inventory(build_dir, repo_root)
            for backend, build_dir in builds.items()
        }
        stable_results = {
            backend: attach_ctest_summary(
                run_command(
                    stable_command(build_dir), cwd=repo_root, timeout_seconds=900
                )
            )
            for backend, build_dir in builds.items()
        }
        stable_errors = validate_stable_results(inventories, stable_results)

        before = fixture_snapshot(fixture_root)
        if before["repository"] != FIXTURE_REPOSITORY:
            raise MatrixError(f"unexpected fixture repository: {before['repository']}")
        if before["revision"] != FIXTURE_REVISION:
            raise MatrixError(f"unexpected fixture revision: {before['revision']}")

        git_lfs = run_command(
            ["git", "lfs", "version"], cwd=fixture_root, timeout_seconds=30
        )
        pointer_paths = [
            item["path"] for item in before["files"] if item["state"] == "git-lfs-pointer"
        ]
        if pointer_paths:
            recovery = run_command(
                recovery_command(pointer_paths, fixture_root),
                cwd=repo_root,
                timeout_seconds=900,
            )
        else:
            recovery = {
                "status": "not-needed",
                "command": [],
                "exit_code": 0,
                "duration_seconds": 0.0,
                "stdout": "",
                "stderr": "no Git LFS pointers detected",
            }

        after = fixture_snapshot(fixture_root)
        recovered_payloads = validate_recovered_payloads(before, after)
        fixture_results = {
            backend: attach_ctest_summary(
                run_command(
                    fixture_command(build_dir), cwd=repo_root, timeout_seconds=300
                )
            )
            for backend, build_dir in builds.items()
        }
        classification, reason = classify_fixture(fixture_results, after, recovery)

        if required_stdout(["git", "rev-parse", "HEAD"], cwd=llama_root) != LLAMA_CPP_COMMIT:
            raise MatrixError("llama.cpp revision changed during testing")
        submodule_status_after = required_stdout(
            ["git", "status", "--porcelain"], cwd=llama_root
        )
        if submodule_status_after:
            raise MatrixError("llama.cpp submodule became dirty during testing")

        errors = list(stable_errors)
        if classification == "hard-failure":
            errors.append(reason)
        overall_status = "pass" if not errors else "fail"

        test_matrix = {
            "schema_version": SCHEMA_VERSION,
            "captured_at_utc": captured_at,
            "contract": {
                "execution_profile": "STANDARD",
                "execution_base": EXECUTION_BASE,
                "execution_branch": EXECUTION_BRANCH,
                "llama_cpp_commit": LLAMA_CPP_COMMIT,
                "external_fixture_test": EXTERNAL_TEST,
            },
            "inventories": inventories,
            "stable_results": stable_results,
            "external_fixture_results": fixture_results,
            "validation": {
                "status": overall_status,
                "errors": errors,
                "external_fixture_classification": classification,
            },
        }
        fixture_classification = {
            "schema_version": SCHEMA_VERSION,
            "captured_at_utc": captured_at,
            "fixture_repository": FIXTURE_REPOSITORY,
            "fixture_revision": FIXTURE_REVISION,
            "classification": classification,
            "reason": reason,
            "before_recovery": before,
            "git_lfs_observation": git_lfs,
            "recovery_attempt": recovery,
            "after_recovery": after,
            "recovered_payload_validation": recovered_payloads,
            "external_fixture_test_results": fixture_results,
            "submodule_revision_after": LLAMA_CPP_COMMIT,
            "submodule_clean_after": True,
            "validation": {"status": overall_status, "errors": errors},
        }
        write_json_atomic(output_dir / "test-matrix.json", test_matrix)
        write_json_atomic(
            output_dir / "fixture-classification.json", fixture_classification
        )
    except (MatrixError, OSError, json.JSONDecodeError) as error:
        print(f"test matrix failed: {error}", file=sys.stderr)
        return 1

    print(f"stable CPU tests: {stable_results['cpu']['summary']}")
    print(f"stable CUDA tests: {stable_results['cuda']['summary']}")
    print(f"external fixture classification: {classification}")
    print(f"wrote {output_dir / 'test-matrix.json'}")
    print(f"wrote {output_dir / 'fixture-classification.json'}")
    return 0 if overall_status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

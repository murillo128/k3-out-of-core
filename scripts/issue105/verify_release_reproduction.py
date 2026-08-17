#!/usr/bin/env python3
"""Regenerate issue-105 analysis from a clean checkout plus the release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any


ARCHIVE_ROOT = "issue105-curated-analysis-v1"
ANALYSIS_CODE_VERSION = "09a539fe1235c0ff85ea8893ddbb3deaba15f9da"


class ReproductionError(ValueError):
    """Raised when release-only regeneration is incomplete or non-identical."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=pathlib.Path, required=True)
    parser.add_argument("--archive", type=pathlib.Path, required=True)
    parser.add_argument("--archive-index", type=pathlib.Path, required=True)
    parser.add_argument("--project-commit", required=True)
    parser.add_argument("--report", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def git_output(repository: pathlib.Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *args], text=True).strip()


def validate_checkout(repository: pathlib.Path, project_commit: str) -> str:
    observed = git_output(repository, "rev-parse", "HEAD")
    if observed != project_commit:
        raise ReproductionError(f"checkout target mismatch: {observed}")
    if subprocess.run(
        ["git", "-C", str(repository), "symbolic-ref", "-q", "HEAD"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0:
        raise ReproductionError("reproduction checkout must be detached")
    dirty = git_output(repository, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ReproductionError(f"reproduction checkout is dirty: {dirty[:200]}")
    return git_output(repository, "rev-parse", "HEAD:llama.cpp")


def validate_member_paths(archive: pathlib.Path) -> list[str]:
    output = subprocess.check_output(
        ["tar", "--use-compress-program=unzstd", "-tf", str(archive)], text=True
    )
    members = [line for line in output.splitlines() if line]
    if not members:
        raise ReproductionError("release archive has no members")
    for member in members:
        parts = pathlib.PurePosixPath(member).parts
        if not member.startswith(f"{ARCHIVE_ROOT}/") or ".." in parts:
            raise ReproductionError(f"unsafe archive member: {member}")
    if len(members) != len(set(members)):
        raise ReproductionError("release archive contains duplicate member paths")
    return members


def file_identities(root: pathlib.Path) -> dict[str, dict[str, Any]]:
    result = {}
    for path in sorted(root.rglob("*"), key=lambda value: value.as_posix()):
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
    return result


def main() -> None:
    args = arguments()
    repository = args.repository_root.resolve(strict=True)
    archive = args.archive.resolve(strict=True)
    archive_index_path = args.archive_index.resolve(strict=True)
    archive_index = load_json(archive_index_path)
    if archive_index.get("status") != "PASS":
        raise ReproductionError("release archive index is not PASS")
    expected_archive = archive_index["archive"]
    if archive.name != expected_archive["name"]:
        raise ReproductionError("release archive name mismatch")
    if archive.stat().st_size != expected_archive["bytes"] or sha256(archive) != expected_archive["sha256"]:
        raise ReproductionError("release archive identity mismatch")
    nested_sha = validate_checkout(repository, args.project_commit)
    members = validate_member_paths(archive)
    if len(members) != expected_archive["member_count"]:
        raise ReproductionError("release archive member-count mismatch")

    with tempfile.TemporaryDirectory(prefix="issue105-release-reproduction-") as directory:
        temporary = pathlib.Path(directory)
        subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-xf", str(archive), "-C", str(temporary)],
            check=True,
        )
        package = temporary / ARCHIVE_ROOT
        regenerated = temporary / "regenerated-analysis"
        command = [
            sys.executable,
            str(repository / "scripts/issue105/analyze_evidence.py"),
            "--canonical-root", str(repository / "results/2026-08-17/issue105"),
            "--frozen-source-root", str(package / "inputs"),
            "--schema-root", str(repository / "schemas/issue105"),
            "--output-root", str(regenerated),
            "--analysis-code-version", ANALYSIS_CODE_VERSION,
        ]
        subprocess.run(command, check=True)
        expected = file_identities(repository / "results/2026-08-17/issue105/analysis")
        packaged = file_identities(package / "repository/results/2026-08-17/issue105/analysis")
        observed = file_identities(regenerated)
        if expected != packaged or expected != observed:
            differing = sorted(set(expected) ^ set(observed))
            raise ReproductionError(f"regenerated analysis differs: {differing[:5]}")
        tests = subprocess.run(
            [
                sys.executable, "-m", "unittest", "discover",
                "-s", str(repository / "tests/issue105"), "-p", "test_*.py",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    report = {
        "schema_version": "issue105-fresh-checkout-reproduction-v1",
        "status": "PASS",
        "checkout": {
            "project_commit": args.project_commit,
            "detached": True,
            "tracked_worktree_clean": True,
            "nested_llama_cpp_commit": nested_sha,
        },
        "release_archive": {
            "name": archive.name,
            "bytes": archive.stat().st_size,
            "sha256": sha256(archive),
            "member_count": len(members),
            "archive_index_sha256": sha256(archive_index_path),
        },
        "regeneration": {
            "analysis_code_version": ANALYSIS_CODE_VERSION,
            "artifact_count": len(observed),
            "byte_identical_to_checkout": True,
            "byte_identical_to_packaged_analysis": True,
            "requires_k3_model_or_original_host": False,
        },
        "focused_tests": {
            "status": "PASS",
            "command": "python -m unittest discover -s tests/issue105 -p test_*.py",
            "output_tail": tests.stdout.strip().splitlines()[-4:],
        },
    }
    write_json(args.report.resolve(), report)
    print(json.dumps({"status": "PASS", "artifact_count": len(observed)}, sort_keys=True))


if __name__ == "__main__":
    main()

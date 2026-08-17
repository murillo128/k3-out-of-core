#!/usr/bin/env python3
"""Create and verify the immutable issue-105 curated-analysis release archive."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import tarfile
import tempfile
from typing import Any, BinaryIO, Sequence

from jsonschema import Draft202012Validator


ARCHIVE_NAME = "issue105-curated-analysis-v3.tar.zst"
ARCHIVE_ROOT = "issue105-curated-analysis-v3"
RELEASE_TAG = "issue105-curated-analysis-v3"
ANALYSIS_CODE_VERSION = "76e0c3d578c4dba56e91d15ad643d8740037788a"
NESTED_LLAMA_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
FROZEN_INPUTS = {
    "host/stage-b-analysis-v1/family-overlap-matrix.json":
        "c1ef18ce953d15231c997e115980e9dcb1a315e8fa581ee32f15fb0324d6ea73",
    "host/stage-b-analysis-v1/stage-b2-family-length-route-endpoints.json":
        "24d48e8cf0fdf2c981562231d1492f6c6890b01eccaae239d1ac7c8b63d05b47",
    "host/stage-b-analysis-v1/standing-committee-core-periphery.json":
        "b7a23e73108c1612c3db9d822c9d82ff065764a4682ef037cdc277ba5d730566",
}


class PackagingError(ValueError):
    """Raised when an archive cannot be proven complete and deterministic."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=pathlib.Path, required=True)
    parser.add_argument("--frozen-source-root", type=pathlib.Path, required=True)
    parser.add_argument("--archive", type=pathlib.Path, required=True)
    parser.add_argument("--archive-index", type=pathlib.Path, required=True)
    parser.add_argument("--project-commit", required=True)
    parser.add_argument("--release-tag", default=RELEASE_TAG)
    parser.add_argument(
        "--release-url",
        default=f"https://github.com/murillo128/k3-out-of-core/releases/tag/{RELEASE_TAG}",
    )
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    return {"bytes": path.stat().st_size, "sha256": sha256(path)}


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def regular_file(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    metadata = os.lstat(resolved)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PackagingError(f"archive input is not a regular non-symlink file: {path}")
    return resolved


def ignored(path: pathlib.Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def add_tree(
    selected: list[tuple[pathlib.Path, str, str]],
    root: pathlib.Path,
    archive_prefix: str,
    role: str,
    excluded: set[pathlib.Path] | None = None,
) -> None:
    excluded = excluded or set()
    resolved_root = root.resolve(strict=True)
    for path in sorted(resolved_root.rglob("*"), key=lambda value: value.as_posix()):
        if not path.is_file() or path.is_symlink() or ignored(path):
            continue
        resolved = regular_file(path)
        if resolved in excluded:
            continue
        relative = resolved.relative_to(resolved_root).as_posix()
        selected.append((resolved, f"{archive_prefix}/{relative}", role))


def validate_selected(selected: Sequence[tuple[pathlib.Path, str, str]]) -> None:
    archive_paths = [archive_path for _, archive_path, _ in selected]
    if len(archive_paths) != len(set(archive_paths)):
        raise PackagingError("duplicate archive path")
    for archive_path in archive_paths:
        parts = pathlib.PurePosixPath(archive_path).parts
        if not archive_path.startswith(f"{ARCHIVE_ROOT}/") or ".." in parts:
            raise PackagingError(f"unsafe archive path: {archive_path}")


def tar_info(path: pathlib.Path, archive_path: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(archive_path)
    info.size = path.stat().st_size
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.type = tarfile.REGTYPE
    return info


def close_process(process: subprocess.Popen[bytes], label: str) -> None:
    if process.stdin is not None and not process.stdin.closed:
        process.stdin.close()
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    if process.stderr is not None:
        process.stderr.close()
    return_code = process.wait()
    if return_code:
        raise RuntimeError(f"{label} failed ({return_code}): {stderr}")


def create_archive(
    archive: pathlib.Path, selected: Sequence[tuple[pathlib.Path, str, str]]
) -> None:
    validate_selected(selected)
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_suffix(archive.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    process = subprocess.Popen(
        ["zstd", "-q", "-T1", "-10", "-f", "-o", str(temporary), "-"],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None:
        raise RuntimeError("zstd stdin unavailable")
    try:
        with tarfile.open(fileobj=process.stdin, mode="w|", format=tarfile.GNU_FORMAT) as stream:
            for path, archive_path, _ in sorted(selected, key=lambda row: row[1]):
                with path.open("rb") as source:
                    stream.addfile(tar_info(path, archive_path), source)
        close_process(process, "zstd archive creation")
    except BaseException:
        process.kill()
        process.wait()
        raise
    os.replace(temporary, archive)


def hash_stream(stream: BinaryIO) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def member_identities(
    selected: Sequence[tuple[pathlib.Path, str, str]]
) -> list[dict[str, Any]]:
    return [
        {"archive_path": archive_path, "role": role, **identity(path)}
        for path, archive_path, role in sorted(selected, key=lambda row: row[1])
    ]


def verify_archive(archive: pathlib.Path, members: Sequence[dict[str, Any]]) -> None:
    expected = {row["archive_path"]: row for row in members}
    process = subprocess.Popen(
        ["zstd", "-q", "-d", "-c", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise RuntimeError("zstd stdout unavailable")
    observed: set[str] = set()
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as stream:
            for member in stream:
                if not member.isfile() or member.name not in expected or member.name in observed:
                    raise PackagingError(f"unexpected archive member: {member.name}")
                source = stream.extractfile(member)
                if source is None:
                    raise PackagingError(f"unreadable archive member: {member.name}")
                size, digest = hash_stream(source)
                expected_row = expected[member.name]
                if size != expected_row["bytes"] or digest != expected_row["sha256"]:
                    raise PackagingError(f"archive member identity mismatch: {member.name}")
                observed.add(member.name)
        process.stdout.close()
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        if process.stderr is not None:
            process.stderr.close()
        return_code = process.wait()
        if return_code:
            raise RuntimeError(f"zstd archive verification failed ({return_code}): {stderr}")
    except BaseException:
        process.kill()
        process.wait()
        raise
    if observed != set(expected):
        raise PackagingError(f"archive members missing: {sorted(set(expected) - observed)[:5]}")


def select_files(
    repository: pathlib.Path,
    frozen_root: pathlib.Path,
    archive: pathlib.Path,
    archive_index: pathlib.Path,
    regeneration_guide: pathlib.Path,
) -> list[tuple[pathlib.Path, str, str]]:
    excluded = {
        archive.resolve(),
        archive_index.resolve(),
        (repository / "results/2026-08-17/issue105/release-archive-index.json").resolve(),
    }
    selected: list[tuple[pathlib.Path, str, str]] = []
    prefix = f"{ARCHIVE_ROOT}/repository"
    add_tree(
        selected,
        repository / "results/2026-08-17/issue105",
        f"{prefix}/results/2026-08-17/issue105",
        "CURATED_AND_ANALYSIS_EVIDENCE",
        excluded,
    )
    add_tree(selected, repository / "scripts/issue105", f"{prefix}/scripts/issue105", "REPRODUCTION_CODE")
    add_tree(selected, repository / "schemas/issue105", f"{prefix}/schemas/issue105", "VERSIONED_SCHEMA")
    add_tree(selected, repository / "tests/issue105", f"{prefix}/tests/issue105", "REPRODUCTION_TEST")
    selected.append((regular_file(repository / "docs/PRIOR_ART.md"), f"{prefix}/docs/PRIOR_ART.md", "PRIOR_ART_AUTHORITY"))
    selected.append((regular_file(regeneration_guide), f"{ARCHIVE_ROOT}/REGENERATE.md", "REPRODUCTION_GUIDE"))
    for relative, expected_sha in sorted(FROZEN_INPUTS.items()):
        path = regular_file(frozen_root / relative)
        if sha256(path) != expected_sha:
            raise PackagingError(f"frozen analysis input identity mismatch: {relative}")
        selected.append((path, f"{ARCHIVE_ROOT}/inputs/{relative}", "FROZEN_ANALYSIS_INPUT"))
    validate_selected(selected)
    return sorted(selected, key=lambda row: row[1])


def git_output(repository: pathlib.Path, *arguments: str) -> str:
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


def validate_repository(repository: pathlib.Path, project_commit: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", project_commit):
        raise PackagingError("project commit must be a full SHA-1")
    if git_output(repository, "rev-parse", "HEAD") != project_commit:
        raise PackagingError("project commit does not match repository HEAD")
    if git_output(repository, "rev-parse", "HEAD:llama.cpp") != NESTED_LLAMA_SHA:
        raise PackagingError("nested llama.cpp gitlink mismatch")
    analysis_catalog = load_json(repository / "results/2026-08-17/issue105/analysis/analysis-catalog.json")
    if analysis_catalog.get("status") != "PASS" or analysis_catalog.get("analysis_code_version") != ANALYSIS_CODE_VERSION:
        raise PackagingError("analysis catalog is not the expected exact-code PASS target")


def regeneration_guide(project_commit: str) -> str:
    return f"""# Regenerate issue 105 secondary analysis

This package is self-contained for offline regeneration. It requires Python 3.9+, `venv`, and no K3 model files, inference runtime, or original OCI host.

```bash
python -m venv .venv
.venv/bin/pip install --requirement repository/scripts/issue105/analysis-requirements.txt
.venv/bin/python repository/scripts/issue105/analyze_evidence.py \\
  --canonical-root repository/results/2026-08-17/issue105 \\
  --frozen-source-root inputs \\
  --schema-root repository/schemas/issue105 \\
  --output-root regenerated-analysis \\
  --analysis-code-version {ANALYSIS_CODE_VERSION}
diff -qr repository/results/2026-08-17/issue105/analysis regenerated-analysis
```

Archive content commit: `{project_commit}`

Nested llama.cpp gitlink: `{NESTED_LLAMA_SHA}`
"""


def build_index(
    args: argparse.Namespace,
    archive: pathlib.Path,
    members: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    role_counts = collections.Counter(row["role"] for row in members)
    role_bytes: dict[str, int] = collections.defaultdict(int)
    for row in members:
        role_bytes[row["role"]] += int(row["bytes"])
    result = {
        "schema_version": "issue105-release-archive-index-v3",
        "status": "PASS",
        "archive": {
            "name": archive.name,
            **identity(archive),
            "format": "deterministic GNU tar compressed with zstd -T1 -10",
            "member_count": len(members),
            "uncompressed_member_bytes": sum(int(row["bytes"]) for row in members),
        },
        "release": {
            "tag": args.release_tag,
            "url": args.release_url,
            "asset_name": archive.name,
        },
        "targets": {
            "archive_content_project_commit": args.project_commit,
            "analysis_code_commit": ANALYSIS_CODE_VERSION,
            "nested_llama_cpp_commit": NESTED_LLAMA_SHA,
        },
        "source_authority": {
            "issue102_archive_sha256": "e198913eb541b2a2e7465a01e09215fc5fecf6fb91574ff1841b11bf2664250c",
            "frozen_analysis_inputs": FROZEN_INPUTS,
        },
        "role_summary": {
            role: {"member_count": role_counts[role], "bytes": role_bytes[role]}
            for role in sorted(role_counts)
        },
        "verification": {
            "status": "PASS",
            "method": "streaming decompression plus complete member size/SHA-256 validation",
            "validated_member_count": len(members),
            "fresh_checkout_regeneration_required": True,
            "requires_k3_model_or_original_host": False,
        },
        "reproduction": {
            "guide": f"{ARCHIVE_ROOT}/REGENERATE.md",
            "expected_analysis_catalog_sha256": sha256(
                args.repository_root / "results/2026-08-17/issue105/analysis/analysis-catalog.json"
            ),
        },
        "members": list(members),
    }
    schema_path = args.repository_root / "schemas/issue105/release-archive-index-v3.schema.json"
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(result)
    return result


def main() -> None:
    args = arguments()
    repository = args.repository_root.resolve(strict=True)
    frozen_root = args.frozen_source_root.resolve(strict=True)
    archive = args.archive.resolve()
    archive_index = args.archive_index.resolve()
    if archive.name != ARCHIVE_NAME or args.release_tag != RELEASE_TAG:
        raise PackagingError("immutable archive name or release tag mismatch")
    validate_repository(repository, args.project_commit)
    with tempfile.TemporaryDirectory(prefix="issue105-package-") as directory:
        guide = pathlib.Path(directory) / "REGENERATE.md"
        with guide.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(regeneration_guide(args.project_commit))
        selected = select_files(repository, frozen_root, archive, archive_index, guide)
        members = member_identities(selected)
        create_archive(archive, selected)
        verify_archive(archive, members)
    index = build_index(args, archive, members)
    write_json(archive_index, index)
    print(json.dumps({
        "status": "PASS",
        "archive": archive.name,
        "bytes": archive.stat().st_size,
        "sha256": sha256(archive),
        "member_count": len(members),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

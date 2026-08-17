#!/usr/bin/env python3
"""Package and verify the immutable checksum-addressed issue-102 evidence release."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import tarfile
from typing import Any, BinaryIO


HOST_DIRECTORIES = (
    "checkpoint-a",
    "corpus",
    "host-normalization",
    "long-horizon",
    "observer-replay-v1",
    "posthoc-analysis-v1",
    "prefill-depth-curve",
    "protocol-bridge",
    "qualification",
    "qualification-clean",
    "sentinel-baseline",
    "stage-a",
    "stage-a-invalid",
    "stage-a-sentinels",
    "stage-b-analysis-v1",
    "stage-b-observer",
    "stage-b-observer-control-v4",
    "stage-c-control-v1",
    "stage-c-control-v2",
    "stage-c-v1",
    "stage-c-v2",
    "tooling",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=pathlib.Path, required=True)
    parser.add_argument("--host-root", type=pathlib.Path, required=True)
    parser.add_argument("--result-root", type=pathlib.Path, required=True)
    parser.add_argument("--archive", type=pathlib.Path, required=True)
    parser.add_argument("--archive-index", type=pathlib.Path, required=True)
    parser.add_argument("--freeze-output", type=pathlib.Path, required=True)
    parser.add_argument("--execution-project-sha", required=True)
    parser.add_argument("--nested-llama-sha", required=True)
    parser.add_argument("--evidence-parent-sha", required=True)
    parser.add_argument("--final-synthesis", type=pathlib.Path, required=True)
    parser.add_argument("--expected-final-synthesis-sha256", required=True)
    parser.add_argument("--normalized-prompt-level", type=pathlib.Path, required=True)
    parser.add_argument("--expected-normalized-prompt-level-sha256", required=True)
    parser.add_argument("--stage-c-synthesis", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-c-synthesis-sha256", required=True)
    parser.add_argument("--stage-c-progress", type=pathlib.Path, required=True)
    parser.add_argument("--expected-stage-c-progress-sha256", required=True)
    parser.add_argument("--release-tag", default="issue102-cross-prompt-v1")
    parser.add_argument(
        "--release-url",
        default=(
            "https://github.com/murillo128/k3-out-of-core/releases/tag/"
            "issue102-cross-prompt-v1"
        ),
    )
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def require_identity(path: pathlib.Path, expected_sha256: str) -> dict[str, Any]:
    result = identity(path)
    if result["sha256"] != expected_sha256:
        raise ValueError(f"identity mismatch: {path}")
    return result


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return identity(path)


def regular_file(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve(strict=True)
    metadata = os.lstat(resolved)
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise ValueError(f"archive input is not an exact regular file: {path}")
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
    for path in sorted(resolved_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.is_symlink() or ignored(path):
            continue
        resolved = regular_file(path)
        if resolved in excluded:
            continue
        relative = resolved.relative_to(resolved_root).as_posix()
        selected.append((resolved, f"{archive_prefix}/{relative}", role))


def member_identity(path: pathlib.Path, archive_path: str, role: str) -> dict[str, Any]:
    result = identity(path)
    result["archive_path"] = archive_path
    result["role"] = role
    return result


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
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"{label} failed ({return_code}): {stderr}")


def create_archive(
    archive: pathlib.Path,
    selected: list[tuple[pathlib.Path, str, str]],
) -> None:
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
        with tarfile.open(fileobj=process.stdin, mode="w|", format=tarfile.GNU_FORMAT) as archive_stream:
            for path, archive_path, _ in selected:
                with path.open("rb") as source:
                    archive_stream.addfile(tar_info(path, archive_path), source)
        close_process(process, "zstd archive creation")
    except BaseException:
        process.kill()
        process.wait()
        raise
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, archive)
    directory = os.open(archive.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def hash_stream(stream: BinaryIO) -> tuple[int, str]:
    count = 0
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        count += len(chunk)
        digest.update(chunk)
    return count, digest.hexdigest()


def verify_archive(archive: pathlib.Path, members: list[dict[str, Any]]) -> None:
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
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive_stream:
            for member in archive_stream:
                if not member.isfile() or member.name not in expected or member.name in observed:
                    raise ValueError(f"unexpected archive member: {member.name}")
                source = archive_stream.extractfile(member)
                if source is None:
                    raise ValueError(f"archive member unreadable: {member.name}")
                size, digest = hash_stream(source)
                row = expected[member.name]
                if size != row["bytes"] or digest != row["sha256"]:
                    raise ValueError(f"archive member identity mismatch: {member.name}")
                observed.add(member.name)
        stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(f"zstd archive verification failed ({return_code}): {stderr}")
    except BaseException:
        process.kill()
        process.wait()
        raise
    if observed != set(expected):
        missing = sorted(set(expected) - observed)
        raise ValueError(f"archive members missing: {missing[:10]}")


def select_files(args: argparse.Namespace) -> list[tuple[pathlib.Path, str, str]]:
    repository = args.repository_root.resolve(strict=True)
    host = args.host_root.resolve(strict=True)
    result_root = args.result_root.resolve(strict=True)
    excluded = {
        args.archive_index.resolve(), args.freeze_output.resolve(), args.archive.resolve(),
    }
    selected: list[tuple[pathlib.Path, str, str]] = []
    add_tree(
        selected, result_root,
        "repository/results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt",
        "COMMITTED_COMPACT_EVIDENCE", excluded,
    )
    add_tree(
        selected, repository / "scripts/issue102", "repository/scripts/issue102",
        "REPRODUCTION_SOURCE",
    )
    for path, archive_path, role in (
        (
            repository / "corpus/phase13/issue102-cross-prompt-v1.json",
            "repository/corpus/phase13/issue102-cross-prompt-v1.json",
            "FROZEN_CORPUS",
        ),
        (repository / "AGENTS.md", "repository/AGENTS.md", "WORKFLOW_CONTEXT"),
        (
            host / "owner-comments.json", "host/owner-comments.json",
            "CORPUS_AUTHORITY_CAPTURE",
        ),
        (
            host / "build/bin/issue102-cross-prompt-probe",
            "runtime/issue102-cross-prompt-probe", "FROZEN_HELPER_BINARY",
        ),
        (
            pathlib.Path("/mnt/nvme1/issue98/identity/build-fingerprint.json"),
            "external-identities/issue98-build-fingerprint.json", "RUNTIME_IDENTITY",
        ),
        (
            pathlib.Path(
                "/mnt/nvme1/issue98/upstream-evidence/issue73-final/identity/"
                "max-safe-artifact-identity.json"
            ),
            "external-identities/k3-model-identity.json", "MODEL_IDENTITY",
        ),
    ):
        selected.append((regular_file(path), archive_path, role))
    for directory in HOST_DIRECTORIES:
        add_tree(
            selected, host / directory, f"host/{directory}",
            "RAW_OR_DERIVED_HOST_EVIDENCE",
        )
    archive_paths = [archive_path for _, archive_path, _ in selected]
    source_paths = [path for path, _, _ in selected]
    if len(archive_paths) != len(set(archive_paths)):
        raise ValueError("duplicate archive path")
    if len(source_paths) != len(set(source_paths)):
        duplicates = [
            str(path) for path, count in collections.Counter(source_paths).items() if count > 1
        ]
        raise ValueError(f"duplicate archive source: {duplicates[:10]}")
    return sorted(selected, key=lambda row: row[1])


def main() -> int:
    args = arguments()
    if args.archive.name != "issue102-cross-prompt-evidence-v1.tar.zst":
        raise ValueError("unexpected immutable archive name")
    critical = {
        "final_synthesis": require_identity(
            args.final_synthesis, args.expected_final_synthesis_sha256
        ),
        "normalized_prompt_level": require_identity(
            args.normalized_prompt_level, args.expected_normalized_prompt_level_sha256
        ),
        "stage_c_synthesis": require_identity(
            args.stage_c_synthesis, args.expected_stage_c_synthesis_sha256
        ),
        "stage_c_progress": require_identity(
            args.stage_c_progress, args.expected_stage_c_progress_sha256
        ),
        "packager": identity(pathlib.Path(__file__)),
    }
    with args.final_synthesis.open() as stream:
        final = json.load(stream)
    if (
        final.get("status") != "pass"
        or final.get("schema_version") != "phase13-6pg-issue102-final-synthesis-v1"
        or final.get("primary_outcomes", {}).get("FOLLOWUP_99_CORPUS_READY") != "yes"
        or final.get("completeness", {}).get("stage_c_physical_cells") != 48
    ):
        raise ValueError("final synthesis is not freeze-ready")

    selected = select_files(args)
    members = [member_identity(*row) for row in selected]
    create_archive(args.archive, selected)
    verify_archive(args.archive, members)
    archive_identity = identity(args.archive)
    role_counts = collections.Counter(row["role"] for row in members)
    role_bytes: dict[str, int] = collections.defaultdict(int)
    for row in members:
        role_bytes[row["role"]] += row["bytes"]
    archive_index = {
        "schema_version": "phase13-6pg-issue102-evidence-archive-index-v1",
        "status": "pass",
        "archive": {
            **archive_identity,
            "format": "deterministic GNU tar compressed with zstd -T1 -10",
            "member_count": len(members),
            "uncompressed_member_bytes": sum(row["bytes"] for row in members),
        },
        "role_summary": {
            role: {"member_count": role_counts[role], "bytes": role_bytes[role]}
            for role in sorted(role_counts)
        },
        "verification": {
            "status": "PASS",
            "method": "streaming decompression and complete member size/SHA-256 validation",
            "validated_member_count": len(members),
        },
        "members": members,
    }
    index_identity = write_json(args.archive_index, archive_index)

    freeze = {
        "schema_version": "phase13-6pg-issue102-evidence-freeze-v1",
        "status": "pass",
        "provenance": "IMMUTABLE_CHECKSUM_ADDRESSED_ISSUE102_FINAL_EVIDENCE",
        "execution_target": {
            "project_sha": args.execution_project_sha,
            "nested_llama_cpp_sha": args.nested_llama_sha,
        },
        "evidence_parent_sha": args.evidence_parent_sha,
        "critical_artifacts": {
            **critical,
            "archive_index": index_identity,
            "archive": archive_identity,
            "frozen_corpus": identity(
                args.repository_root / "corpus/phase13/issue102-cross-prompt-v1.json"
            ),
            "stage_a_final_checkpoint": identity(
                args.result_root / "stage-a-round-08-checkpoint.json"
            ),
            "stage_b_capacity_handoff": identity(
                args.result_root / "stage-b-capacity-handoff.json"
            ),
            "original_stage_c_failure": identity(
                args.result_root / "stage-c-technical-return.json"
            ),
            "stage_c_recovery_control": identity(
                args.result_root / "stage-c-recovery-control.json"
            ),
        },
        "archive_release": {
            "tag": args.release_tag,
            "url": args.release_url,
            "asset_name": args.archive.name,
            "publication_status": "READY_AFTER_EXACT_EVIDENCE_COMMIT",
            "archive_sha256": archive_identity["sha256"],
            "archive_bytes": archive_identity["bytes"],
            "member_count": len(members),
        },
        "completeness": final["completeness"],
        "primary_outcomes": final["primary_outcomes"],
        "reviews": {
            "checkpoint_a_pass": (
                "https://github.com/murillo128/k3-out-of-core/issues/102#"
                "issuecomment-5281857040"
            ),
            "checkpoint_b_pass": (
                "https://github.com/murillo128/k3-out-of-core/issues/102#"
                "issuecomment-5307346928"
            ),
            "stage_c_recovery_preexecution_pass": (
                "https://github.com/murillo128/k3-out-of-core/issues/102#"
                "issuecomment-5308081832"
            ),
            "checkpoint_c": "PENDING_FINAL_CAPABLE_REVIEW_OF_EXACT_PUBLISHED_TARGET",
        },
        "handoff": final["handoff"],
        "interpretation_limits": final["limitations"],
        "disposition": "ISSUE102_FINAL_EVIDENCE_FROZEN_READY_FOR_PUBLICATION_AND_CHECKPOINT_C",
    }
    freeze_identity = write_json(args.freeze_output, freeze)
    print(json.dumps({
        "status": "pass",
        "archive": archive_identity,
        "archive_index": index_identity,
        "evidence_freeze": freeze_identity,
        "member_count": len(members),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

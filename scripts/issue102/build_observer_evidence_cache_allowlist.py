#!/usr/bin/env python3
"""Build the exact issue-102 observer-evidence page-cache release allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
from typing import Any


EXPECTED_PREREGISTRATION_SHA256 = (
    "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
)
EXPECTED_TECHNICAL_RETURN_SHA256 = (
    "c1493c3732e349179ce71dc8a5933da82736967083fee87d07ceef57d3f3d264"
)
EXPECTED_FILE_COUNT = 18


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--technical-return", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
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


def has_symlink_component(path: pathlib.Path, root: pathlib.Path) -> bool:
    relative = path.relative_to(root)
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def validate_file(
    source: str,
    artifact: dict[str, Any],
    root: pathlib.Path,
) -> dict[str, Any]:
    declared = pathlib.Path(artifact["path"])
    if not declared.is_absolute():
        raise ValueError(f"allowlist path is not absolute: {declared}")
    resolved = declared.resolve(strict=True)
    if resolved != declared or not resolved.is_relative_to(root):
        raise ValueError(f"allowlist path escapes or aliases the evidence root: {declared}")
    if has_symlink_component(resolved, root):
        raise ValueError(f"allowlist path contains a symlink: {resolved}")
    metadata = os.lstat(resolved)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"allowlist path is not a regular file: {resolved}")
    expected_size = artifact.get("bytes", metadata.st_size)
    if metadata.st_size != expected_size or sha256(resolved) != artifact["sha256"]:
        raise ValueError(f"allowlist artifact identity changed: {resolved}")
    return {
        "source": source,
        "canonical_path": str(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "bytes": metadata.st_size,
        "sha256": artifact["sha256"],
    }


def main() -> int:
    args = arguments()
    preregistration_path = args.preregistration.resolve(strict=True)
    technical_return_path = args.technical_return.resolve(strict=True)
    root = args.evidence_root.resolve(strict=True)
    output_path = args.output.resolve()
    if sha256(preregistration_path) != EXPECTED_PREREGISTRATION_SHA256:
        raise ValueError("observer V2 preregistration identity changed")
    if sha256(technical_return_path) != EXPECTED_TECHNICAL_RETURN_SHA256:
        raise ValueError("observer technical-return identity changed")
    preregistration = json.loads(preregistration_path.read_text())
    technical_return = json.loads(technical_return_path.read_text())
    if technical_return["interpretation"]["next"] != "RETURN_TO_DESIGN_FOR_FROZEN_CAPACITY_ADMISSION_DECISION":
        raise ValueError("technical return does not own the expected admission failure")

    declared: list[tuple[str, dict[str, Any]]] = []
    initial_failure = preregistration["supersession"]["failed_attempt"]
    for name in ("envelope", "stderr", "stdout"):
        declared.append((f"observer-v1-failed-attempt:{name}", initial_failure[name]))
    for capture in technical_return["campaign"]["accepted"]:
        for name in ("result", "envelope", "stdout", "stderr"):
            declared.append((f"accepted-{capture['ordinal']:03d}:{name}", capture["artifacts"][name]))
    failed_capture = technical_return["campaign"]["failed"]
    for name in ("envelope", "stdout", "stderr"):
        declared.append((f"failed-{failed_capture['ordinal']:03d}:{name}", failed_capture["artifacts"][name]))
    if len(declared) != EXPECTED_FILE_COUNT:
        raise ValueError("observer evidence source did not produce the exact 18-file allowlist")

    files = [validate_file(source, artifact, root) for source, artifact in declared]
    paths = [row["canonical_path"] for row in files]
    inodes = [(row["device"], row["inode"]) for row in files]
    if len(set(paths)) != len(paths) or len(set(inodes)) != len(inodes):
        raise ValueError("observer evidence allowlist contains duplicate paths or inodes")
    output = {
        "schema_version": "phase13-6pg-observer-evidence-cache-allowlist-v1",
        "status": "frozen",
        "purpose": "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE",
        "inputs": {
            "preregistration": identity(preregistration_path),
            "technical_return": identity(technical_return_path),
            "generator": identity(pathlib.Path(__file__)),
        },
        "evidence_root": str(root),
        "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files),
        "files": files,
        "operation": {
            "durability": "syncfs on the observer evidence filesystem before exact-file advice",
            "advice": "POSIX_FADV_DONTNEED",
            "read_payload_after_release": False,
            "model_or_runtime_file_allowed": False,
            "path_outside_evidence_root_allowed": False,
        },
        "disposition": "READY_FOR_TARGETED_HYGIENE_GATE",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "file_count": len(files),
        "total_bytes": output["total_bytes"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Freeze the exact evidence-file cache allowlist for one observer attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
from typing import Any, Optional


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--ordinal", type=int, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--process-exit-status", type=int, required=True)
    parser.add_argument("--validation-record", type=pathlib.Path)
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
    current = root
    if current.is_symlink():
        return True
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def frozen_file(
    path: pathlib.Path,
    root: pathlib.Path,
    source: str,
    expected: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not path.is_absolute():
        raise ValueError(f"capture evidence path is not absolute: {path}")
    resolved = path.resolve(strict=True)
    if resolved != path or not resolved.is_relative_to(root):
        raise ValueError(f"capture evidence path escapes or aliases the evidence root: {path}")
    if has_symlink_component(resolved, root):
        raise ValueError(f"capture evidence path contains a symlink: {resolved}")
    metadata = os.lstat(resolved)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"capture evidence path is not a regular file: {resolved}")
    if expected is not None and (
        pathlib.Path(expected["path"]).resolve() != resolved
        or expected["bytes"] != metadata.st_size
    ):
        raise ValueError(f"prevalidated capture identity changed: {resolved}")
    return {
        "source": source,
        "canonical_path": str(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "bytes": metadata.st_size,
        "sha256": expected["sha256"] if expected is not None else sha256(resolved),
        "sha256_source": (
            "PRE_RELEASE_EXHAUSTIVE_VALIDATION" if expected is not None
            else "PRE_RELEASE_ALLOWLIST_HASH"
        ),
    }


def main() -> int:
    args = arguments()
    root = args.evidence_root.resolve(strict=True)
    capture_root = args.capture_root.resolve(strict=True)
    output_path = args.output.resolve()
    if not capture_root.is_relative_to(root) or capture_root.parent != root:
        raise ValueError("capture root is not an immediate child of the evidence root")
    if capture_root.is_symlink() or not capture_root.is_dir():
        raise ValueError("capture root is not a real directory")
    if capture_root.name != f"run-{args.ordinal:03d}-{args.case_id}":
        raise ValueError("capture root name does not match the frozen ordinal/case identity")

    required = ["envelope.json", "stdout.log", "stderr.log"]
    if not all((capture_root / name).is_file() for name in required):
        raise ValueError("attempt is missing a required runner evidence file")
    result_path = capture_root / "result.json"
    if args.process_exit_status == 0 and not result_path.is_file():
        raise ValueError("successful process attempt is missing result.json")

    envelope = json.loads((capture_root / "envelope.json").read_text())
    if (
        envelope.get("run_ordinal") != args.ordinal
        or envelope.get("campaign") != "issue102-stage-b-observer"
        or envelope.get("exit_status") != args.process_exit_status
    ):
        raise ValueError("capture envelope identity/status mismatch")
    names = (["result.json"] if result_path.is_file() else []) + required
    validated: dict[str, Any] = {}
    validation_identity = None
    if args.validation_record is not None:
        validation_path = args.validation_record.resolve(strict=True)
        validation = json.loads(validation_path.read_text())
        capture = validation.get("capture", {})
        if (
            validation.get("status") != "pass"
            or capture.get("ordinal") != args.ordinal
            or capture.get("case_id") != args.case_id
            or args.process_exit_status != 0
        ):
            raise ValueError("pre-release validation record does not own this attempt")
        validated = {
            "result.json": capture["result"],
            "envelope.json": capture["envelope"],
            "stdout.log": capture["stdout"],
            "stderr.log": capture["stderr"],
        }
        validation_identity = identity(validation_path)
    elif args.process_exit_status == 0:
        raise ValueError("successful attempt requires its exhaustive validation record")
    files = [
        frozen_file(
            capture_root / name, root, f"capture-{args.ordinal:03d}:{name}",
            validated.get(name),
        )
        for name in names
    ]
    output = {
        "schema_version": "phase13-6pg-observer-attempt-cache-allowlist-v1",
        "status": "frozen",
        "purpose": "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE",
        "inputs": {
            "generator": identity(pathlib.Path(__file__)),
            "validation_record": validation_identity,
        },
        "capture": {
            "ordinal": args.ordinal,
            "case_id": args.case_id,
            "process_exit_status": args.process_exit_status,
            "scientific_result_present": result_path.is_file(),
            "root": str(capture_root),
        },
        "evidence_root": str(root),
        "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files),
        "files": files,
        "operation": {
            "durability": "syncfs on observer evidence filesystem before exact-file advice",
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

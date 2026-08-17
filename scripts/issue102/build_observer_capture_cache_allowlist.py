#!/usr/bin/env python3
"""Freeze an exact page-cache release allowlist for one observer attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=pathlib.Path, required=True)
    parser.add_argument("--evidence-root", type=pathlib.Path, required=True)
    parser.add_argument("--ordinal", type=int, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--capture-status", choices=("pass", "failed"), required=True)
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


def frozen_file(path: pathlib.Path, root: pathlib.Path, source: str) -> dict[str, Any]:
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
    return {
        "source": source,
        "canonical_path": str(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "bytes": metadata.st_size,
        "sha256": sha256(resolved),
    }


def main() -> int:
    args = arguments()
    root = args.evidence_root.resolve(strict=True)
    capture_root = args.capture_root.resolve(strict=True)
    output_path = args.output.resolve()
    if not capture_root.is_relative_to(root) or capture_root.parent != root:
        raise ValueError("capture root is not an immediate child of the observer evidence root")
    if capture_root.is_symlink() or not capture_root.is_dir():
        raise ValueError("capture root is not a real directory")
    if not capture_root.name.startswith(f"run-{args.ordinal:03d}-{args.case_id}"):
        raise ValueError("capture root name does not match ordinal/case identity")

    names = ["envelope.json", "stdout.log", "stderr.log"]
    if args.capture_status == "pass":
        names.insert(0, "result.json")
    elif (capture_root / "result.json").exists():
        raise ValueError("failed capture unexpectedly has a result payload")
    files = [
        frozen_file(capture_root / name, root, f"capture-{args.ordinal:03d}:{name}")
        for name in names
    ]
    envelope = json.loads((capture_root / "envelope.json").read_text())
    expected_exit = 0 if args.capture_status == "pass" else 1
    if envelope["run_ordinal"] != args.ordinal or envelope["exit_status"] != expected_exit:
        raise ValueError("capture envelope identity/status mismatch")
    if args.capture_status == "pass":
        result = json.loads((capture_root / "result.json").read_text())
        if (
            result["case"]["id"] != args.case_id
            or result["status"] != "pass"
            or result["exit_status"] != 0
        ):
            raise ValueError("capture result identity/status mismatch")

    output = {
        "schema_version": "phase13-6pg-observer-capture-cache-allowlist-v1",
        "status": "frozen",
        "purpose": "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE",
        "inputs": {"generator": identity(pathlib.Path(__file__))},
        "capture": {
            "ordinal": args.ordinal,
            "case_id": args.case_id,
            "status": args.capture_status,
            "root": str(capture_root),
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

#!/usr/bin/env python3
"""Combine frozen issue-102 observer-output allowlists without rereading payloads."""

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
    parser.add_argument("--prefix-allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--expected-prefix-sha256", required=True)
    parser.add_argument("--retry-allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--expected-retry-sha256", required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--expected-progress-sha256", required=True)
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


def verified_identity(path: pathlib.Path, expected_sha256: str, expected_bytes: int | None = None) -> dict[str, Any]:
    result = identity(path)
    if result["sha256"] != expected_sha256 or (
        expected_bytes is not None and result["bytes"] != expected_bytes
    ):
        raise ValueError(f"allowlist input identity changed: {path}")
    return result


def load_allowlist(path: pathlib.Path, expected: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source = verified_identity(path, expected["sha256"], expected.get("bytes"))
    with path.resolve(strict=True).open() as stream:
        document = json.load(stream)
    if (
        document.get("status") != "frozen"
        or document.get("purpose") != "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE"
        or document.get("disposition") != "READY_FOR_TARGETED_HYGIENE_GATE"
    ):
        raise ValueError(f"input allowlist is not frozen/executable: {path}")
    return source, document


def capture_artifacts(capture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if "artifacts" in capture:
        return capture["artifacts"]
    return {name: capture[name] for name in ("result", "envelope", "stdout", "stderr")}


def main() -> None:
    args = arguments()
    progress_identity = verified_identity(args.progress, args.expected_progress_sha256)
    with args.progress.resolve(strict=True).open() as stream:
        progress = json.load(stream)
    if (
        progress.get("schema_version") != "phase13-6pg-stage-b-observer-resume-progress-v4"
        or progress.get("status") != "pass"
        or progress.get("accepted_capture_count") != 44
        or progress.get("expected_capture_count") != 44
        or len(progress.get("captures", [])) != 44
    ):
        raise ValueError("observer progress is not the accepted frozen 44-case campaign")

    inputs: list[tuple[str, pathlib.Path, dict[str, Any]]] = [
        (
            "initial-prefix-through-failed-capture-004",
            args.prefix_allowlist,
            {"sha256": args.expected_prefix_sha256},
        ),
        (
            "authorized-capture-004-retry",
            args.retry_allowlist,
            {"sha256": args.expected_retry_sha256},
        ),
    ]
    for capture in progress["captures"]:
        if capture["ordinal"] < 5:
            continue
        allowlist = capture.get("hygiene", {}).get("allowlist")
        if not allowlist:
            raise ValueError(f"capture lacks frozen allowlist identity: {capture['ordinal']}")
        inputs.append((
            f"accepted-capture-{capture['ordinal']:03d}",
            pathlib.Path(allowlist["path"]),
            allowlist,
        ))
    if len(inputs) != 42:
        raise ValueError("expected two historical and forty continuation allowlists")

    source_rows = []
    files = []
    evidence_root: str | None = None
    for source_name, path, expected in inputs:
        source_identity, document = load_allowlist(path, expected)
        source_rows.append({"source": source_name, "allowlist": source_identity})
        if evidence_root is None:
            evidence_root = document["evidence_root"]
        elif evidence_root != document["evidence_root"]:
            raise ValueError("allowlists do not share one evidence root")
        for row in document["files"]:
            copy = dict(row)
            copy["aggregate_source"] = source_name
            files.append(copy)

    paths = [row["canonical_path"] for row in files]
    inodes = [(row["device"], row["inode"]) for row in files]
    if len(paths) != 182 or len(set(paths)) != len(paths) or len(set(inodes)) != len(inodes):
        raise ValueError("aggregate allowlist does not contain 182 unique files/inodes")
    for row in files:
        path = pathlib.Path(row["canonical_path"])
        metadata = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_dev != row["device"]
            or metadata.st_ino != row["inode"]
            or metadata.st_size != row["bytes"]
        ):
            raise ValueError(f"allowlisted metadata changed: {path}")

    indexed = {row["canonical_path"]: row for row in files}
    for capture in progress["captures"]:
        for artifact in capture_artifacts(capture).values():
            path = str(pathlib.Path(artifact["path"]).resolve(strict=True))
            row = indexed.get(path)
            if row is None or row["bytes"] != artifact["bytes"] or row["sha256"] != artifact["sha256"]:
                raise ValueError(f"accepted capture artifact is not exactly allowlisted: {path}")

    output = {
        "schema_version": "phase13-6pg-final-observer-evidence-cache-allowlist-v1",
        "status": "frozen",
        "purpose": "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE",
        "inputs": {
            "generator": identity(pathlib.Path(__file__)),
            "observer_progress": progress_identity,
            "component_allowlists": source_rows,
        },
        "evidence_root": evidence_root,
        "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files),
        "accepted_capture_coverage": {
            "count": 44,
            "ordinals": list(range(1, 45)),
            "artifact_fields": ["result", "envelope", "stdout", "stderr"],
        },
        "files": files,
        "operation": {
            "durability": "syncfs on observer evidence filesystem before exact-file advice",
            "advice": "POSIX_FADV_DONTNEED",
            "read_payload_after_release": False,
            "payload_rehash_during_aggregation": False,
            "model_or_runtime_file_allowed": False,
            "path_outside_evidence_root_allowed": False,
        },
        "disposition": "READY_FOR_TARGETED_HYGIENE_GATE",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({
        "status": "pass",
        "output": identity(args.output),
        "file_count": len(files),
        "total_bytes": output["total_bytes"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()

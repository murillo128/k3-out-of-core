#!/usr/bin/env python3
"""Create a deterministic archive and compact index for one experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def add_file(archive: tarfile.TarFile, path: Path, archive_path: str) -> None:
    info = archive.gettarinfo(str(path), arcname=archive_path)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = 0o644
    with path.open("rb") as stream:
        archive.addfile(info, stream)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, action="append", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw_root = args.raw_root.resolve()
    archive_path = args.archive.resolve()
    output_path = args.output.resolve()
    if not raw_root.is_dir():
        raise ValueError(f"missing raw evidence directory: {raw_root}")
    selected = [
        (path, f"raw/{path.relative_to(raw_root)}")
        for path in sorted(raw_root.rglob("*")) if path.is_file()
    ]
    for summary_arg in args.summary:
        summary = summary_arg.resolve()
        if not summary.is_file():
            raise ValueError(f"missing summary: {summary}")
        selected.append((summary, f"compact/{summary.name}"))
    identities = [{
        "archive_path": name,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    } for path, name in selected]
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w") as archive:
        for path, name in selected:
            add_file(archive, path, name)
    with tarfile.open(archive_path, "r") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        if set(members) != {name for _, name in selected}:
            raise ValueError("archive membership mismatch")
        for expected in identities:
            member = archive.extractfile(str(expected["archive_path"]))
            if member is None:
                raise ValueError("archive member cannot be read")
            digest = hashlib.sha256()
            for block in iter(lambda: member.read(1 << 20), b""):
                digest.update(block)
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError("archive member checksum mismatch")
    document = {
        "schema_version": "phase12-nvme-experiment-raw-index-v1",
        "status": "PASS",
        "experiment": args.experiment,
        "archive": {
            "path": str(archive_path),
            "size": archive_path.stat().st_size,
            "sha256": sha256_file(archive_path),
            "format": "deterministic POSIX tar; uid/gid/mtime normalized",
        },
        "file_count": len(identities),
        "files": identities,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "experiment": args.experiment, "archive": document["archive"], "files": len(identities)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

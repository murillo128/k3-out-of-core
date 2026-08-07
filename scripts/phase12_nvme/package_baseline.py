#!/usr/bin/env python3
"""Archive and index the raw fio and baseline-matrix evidence deterministically."""
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


def identity(path: Path, archive_path: str) -> dict[str, object]:
    return {
        "archive_path": archive_path,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


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
    parser.add_argument("--fio-raw", type=Path, required=True)
    parser.add_argument("--matrix-raw", type=Path, required=True)
    parser.add_argument("--compact-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    archive_path = args.archive.resolve()
    output_path = args.output.resolve()
    selected: list[tuple[Path, str]] = []
    for label, root_arg in (("fio", args.fio_raw), ("matrix", args.matrix_raw)):
        root = root_arg.resolve()
        if not root.is_dir():
            raise ValueError(f"missing raw evidence directory: {root}")
        for path in sorted(root.rglob("*")):
            if path.is_file():
                selected.append((path, f"raw/{label}/{path.relative_to(root)}"))
    for name in ("fio-characterization.json", "baseline-matrix.json", "baseline-analysis.json"):
        path = (args.compact_root / name).resolve()
        if not path.is_file():
            raise ValueError(f"missing compact evidence: {path}")
        selected.append((path, f"compact/{name}"))
    identities = [identity(path, name) for path, name in selected]
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w") as archive:
        for path, name in selected:
            add_file(archive, path, name)
    with tarfile.open(archive_path, "r") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        if set(members) != {name for _, name in selected}:
            raise ValueError("archive membership mismatch")
        for expected in identities:
            extracted = archive.extractfile(str(expected["archive_path"]))
            if extracted is None:
                raise ValueError("archive member cannot be read")
            digest = hashlib.sha256()
            for block in iter(lambda: extracted.read(1 << 20), b""):
                digest.update(block)
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError("archive member checksum mismatch")
    document = {
        "schema_version": "phase12-nvme-baseline-raw-index-v1",
        "status": "PASS",
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
    print(json.dumps({"status": "PASS", "archive": document["archive"], "files": len(identities)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

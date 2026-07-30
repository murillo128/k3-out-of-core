#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from common import diagnostics, run, write


CHECK_KEYS = {"command", "byte_count", "sha256_exact", "source_spans", "split_cross_file"}


def split_paths(first: Path) -> list[Path]:
    name = first.name
    marker = "-00001-of-"
    if marker not in name:
        raise RuntimeError(f"not a first split path: {first}")
    prefix, suffix = name.split(marker, 1)
    count = int(suffix.split(".", 1)[0])
    extension = "." + suffix.split(".", 1)[1]
    return [first.with_name(f"{prefix}-{number:05d}-of-{count:05d}{extension}")
            for number in range(1, count + 1)]


def read_exact(path: Path, offset: int, count: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        result = bytearray()
        while len(result) < count:
            block = os.pread(descriptor, count - len(result), offset + len(result))
            if not block:
                raise RuntimeError(f"short read: {path}:{offset}+{count}")
            result.extend(block)
        return bytes(result)
    finally:
        os.close(descriptor)


def capture(executable: Path, model: Path, sources: list[Path], representation: str,
            kind: str, root: Path, directory: Path) -> dict:
    dump = directory/f"{representation}-{kind}.bin"
    command = [str(executable), "--model", str(model), "--mode", "cold", "--capacity", "8",
               "--cold-bytes", "67108864", "--ring-bytes", "16777216", "--steps", "3",
               "--dump-cold-bundle", str(dump)]
    result = run(command, root)
    output = result["stdout"] + result["stderr"]
    observed = diagnostics(output, "PHASE6_BUNDLE")
    spans = []
    source_hash = hashlib.sha256()
    source_bytes = 0
    for text in str(observed["spans"]).split(","):
        split_index, offset, count = (int(value) for value in text.split(":"))
        if split_index >= len(sources):
            raise RuntimeError(f"invalid split index {split_index}")
        payload = read_exact(sources[split_index], offset, count)
        source_hash.update(payload)
        source_bytes += len(payload)
        spans.append({"split_index": split_index, "offset": offset, "count": count})
    dump_hash = hashlib.sha256(dump.read_bytes()).hexdigest()
    source_digest = source_hash.hexdigest()
    distinct_splits = sorted({item["split_index"] for item in spans})
    checks = {
        "command": result["exit_code"] == 0,
        "byte_count": source_bytes == observed["bytes"] == dump.stat().st_size,
        "sha256_exact": source_digest == dump_hash,
        "source_spans": len(spans) == 3 and all(item["count"] > 0 for item in spans),
        "split_cross_file": len(distinct_splits) >= 2 if kind == "split" else distinct_splits == [0],
    }
    assert set(checks) == CHECK_KEYS
    return {"representation": representation, "kind": kind, "command": command,
            "bundle": {"layer": observed["layer"], "expert": observed["expert"],
                       "bytes": observed["bytes"], "spans": spans,
                       "distinct_split_indices": distinct_splits,
                       "source_sha256": source_digest, "cold_dump_sha256": dump_hash},
            "checks": checks,
            "output_digests": {"stdout": result["stdout_sha256"], "stderr": result["stderr_sha256"]}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--split-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    executable = (args.cuda_build/"bin/phase6-bundle-integrity-probe").resolve()
    cases = []
    with tempfile.TemporaryDirectory(prefix="phase6-bundle-integrity-") as temporary:
        directory = Path(temporary)
        for representation, original in (("f16", args.f16.resolve()), ("mxfp4", args.mxfp4.resolve())):
            matches = sorted(args.split_dir.glob(
                f"*{'F16' if representation == 'f16' else 'MXFP4'}-split.gguf-*.gguf"))
            if len(matches) != 218:
                raise RuntimeError(f"{representation}: expected 218 split files")
            first = matches[0].resolve()
            cases.append(capture(executable, original, [original], representation, "original", root, directory))
            cases.append(capture(executable, first, split_paths(first), representation, "split", root, directory))
    status = all(set(case["checks"]) == CHECK_KEYS and all(case["checks"].values()) for case in cases)
    write(args.output, {"schema_version": "phase6-bundle-integrity-v1",
                        "status": "pass" if status else "fail", "cases": cases})
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

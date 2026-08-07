#!/usr/bin/env python3
"""Materialize and verify the deterministic layer-parity dual-NVMe corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
from common import seek_extent_coverage  # noqa: E402
from plan import PlanOperation, build_plan, encode_plan  # noqa: E402
from qualify_harness import sha256_file  # noqa: E402

EXPECTED_SINK = "205a762e95ada0c9d731c7d47ef41adda5a4ef9fbd8ea650eb91a74b9207956d"
CHUNK_BYTES = 4 * 1024 * 1024


def copy_operation(source_fd: int, target_fd: int, operation: PlanOperation) -> str:
    digest = hashlib.sha256()
    cursor = 0
    while cursor < operation.length:
        count = min(CHUNK_BYTES, operation.length - cursor)
        block = os.pread(source_fd, count, operation.offset + cursor)
        if len(block) != count:
            raise ValueError("short source read during dual corpus materialization")
        written = os.pwrite(target_fd, block, operation.offset + cursor)
        if written != count:
            raise ValueError("short target write during dual corpus materialization")
        digest.update(block)
        cursor += count
    return digest.hexdigest()


def verify_operation(fd: int, operation: PlanOperation) -> bytes:
    digest = hashlib.sha256()
    cursor = 0
    while cursor < operation.length:
        count = min(CHUNK_BYTES, operation.length - cursor)
        block = os.pread(fd, count, operation.offset + cursor)
        if len(block) != count:
            raise ValueError("short dual corpus verification read")
        digest.update(block)
        cursor += count
    observed = digest.digest()
    if observed.hex() != operation.sha256:
        raise ValueError(f"dual corpus checksum mismatch for operation {operation.ordinal}")
    return observed


def file_metadata(path: Path, useful_bytes: int, records: int) -> dict[str, object]:
    stat = path.stat()
    return {
        "path": str(path),
        "logical_size": stat.st_size,
        "allocated_bytes": stat.st_blocks * 512,
        "planned_useful_bytes": useful_bytes,
        "record_count": records,
        "device": {"major": os.major(stat.st_dev), "minor": os.minor(stat.st_dev)},
        "block_stat_path": f"/sys/dev/block/{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}/stat",
        "copy_method": "userspace pread/pwrite; no reflink/clone",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--namespace-0-root", type=Path, required=True)
    parser.add_argument("--namespace-1-root", type=Path, required=True)
    parser.add_argument("--plan-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = args.corpus.resolve()
    source_path = corpus / "layout-a/projection-spans.bin"
    operations = build_plan(corpus, "A", "COLD_SPREAD", 0, "LOGICAL_SELECTED")
    roots = (args.namespace_0_root.resolve(), args.namespace_1_root.resolve())
    paths = tuple(root / "projection-spans.bin" for root in roots)
    if any(path.exists() for path in paths):
        raise FileExistsError("dual corpus targets already exist; refusing to overwrite")
    for root in roots:
        root.mkdir(parents=True, exist_ok=False)
    logical_size = source_path.stat().st_size
    target_fds: list[int] = []
    source_fd = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        for path in paths:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o644)
            os.ftruncate(fd, logical_size)
            target_fds.append(fd)
        for operation in operations:
            drive = operation.layer % 2
            os.posix_fallocate(target_fds[drive], operation.offset, operation.length)
            observed = copy_operation(source_fd, target_fds[drive], operation)
            if observed != operation.sha256:
                raise ValueError(f"source checksum mismatch for operation {operation.ordinal}")
        for fd in target_fds:
            os.fdatasync(fd)
    finally:
        os.close(source_fd)
        for fd in target_fds:
            os.close(fd)

    target_fds = [os.open(path, os.O_RDONLY | os.O_CLOEXEC) for path in paths]
    sink = hashlib.sha256()
    try:
        for operation in operations:
            digest = verify_operation(target_fds[operation.layer % 2], operation)
            sink.update(operation.ordinal.to_bytes(8, "little"))
            sink.update(digest)
    finally:
        for fd in target_fds:
            os.close(fd)
    if sink.hexdigest() != EXPECTED_SINK:
        raise ValueError("dual corpus canonical sink mismatch")

    per_drive_operations = [
        [operation for operation in operations if operation.layer % 2 == drive]
        for drive in (0, 1)
    ]
    extents = [
        seek_extent_coverage(paths[drive], ((operation.offset, operation.length) for operation in per_drive_operations[drive]))
        for drive in (0, 1)
    ]
    if not all(proof.get("complete") for proof in extents):
        raise ValueError("dual corpus physical-backing proof failed")
    metadata = [
        file_metadata(
            paths[drive], sum(operation.length for operation in per_drive_operations[drive]),
            len(per_drive_operations[drive]),
        )
        for drive in (0, 1)
    ]
    if metadata[0]["device"] == metadata[1]["device"]:
        raise ValueError("dual corpus targets are not independent block devices")
    for item in metadata:
        if int(item["allocated_bytes"]) < int(item["planned_useful_bytes"]):
            raise ValueError("dual corpus allocated bytes are smaller than planned bytes")

    single_plan = operations
    dual_plan = [
        PlanOperation(
            ordinal=operation.ordinal,
            source=operation.layer % 2,
            path=paths[operation.layer % 2],
            offset=operation.offset,
            length=operation.length,
            sha256=operation.sha256,
            layer=operation.layer,
            expert=operation.expert,
        )
        for operation in operations
    ]
    plan_root = args.plan_root.resolve()
    plan_root.mkdir(parents=True, exist_ok=True)
    single_path = plan_root / "single-namespace.tsv"
    dual_path = plan_root / "dual-namespace.tsv"
    single_path.write_bytes(encode_plan(single_plan))
    dual_path.write_bytes(encode_plan(dual_plan))
    document = {
        "schema_version": "phase12-nvme-dual-corpus-v1",
        "status": "PASS",
        "mapping": "layer % 2 selects namespace 0/1",
        "source": {
            "path": str(source_path), "logical_size": logical_size,
            "corpus_generation_sha256": sha256_file(corpus / "generation.json"),
        },
        "records": len(operations),
        "useful_bytes": sum(operation.length for operation in operations),
        "checksum_sink_sha256": sink.hexdigest(),
        "namespaces": [
            {**metadata[drive], "extent_proof": extents[drive]}
            for drive in (0, 1)
        ],
        "plans": {
            "single_namespace": {"path": str(single_path), "sha256": sha256_file(single_path)},
            "dual_namespace": {"path": str(dual_path), "sha256": sha256_file(dual_path)},
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": "PASS", "records": document["records"], "useful_bytes": document["useful_bytes"],
        "devices": [item["device"] for item in metadata], "sink": sink.hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

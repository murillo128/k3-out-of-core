#!/usr/bin/env python3
"""Independently verify a pinned Kimi-K3 text snapshot against publisher metadata."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

MODEL_REPOSITORY = "moonshotai/Kimi-K3"
MODEL_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
VISION_SHARDS = {
    "model-00095-of-000096.safetensors",
    "model-00096-of-000096.safetensors",
}


def publisher_metadata() -> dict[str, object]:
    url = (
        "https://huggingface.co/api/models/"
        f"{MODEL_REPOSITORY}/revision/{MODEL_REVISION}?blobs=true"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        document = json.load(response)
    if document["sha"] != MODEL_REVISION:
        raise ValueError("publisher API resolved a different revision")
    return document


def digest_file(path: Path, expected: dict[str, object]) -> dict[str, object]:
    started = time.monotonic()
    size = path.stat().st_size
    if size != int(expected["size"]):
        return {"path": path.name, "size": size, "status": "FAIL", "failure": "size mismatch"}
    lfs = expected.get("lfs")
    if lfs:
        algorithm = "sha256"
        digest = hashlib.sha256()
        expected_digest = str(lfs["sha256"])
    else:
        algorithm = "git-blob-sha1"
        digest = hashlib.sha1()
        digest.update(f"blob {size}\0".encode())
        expected_digest = str(expected["blobId"])
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    actual_digest = digest.hexdigest()
    return {
        "path": str(path),
        "size": size,
        "algorithm": algorithm,
        "expected_digest": expected_digest,
        "actual_digest": actual_digest,
        "status": "PASS" if actual_digest == expected_digest else "FAIL",
        "elapsed_seconds": time.monotonic() - started,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--primary-transfer-seconds", type=float, required=True)
    parser.add_argument("--resume-transfer-seconds", type=float, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    metadata = publisher_metadata()
    expected = {
        item["rfilename"]: item
        for item in metadata["siblings"]
        if item["rfilename"] not in VISION_SHARDS
    }
    missing = sorted(name for name in expected if not (snapshot / name).is_file())
    unexpected_vision = sorted(name for name in VISION_SHARDS if (snapshot / name).exists())

    started = time.monotonic()
    results: list[dict[str, object]] = []
    if not missing and not unexpected_vision:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(digest_file, snapshot / name, item): name
                for name, item in expected.items()
            }
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results.append(result)
                print(f"{result['status']} {futures[future]}", flush=True)
    elapsed = time.monotonic() - started
    results.sort(key=lambda item: str(item["path"]))
    failures = [item for item in results if item["status"] != "PASS"]
    verified_bytes = sum(int(item["size"]) for item in results if item["status"] == "PASS")
    manifest_digest = hashlib.sha256()
    for item in results:
        manifest_digest.update(
            (f"{Path(str(item['path'])).name}\0{item['size']}\0{item.get('algorithm', '')}\0"
             f"{item.get('actual_digest', '')}\n").encode()
        )
    passed = not missing and not unexpected_vision and not failures and len(results) == len(expected)
    total_transfer_seconds = args.primary_transfer_seconds + args.resume_transfer_seconds
    document = {
        "schema_version": "phase12-nvme-colibri-snapshot-verification-v1",
        "status": "PASS" if passed else "FAIL",
        "disposition": "accepted" if passed else "blocked",
        "repository": MODEL_REPOSITORY,
        "revision": MODEL_REVISION,
        "snapshot": str(snapshot),
        "text_only": True,
        "excluded_vision_shards": sorted(VISION_SHARDS),
        "expected_file_count": len(expected),
        "verified_file_count": len(results),
        "verified_bytes": verified_bytes,
        "publisher_manifest_sha256": manifest_digest.hexdigest(),
        "transfer": {
            "primary_high_performance_seconds": args.primary_transfer_seconds,
            "primary_disposition": "resumable transport stalled after 93 of 94 text shards",
            "standard_http_resume_seconds": args.resume_transfer_seconds,
            "resume_start_byte": 11_133_591_820,
            "total_wall_seconds": total_transfer_seconds,
            "effective_verified_bytes_per_second": verified_bytes / total_transfer_seconds,
            "download_ceiling_seconds": 14_400,
            "within_ceiling": total_transfer_seconds <= 14_400,
        },
        "verification": {
            "workers": args.workers,
            "elapsed_seconds": elapsed,
            "bytes_per_second": verified_bytes / elapsed if elapsed else 0.0,
            "lfs_files": sum(1 for item in results if item.get("algorithm") == "sha256"),
            "git_blob_files": sum(1 for item in results if item.get("algorithm") == "git-blob-sha1"),
            "files": results,
        },
        "missing_files": missing,
        "unexpected_vision_files": unexpected_vision,
        "failures": failures,
        "interpretation": (
            "the complete pinned text snapshot is byte-identical to publisher metadata"
            if passed else "the snapshot is not qualified for full-model execution"
        ),
        "next_action": (
            "run the direct-source Colibrì full-model reference with K3_TOPP=0"
            if passed else "publish the exact snapshot blocker"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"], "verified_files": len(results),
        "verified_bytes": verified_bytes, "elapsed_seconds": elapsed,
        "publisher_manifest_sha256": document["publisher_manifest_sha256"],
        "failures": len(failures), "missing": len(missing),
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

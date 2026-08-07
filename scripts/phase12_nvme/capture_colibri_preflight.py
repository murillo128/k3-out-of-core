#!/usr/bin/env python3
"""Capture the bounded Colibrì/Kimi-K3 full-model execution preflight."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

COLIBRI_COMMIT = "b085b48888a88d9a1c00b151a9979774b72cdbfd"
MODEL_REPOSITORY = "moonshotai/Kimi-K3"
MODEL_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
DOWNLOAD_CEILING_SECONDS = 4 * 60 * 60
RUN_CEILING_SECONDS = 2 * 60 * 60
MEMORY_PLAN_BYTES = 64 * (1 << 30)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def command(*parts: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(parts, cwd=cwd, text=True).strip()


def model_metadata() -> dict[str, object]:
    url = (
        "https://huggingface.co/api/models/"
        f"{MODEL_REPOSITORY}/revision/{MODEL_REVISION}?blobs=true"
    )
    with urllib.request.urlopen(url, timeout=60) as response:
        document = json.load(response)
    if document["sha"] != MODEL_REVISION:
        raise ValueError("Hugging Face resolved a different model revision")
    return document


def memory_bytes() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        values[key] = int(value.strip().split()[0]) * 1024
    return values["MemTotal"], values["MemAvailable"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--colibri-source", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--sample-file", type=Path, required=True)
    parser.add_argument("--sample-seconds", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = args.colibri_source.resolve()
    model_dir = args.model_dir.resolve()
    # Preserve the virtual-environment launcher instead of resolving its
    # symlink to the system interpreter (which would drop the venv packages).
    python = args.python.absolute()
    sample = args.sample_file.resolve()
    if command("git", "rev-parse", "HEAD", cwd=source) != COLIBRI_COMMIT:
        raise ValueError("Colibrì source revision mismatch")
    if command("git", "status", "--porcelain", cwd=source):
        raise ValueError("Colibrì source is dirty")

    build = subprocess.run(
        ["make", "-C", str(source / "c"), "kimi_k3", "-j16"],
        text=True, capture_output=True,
    )
    if build.returncode:
        raise RuntimeError(build.stdout + build.stderr)
    test_binary = source / "c/tests/test_tok_kimi"
    build_test = subprocess.run(
        ["make", "-C", str(source / "c"), str(test_binary.relative_to(source / "c")), "-j16"],
        text=True, capture_output=True,
    )
    if build_test.returncode:
        raise RuntimeError(build_test.stdout + build_test.stderr)
    tokenizer_test = subprocess.run(
        [str(python), str(source / "c/tools/k3_tokenizer.py"), str(model_dir),
         "--ctest", str(test_binary)],
        text=True, capture_output=True,
    )
    if tokenizer_test.returncode or "ctest: OK" not in tokenizer_test.stdout:
        raise RuntimeError(tokenizer_test.stdout + tokenizer_test.stderr)

    metadata = model_metadata()
    siblings = {item["rfilename"]: item for item in metadata["siblings"]}
    shard_items = {
        name: item for name, item in siblings.items()
        if name.startswith("model-") and name.endswith(".safetensors")
    }
    text_shards = {
        name: item for name, item in shard_items.items()
        if int(name[6:11]) <= 94
    }
    vision_shards = {
        name: item for name, item in shard_items.items()
        if int(name[6:11]) > 94
    }
    metadata_bytes = sum(int(item["size"]) for name, item in siblings.items() if name not in shard_items)
    text_shard_bytes = sum(int(item["size"]) for item in text_shards.values())
    text_snapshot_bytes = text_shard_bytes + metadata_bytes

    sample_metadata = siblings[sample.name]
    sample_hash = sha256_file(sample)
    if sample.stat().st_size != int(sample_metadata["size"]):
        raise ValueError("sample size mismatch")
    if sample_hash != sample_metadata["lfs"]["sha256"]:
        raise ValueError("sample checksum mismatch")
    measured_bytes_per_second = sample.stat().st_size / args.sample_seconds
    estimated_download_seconds = text_snapshot_bytes / measured_bytes_per_second
    conservative_download_seconds = 2.0 * estimated_download_seconds

    disk = shutil.disk_usage(model_dir)
    total_memory, available_memory = memory_bytes()
    memory_ceiling = int(total_memory * 0.8)
    disk_requirement = int(text_snapshot_bytes * 1.05)
    gates = {
        "source_revision": command("git", "rev-parse", "HEAD", cwd=source) == COLIBRI_COMMIT,
        "source_clean": not bool(command("git", "status", "--porcelain", cwd=source)),
        "model_public_ungated": not bool(metadata.get("private")) and not bool(metadata.get("gated")),
        "sample_checksum": True,
        "download_time": conservative_download_seconds <= DOWNLOAD_CEILING_SECONDS,
        "disk_capacity": disk.free >= disk_requirement,
        "memory_capacity": MEMORY_PLAN_BYTES <= memory_ceiling and MEMORY_PLAN_BYTES <= available_memory,
        "native_build": (source / "c/kimi_k3").is_file(),
        "tokenizer_equivalence": True,
    }
    accepted = all(gates.values())
    binary = source / "c/kimi_k3"
    license_path = source / "LICENSE"
    result = {
        "schema_version": "phase12-nvme-colibri-preflight-v1",
        "status": "PASS" if accepted else "FAIL",
        "disposition": "accepted" if accepted else "blocked",
        "scope": "same-machine Colibrì Kimi-K3 text-only full-model reference",
        "colibri": {
            "repository": command("git", "remote", "get-url", "origin", cwd=source),
            "commit": COLIBRI_COMMIT,
            "version": "v1.4.0 reviewed commit",
            "license": "Apache-2.0",
            "license_sha256": sha256_file(license_path),
            "binary": {"path": str(binary), "size": binary.stat().st_size, "sha256": sha256_file(binary)},
        },
        "model": {
            "repository": MODEL_REPOSITORY,
            "revision": MODEL_REVISION,
            "private": bool(metadata.get("private")),
            "gated": bool(metadata.get("gated")),
            "file_count": len(siblings),
            "text_shard_count": len(text_shards),
            "vision_shard_count_excluded": len(vision_shards),
            "text_shard_bytes": text_shard_bytes,
            "metadata_bytes": metadata_bytes,
            "text_snapshot_bytes": text_snapshot_bytes,
            "source_expert_format": "native MXFP4; no expert re-encode",
        },
        "sample_transfer": {
            "file": sample.name,
            "bytes": sample.stat().st_size,
            "seconds": args.sample_seconds,
            "bytes_per_second": measured_bytes_per_second,
            "sha256": sample_hash,
            "estimated_full_download_seconds": estimated_download_seconds,
            "conservative_slowdown_factor": 2.0,
            "conservative_full_download_seconds": conservative_download_seconds,
        },
        "declared_ceilings": {
            "download_seconds": DOWNLOAD_CEILING_SECONDS,
            "full_reference_run_seconds": RUN_CEILING_SECONDS,
            "disk_requirement_factor": 1.05,
            "memory_bytes": memory_ceiling,
        },
        "host_capacity": {
            "target_filesystem": os.statvfs(model_dir).f_fsid,
            "disk_total_bytes": disk.total,
            "disk_free_bytes": disk.free,
            "disk_required_bytes": disk_requirement,
            "memory_total_bytes": total_memory,
            "memory_available_bytes": available_memory,
            "planned_colibri_memory_bytes": MEMORY_PLAN_BYTES,
        },
        "validation": {
            "native_build_command": ["make", "-C", "c", "kimi_k3", "-j16"],
            "native_build_status": "PASS",
            "tokenizer_test_status": "PASS",
            "tokenizer_test_fact": "tok.h matches tiktoken on all upstream Kimi cases",
        },
        "gates": gates,
        "interpretation": (
            "declared network, disk, RAM, runtime, source, access, build, and tokenizer gates permit the full-model reference"
            if accepted else
            "one or more declared gates block the full-model reference"
        ),
        "next_action": (
            "download the pinned text-only snapshot and run the direct-source Colibrì reference with K3_TOPP=0"
            if accepted else "publish the exact capability blocker"
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": result["status"], "disposition": result["disposition"],
        "text_snapshot_bytes": text_snapshot_bytes,
        "conservative_full_download_seconds": conservative_download_seconds,
        "gates": gates,
    }, sort_keys=True))
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())

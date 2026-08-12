#!/usr/bin/env python3
"""Aggregate /proc/<pid>/smaps snapshots from a residency-attribution cell."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
from collections import defaultdict
from typing import Any


HEADER = re.compile(
    r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)(?:\s+(.*))?$"
)
FIELDS = (
    "Size", "Rss", "Pss", "Shared_Clean", "Shared_Dirty", "Private_Clean",
    "Private_Dirty", "Referenced", "Anonymous", "AnonHugePages", "FilePmdMapped",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("cell_root", nargs="?")
    parser.add_argument("--cache-actual-bytes", type=int)
    parser.add_argument("--output")
    parser.add_argument("--index-tree")
    parser.add_argument("--index-output")
    args = parser.parse_args()
    if args.index_tree is not None:
        if args.index_output is None:
            parser.error("--index-tree requires --index-output")
    elif args.cell_root is None or args.cache_actual_bytes is None or args.output is None:
        parser.error("residency aggregation requires cell_root, cache bytes, and output")
    return args


def parse_smaps(path: pathlib.Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in path.read_text().splitlines():
        match = HEADER.match(line)
        if match:
            current = {
                "start": match.group(1),
                "end": match.group(2),
                "perms": match.group(3),
                "offset": match.group(4),
                "device": match.group(5),
                "inode": int(match.group(6)),
                "path": match.group(7) or "",
                "fields_kib": {},
            }
            result.append(current)
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in FIELDS:
            pieces = value.split()
            if pieces:
                current["fields_kib"][key] = int(pieces[0])
    return result


def base_category(vma: dict[str, Any]) -> str:
    path = vma["path"]
    if path.endswith(".gguf") and "kimi-k3-bf16-" in path:
        return "model_gguf"
    if not path or path.startswith("["):
        if path == "[heap]":
            return "heap"
        if path.startswith("[stack"):
            return "thread_stacks"
        if path in ("[vdso]", "[vvar]", "[vsyscall]"):
            return "kernel_special"
        return "anonymous_runtime"
    if "/mnt/nvme1/issue89/build-max-native/" in path:
        return "project_binary_or_library"
    if ".so" in path or path.startswith("/usr/lib") or path.startswith("/lib"):
        return "shared_libraries"
    return "other_file"


def add_fields(target: dict[str, int], source: dict[str, int]) -> None:
    for field in FIELDS:
        target[field] += source.get(field, 0)


def aggregate(path: pathlib.Path, cache_actual_kib: int) -> dict[str, Any]:
    vmas = parse_smaps(path)
    anonymous = [vma for vma in vmas if base_category(vma) == "anonymous_runtime"]
    cold = max(anonymous, key=lambda vma: vma["fields_kib"].get("Rss", 0), default=None)
    categories: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    shards: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for vma in vmas:
        category = base_category(vma)
        if cold is not None and vma is cold:
            category = "cold_cache_candidate"
        add_fields(categories[category], vma["fields_kib"])
        if category == "model_gguf":
            add_fields(shards[pathlib.Path(vma["path"]).name], vma["fields_kib"])
    cold_summary: dict[str, Any] | None = None
    if cold is not None:
        cold_summary = {
            "mapping": f"{cold['start']}-{cold['end']}",
            "path": cold["path"],
            "perms": cold["perms"],
            "fields_kib": cold["fields_kib"],
            "configured_cache_actual_kib": cache_actual_kib,
            "size_minus_cache_kib": cold["fields_kib"].get("Size", 0) - cache_actual_kib,
            "rss_minus_cache_kib": cold["fields_kib"].get("Rss", 0) - cache_actual_kib,
            "identification_basis": "largest anonymous VMA; compare exact configured cache allocation",
        }
    return {
        "smaps": str(path),
        "vma_count": len(vmas),
        "cold_cache_candidate": cold_summary,
        "categories_kib": {key: dict(value) for key, value in sorted(categories.items())},
        "model_shards_kib": {key: dict(value) for key, value in sorted(shards.items())},
    }


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def index_tree(root: pathlib.Path, output: pathlib.Path) -> None:
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.resolve() == output.resolve():
            continue
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    canonical = "".join(
        f"{entry['sha256']}  {entry['path']}\n" for entry in entries
    ).encode()
    value = {
        "schema_version": "phase13-6p-residency-raw-index-v1",
        "root": str(root),
        "file_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
        "canonical_tree_sha256": hashlib.sha256(canonical).hexdigest(),
        "artifacts": entries,
    }
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)


def main() -> int:
    args = parse_args()
    if args.index_tree is not None:
        index_tree(pathlib.Path(args.index_tree).resolve(), pathlib.Path(args.index_output).resolve())
        return 0
    cell_root = pathlib.Path(args.cell_root).resolve()
    output = pathlib.Path(args.output).resolve()
    cache_actual_kib = args.cache_actual_bytes // 1024
    snapshots: list[dict[str, Any]] = []
    for directory in sorted((cell_root / "snapshots").iterdir()):
        smaps = directory / "smaps"
        metadata = directory / "snapshot.json"
        if not smaps.exists() or not metadata.exists():
            continue
        item = json.loads(metadata.read_text())
        item["residency"] = aggregate(smaps, cache_actual_kib)
        snapshots.append(item)
    value = {
        "schema_version": "phase13-6p-residency-aggregation-v1",
        "cell_root": str(cell_root),
        "cache_actual_bytes": args.cache_actual_bytes,
        "snapshots": sorted(snapshots, key=lambda item: item["elapsed_s"]),
    }
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify and smoke-test a downloaded Kimi K3 router tensor pack."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from router_pack import (
    PackError,
    assert_relative_members,
    load_json,
    sha256_file,
    static_smoke_test,
    validate_inventory,
    validate_payload_tree,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--assets-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--smoke-output", required=True, type=Path)
    return parser.parse_args()


def run_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackError(f"command failed: {' '.join(command)}: {exc}") from exc


def run_checked(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackError(f"command failed: {' '.join(command)}: {exc}") from exc


def validate_manifest(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != "kimi-k3-router-pack-manifest-v1":
        raise PackError("unsupported pack manifest schema")
    if manifest.get("pack_id") != config.get("pack_id"):
        raise PackError("pack ID differs between manifest and config")
    source = manifest.get("source_artifact", {})
    expected_source = config["source_artifact"]
    if source.get("identity_manifest_sha256") != expected_source["identity_manifest_sha256"]:
        raise PackError("source artifact identity differs between manifest and config")
    if source.get("file_count") != expected_source["file_count"]:
        raise PackError("source artifact file count differs between manifest and config")
    files = source.get("files")
    if not isinstance(files, list) or len(files) != expected_source["file_count"]:
        raise PackError("source split inventory is incomplete")
    if sum(int(item["size"]) for item in files) != expected_source["total_bytes"]:
        raise PackError("source split inventory byte total is inconsistent")
    if manifest.get("inventory", {}).get("tensor_count") != inventory.get("tensor_count"):
        raise PackError("manifest and inventory tensor counts differ")
    if manifest.get("inventory", {}).get("payload_bytes") != inventory.get("payload_bytes"):
        raise PackError("manifest and inventory payload bytes differ")
    release = manifest.get("release", {})
    if release.get("tag") != config["release"]["tag"]:
        raise PackError("release tag differs between manifest and config")
    assets = release.get("ordered_assets")
    if not isinstance(assets, list) or not assets:
        raise PackError("manifest has no ordered release assets")
    orders = [asset.get("order") for asset in assets]
    if orders != list(range(len(assets))):
        raise PackError("release asset order is not contiguous")
    filenames = [asset.get("filename") for asset in assets]
    if len(filenames) != len(set(filenames)):
        raise PackError("duplicate release asset filename")
    for asset in assets:
        digest = asset.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise PackError(f"invalid release asset SHA-256: {asset}")
        if int(asset.get("compressed_bytes", 0)) <= 0:
            raise PackError(f"invalid compressed asset size: {asset}")
        if int(asset.get("uncompressed_tar_bytes", 0)) <= 0:
            raise PackError(f"invalid uncompressed asset size: {asset}")
    return assets


def verify_and_extract_assets(
    assets: list[dict[str, Any]],
    records: list[dict[str, Any]],
    assets_dir: Path,
    work_dir: Path,
) -> list[dict[str, Any]]:
    expected_by_asset: dict[str, set[str]] = {asset["filename"]: set() for asset in assets}
    for record in records:
        asset = record.get("asset")
        if asset not in expected_by_asset:
            raise PackError(f"tensor references unknown asset: {asset}")
        expected_by_asset[asset].add(record["payload_path"])

    verified = []
    seen_members: set[str] = set()
    for asset in assets:
        filename = asset["filename"]
        path = assets_dir / filename
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise PackError(f"release asset is unavailable: {path}: {exc}") from exc
        if size != asset["compressed_bytes"]:
            raise PackError(f"release asset size mismatch for {filename}: {size}")
        digest = sha256_file(path)
        if digest != asset["sha256"]:
            raise PackError(f"release asset SHA-256 mismatch for {filename}: {digest}")
        listing = [line for line in run_output(["tar", "--zstd", "-tf", os.fspath(path)]).splitlines() if line]
        assert_relative_members(listing)
        members = {member.rstrip("/") for member in listing if member.rstrip("/") != "tensors"}
        if members != expected_by_asset[filename]:
            raise PackError(
                f"archive member mismatch for {filename}: "
                f"missing={sorted(expected_by_asset[filename] - members)} "
                f"unexpected={sorted(members - expected_by_asset[filename])}"
            )
        duplicates = seen_members & members
        if duplicates:
            raise PackError(f"payload members occur in multiple assets: {sorted(duplicates)}")
        seen_members |= members
        run_checked(["tar", "--zstd", "-xf", os.fspath(path), "-C", os.fspath(work_dir)])
        verified.append({"filename": filename, "compressed_bytes": size, "sha256": digest})
    return verified


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest.resolve())
    inventory = load_json(args.inventory.resolve())
    config = load_json(args.config.resolve())
    records = validate_inventory(inventory, config)
    assets = validate_manifest(manifest, inventory, config)

    work_dir = args.work_dir.resolve()
    smoke_output = args.smoke_output.resolve()
    if work_dir.exists():
        raise PackError(f"work directory already exists; fresh directory required: {work_dir}")
    if smoke_output.exists():
        raise PackError(f"smoke output already exists; refusing to overwrite: {smoke_output}")
    work_dir.mkdir(parents=True)

    asset_validation = verify_and_extract_assets(
        assets,
        records,
        args.assets_dir.resolve(),
        work_dir,
    )
    payload_validation = validate_payload_tree(work_dir, records)
    smoke = static_smoke_test(
        work_dir,
        records,
        config["smoke_test"]["layers"],
        *config["smoke_test"]["experts"],
    )
    smoke.update(
        {
            "verification_scope": "fresh extracted release assets; full source model unavailable",
            "asset_validation": asset_validation,
            "payload_validation": payload_validation,
            "source_artifact_identity_manifest_sha256": manifest["source_artifact"][
                "identity_manifest_sha256"
            ],
        }
    )
    write_json(smoke_output, smoke)
    print(json.dumps(smoke, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

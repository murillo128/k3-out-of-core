#!/usr/bin/env python3
"""Extract the immutable Kimi K3 static router tensors from the qualified GGUF."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from router_pack import (
    PackError,
    ROLE_ORDER,
    expected_tensor_spec,
    fsync_file,
    load_json,
    parse_router_tensor_name,
    payload_name,
    sha256_file,
    static_smoke_test,
    validate_payload_tree,
    validate_tensor_records,
    write_checksums,
    write_json,
)


TOOL_VERSION = "1"
CHUNK_BYTES = 16 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-identity-manifest", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--asset-dir", required=True, type=Path)
    parser.add_argument("--payload-staging-dir", required=True, type=Path)
    parser.add_argument("--project-root", default=Path(__file__).resolve().parents[2], type=Path)
    parser.add_argument("--gguf-python-root", type=Path)
    parser.add_argument("--hash-workers", type=int, default=4)
    args = parser.parse_args()
    if args.hash_workers < 1 or args.hash_workers > 16:
        parser.error("--hash-workers must be between 1 and 16")
    return args


def git_output(repo: Path, *arguments: str, binary: bool = False) -> str | bytes:
    command = ["git", "-C", os.fspath(repo), *arguments]
    try:
        return subprocess.check_output(command, text=not binary)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackError(f"git command failed: {' '.join(command)}: {exc}") from exc


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != "kimi-k3-router-pack-extraction-config-v1":
        raise PackError("unsupported extraction config schema")
    router = config.get("router", {})
    required = {
        "architecture": "kimi-k3",
        "block_count": 93,
        "leading_dense_block_count": 1,
        "first_routed_layer": 1,
        "routed_layer_count": 92,
        "experts_per_layer": 896,
        "selected_experts": 16,
        "hidden_dimension": 7168,
        "router_output_dimension": 896,
        "projection_dtype": "F32",
        "correction_dtype": "F32",
        "gating_function_code": 2,
        "gating_function": "sigmoid",
        "expert_weights_norm": True,
        "expert_weights_scale": 1.0,
    }
    for key, expected in required.items():
        if router.get(key) != expected:
            raise PackError(f"unexpected extraction config {key}: {router.get(key)!r}")
    if router["router_output_dimension"] != router["experts_per_layer"]:
        raise PackError("router output dimension does not match expert count")


def validate_semantics_sources(project_root: Path, config: dict[str, Any]) -> list[dict[str, Any]]:
    nested = project_root / "llama.cpp"
    revision = config["accepted_issue73_revisions"]["nested_runtime"]
    if git_output(nested, "cat-file", "-t", revision).strip() != "commit":
        raise PackError(f"nested semantics revision is unavailable: {revision}")
    verified = []
    for source in config["semantics_sources"]:
        path = source["path"]
        data = git_output(nested, "show", f"{revision}:{path}", binary=True)
        assert isinstance(data, bytes)
        digest = hashlib.sha256(data).hexdigest()
        if digest != source["sha256"]:
            raise PackError(f"semantics source hash mismatch for {path}: {digest}")
        verified.append(
            {
                "path": path,
                "sha256": digest,
                "symbols": source["symbols"],
                "nested_revision": revision,
            }
        )
    return verified


def validate_artifact_identity(
    identity_path: Path,
    config: dict[str, Any],
    workers: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected_manifest_hash = config["source_artifact"]["identity_manifest_sha256"]
    actual_manifest_hash = sha256_file(identity_path)
    if actual_manifest_hash != expected_manifest_hash:
        raise PackError(
            f"artifact identity manifest hash mismatch: {actual_manifest_hash} != {expected_manifest_hash}"
        )
    identity = load_json(identity_path)
    artifact = identity.get("artifact")
    if not isinstance(artifact, dict):
        raise PackError("artifact identity manifest has no artifact object")
    expected = config["source_artifact"]
    for identity_key, config_key in (
        ("repository", "repository"),
        ("revision", "revision"),
        ("variant", "variant"),
        ("total_bytes", "total_bytes"),
    ):
        if artifact.get(identity_key) != expected[config_key]:
            raise PackError(f"artifact identity mismatch for {identity_key}")
    files = artifact.get("files")
    if not isinstance(files, list) or len(files) != expected["file_count"]:
        raise PackError("artifact split count mismatch")
    if sum(int(item["size"]) for item in files) != expected["total_bytes"]:
        raise PackError("artifact split sizes do not sum to expected total")

    def verify_file(item: dict[str, Any]) -> dict[str, Any]:
        path = Path(item["path"])
        if path.name != item["name"]:
            raise PackError(f"artifact path/name mismatch: {path}")
        try:
            stat = path.stat()
        except OSError as exc:
            raise PackError(f"source artifact is unavailable: {path}: {exc}") from exc
        if stat.st_size != item["size"]:
            raise PackError(f"source artifact size mismatch: {path}")
        digest = sha256_file(path)
        if digest != item["sha256"]:
            raise PackError(f"source artifact SHA-256 mismatch: {path}: {digest}")
        return {
            "name": item["name"],
            "path": path,
            "size": int(item["size"]),
            "sha256": digest,
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        verified = list(executor.map(verify_file, files))
    return identity, verified


def field_value(reader: Any, key: str) -> Any:
    field = reader.get_field(key)
    if field is None:
        raise PackError(f"GGUF metadata key is missing: {key}")
    value = field.contents()
    if hasattr(value, "item"):
        value = value.item()
    return value


def validate_gguf_metadata(reader: Any, config: dict[str, Any], split_count: int) -> dict[str, Any]:
    router = config["router"]
    expected = {
        "general.architecture": router["architecture"],
        "general.name": "moonshotai/Kimi-K3",
        "general.finetune": config["source_artifact"]["revision"],
        "kimi-k3.block_count": router["block_count"],
        "kimi-k3.embedding_length": router["hidden_dimension"],
        "kimi-k3.expert_count": router["experts_per_layer"],
        "kimi-k3.expert_used_count": router["selected_experts"],
        "kimi-k3.expert_gating_func": router["gating_function_code"],
        "kimi-k3.leading_dense_block_count": router["leading_dense_block_count"],
        "kimi-k3.expert_weights_scale": router["expert_weights_scale"],
        "kimi-k3.expert_weights_norm": router["expert_weights_norm"],
        "kimi-k3.expert_latent_length": router["expert_latent_dimension"],
        "split.count": split_count,
    }
    observed = {}
    for key, wanted in expected.items():
        value = field_value(reader, key)
        if value != wanted:
            raise PackError(f"GGUF metadata mismatch for {key}: {value!r} != {wanted!r}")
        observed[key] = value
    if str(reader.endianess.name).lower() != "little":
        raise PackError("only the qualified little-endian GGUF artifact is accepted")
    return observed


def scan_tensors(
    verified_files: list[dict[str, Any]],
    config: dict[str, Any],
    gguf_python_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    sys.path.insert(0, os.fspath(gguf_python_root))
    try:
        from gguf import GGUFReader  # type: ignore
    except ImportError as exc:
        raise PackError(f"cannot import GGUFReader from {gguf_python_root}: {exc}") from exc

    records = []
    metadata_reference = None
    split_count = len(verified_files)
    split_total_tensor_count = None
    for expected_index, source in enumerate(verified_files):
        reader = GGUFReader(source["path"])
        split_index = int(field_value(reader, "split.no"))
        if split_index != expected_index:
            raise PackError(
                f"GGUF split index mismatch for {source['name']}: {split_index} != {expected_index}"
            )
        if int(field_value(reader, "split.count")) != split_count:
            raise PackError(f"GGUF split count mismatch for {source['name']}")
        if expected_index == 0:
            metadata_reference = validate_gguf_metadata(reader, config, split_count)
            split_total_tensor_count = int(field_value(reader, "split.tensors.count"))
        elif any(
            reader.get_field(key) is not None
            for key in (
                "general.architecture",
                "general.name",
                "general.finetune",
                "kimi-k3.block_count",
            )
        ):
            raise PackError(
                f"unexpected partial model metadata occurs outside the authoritative first split: "
                f"{source['name']}"
            )
        if int(field_value(reader, "split.tensors.count")) != split_total_tensor_count:
            raise PackError("GGUF total tensor count differs across splits")

        for tensor in reader.tensors:
            if "ffn_gate_inp" not in tensor.name and "exp_probs_b" not in tensor.name:
                continue
            layer, role = parse_router_tensor_name(tensor.name)
            expected = expected_tensor_spec(config, layer, role)
            record = {
                "layer": layer,
                "source_tensor_name": tensor.name,
                "semantic_role": role,
                "shape": [int(value) for value in tensor.shape],
                "dtype": tensor.tensor_type.name,
                "byte_length": int(tensor.n_bytes),
                "source_file": source["name"],
                "source_split": {"index": split_index, "number": split_index + 1, "count": split_count},
                "source_range": {
                    "offset": int(tensor.data_offset),
                    "end_exclusive": int(tensor.data_offset + tensor.n_bytes),
                },
                "payload_path": payload_name(layer, role),
                "sha256": "0" * 64,
            }
            if record["shape"] != expected["shape"]:
                raise PackError(f"shape mismatch for {tensor.name}: {record['shape']}")
            if record["dtype"] != expected["dtype"]:
                raise PackError(f"dtype mismatch for {tensor.name}: {record['dtype']}")
            if record["byte_length"] != expected["byte_length"]:
                raise PackError(f"byte-length mismatch for {tensor.name}")
            if record["source_range"]["end_exclusive"] > source["size"]:
                raise PackError(f"tensor extends beyond its source split: {tensor.name}")
            records.append(record)
        del reader

    placeholder_records = [dict(record, sha256="0" * 64) for record in records]
    # validate_tensor_records also checks digests, and a zero digest is syntactically valid here.
    validate_tensor_records(placeholder_records, config)
    records.sort(key=lambda item: (int(item["layer"]), ROLE_ORDER[item["semantic_role"]]))
    return records, {
        "metadata": metadata_reference,
        "split_tensor_count": split_total_tensor_count,
        "endianness": "little",
    }


def copy_exact_range(source: Path, start: int, length: int, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    source_fd = os.open(source, os.O_RDONLY)
    try:
        with destination.open("xb") as output:
            remaining = length
            offset = start
            while remaining:
                chunk = os.pread(source_fd, min(CHUNK_BYTES, remaining), offset)
                if not chunk:
                    raise PackError(
                        f"short source read from {source} at offset {offset}; {remaining} bytes remain"
                    )
                output.write(chunk)
                digest.update(chunk)
                offset += len(chunk)
                remaining -= len(chunk)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(source_fd)
    return digest.hexdigest()


def extract_tensors(
    records: list[dict[str, Any]],
    verified_files: list[dict[str, Any]],
    payload_root: Path,
) -> None:
    sources = {item["name"]: item["path"] for item in verified_files}
    for record in records:
        source = sources[record["source_file"]]
        source_range = record["source_range"]
        record["sha256"] = copy_exact_range(
            source,
            int(source_range["offset"]),
            int(record["byte_length"]),
            payload_root / record["payload_path"],
        )


def run_checked(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PackError(f"command failed: {' '.join(command)}: {exc}") from exc


def build_assets(
    records: list[dict[str, Any]],
    payload_root: Path,
    asset_dir: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    by_split: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for record in records:
        split = record["source_split"]
        by_split.setdefault((int(split["number"]), int(split["count"])), []).append(record)

    assets = []
    prefix = config["release"]["asset_prefix"]
    repository = config["release"]["repository"]
    tag = config["release"]["tag"]
    for (number, count), part_records in sorted(by_split.items()):
        filename = f"{prefix}-{number:05d}-of-{count:05d}.tar.zst"
        tar_path = asset_dir / filename.removesuffix(".zst")
        asset_path = asset_dir / filename
        members = sorted(str(record["payload_path"]) for record in part_records)
        command = [
            "tar",
            "--sort=name",
            "--mtime=@0",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "--format=ustar",
            "-C",
            os.fspath(payload_root),
            "-cf",
            os.fspath(tar_path),
            "--",
            *members,
        ]
        run_checked(command)
        uncompressed_tar_bytes = tar_path.stat().st_size
        run_checked(["zstd", "-T0", "-10", "--no-progress", "-f", os.fspath(tar_path), "-o", os.fspath(asset_path)])
        tar_path.unlink()
        fsync_file(asset_path)
        compressed_bytes = asset_path.stat().st_size
        source_files = sorted({record["source_file"] for record in part_records})
        asset = {
            "filename": filename,
            "order": len(assets),
            "compressed_bytes": compressed_bytes,
            "uncompressed_tar_bytes": uncompressed_tar_bytes,
            "payload_bytes": sum(int(record["byte_length"]) for record in part_records),
            "tensor_count": len(part_records),
            "sha256": sha256_file(asset_path),
            "content_description": (
                "Exact native GGUF F32 router projection and selection-correction bytes "
                f"sourced from GGUF split {number:05d}-of-{count:05d}."
            ),
            "source_identity": {
                "repository": config["source_artifact"]["repository"],
                "revision": config["source_artifact"]["revision"],
                "artifact_identity_manifest_sha256": config["source_artifact"][
                    "identity_manifest_sha256"
                ],
                "source_files": source_files,
            },
            "download_url": f"https://github.com/{repository}/releases/download/{tag}/{filename}",
        }
        assets.append(asset)
        for record in part_records:
            record["asset"] = filename
    return assets


def tool_identity(project_root: Path, project_revision: str) -> dict[str, Any]:
    relatives = [
        "scripts/issue75/extract_router_pack.py",
        "scripts/issue75/verify_router_pack.py",
        "scripts/issue75/router_pack.py",
        "scripts/issue75/extraction-config.json",
    ]
    files = []
    for relative in relatives:
        path = project_root / relative
        files.append({"path": relative, "sha256": sha256_file(path)})
    return {"version": TOOL_VERSION, "project_revision": project_revision, "files": files}


def manifest_document(
    config: dict[str, Any],
    identity: dict[str, Any],
    verified_files: list[dict[str, Any]],
    records: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    gguf_observation: dict[str, Any],
    semantics_sources: list[dict[str, Any]],
    tool: dict[str, Any],
) -> dict[str, Any]:
    source = config["source_artifact"]
    router = config["router"]
    release = config["release"]
    return {
        "schema_version": "kimi-k3-router-pack-manifest-v1",
        "pack_id": config["pack_id"],
        "payload_authority": "Exact native tensor byte ranges copied from the qualified GGUF splits.",
        "model": {
            "repository": source["repository"],
            "revision": source["revision"],
            "variant": source["variant"],
            "architecture": router["architecture"],
        },
        "source_artifact": {
            "identity_manifest_sha256": source["identity_manifest_sha256"],
            "total_bytes": source["total_bytes"],
            "file_count": source["file_count"],
            "files": [
                {"name": item["name"], "size": item["size"], "sha256": item["sha256"]}
                for item in verified_files
            ],
            "qualified_identity": {
                "repository": identity["artifact"]["repository"],
                "revision": identity["artifact"]["revision"],
                "variant": identity["artifact"]["variant"],
            },
        },
        "router": {
            "block_count": router["block_count"],
            "leading_dense_block_count": router["leading_dense_block_count"],
            "routed_layers": list(
                range(
                    router["first_routed_layer"],
                    router["first_routed_layer"] + router["routed_layer_count"],
                )
            ),
            "routed_layer_count": router["routed_layer_count"],
            "experts_per_layer": router["experts_per_layer"],
            "top_k": router["selected_experts"],
            "hidden_dimension": router["hidden_dimension"],
            "router_output_dimension": router["router_output_dimension"],
            "expert_latent_dimension": router["expert_latent_dimension"],
            "projection_shape_ggml_ne_order": [
                router["hidden_dimension"],
                router["router_output_dimension"],
            ],
            "projection_storage_interpretation": (
                "F32 little-endian; ne[0] is contiguous, so each of 896 expert router vectors "
                "contains 7168 contiguous coefficients."
            ),
            "correction_shape": [router["experts_per_layer"]],
        },
        "semantics": {
            "router_projection": (
                "Each routed layer multiplies the full-width 7168-element hidden state by "
                "ffn_gate_inp to produce 896 logits; routed experts consume the separate "
                "3584-element latent projection."
            ),
            "router_activation": "Elementwise sigmoid of the 896 router logits.",
            "selection_score": (
                "The per-expert correction tensor exp_probs_b is added to the unbiased sigmoid "
                "probabilities only for top-16 membership selection."
            ),
            "correction_bias": (
                "A learned 896-element F32 additive selection correction exists for every routed layer."
            ),
            "final_expert_weights": (
                "Weights are gathered from the original unbiased sigmoid probabilities for the "
                "selected IDs, normalized by their sum with the runtime's F16-minimum clamp, and "
                "scaled by 1.0. The correction bias is not part of final expert weights."
            ),
            "accepted_issue73_revisions": config["accepted_issue73_revisions"],
            "sources": semantics_sources,
        },
        "gguf_observation": gguf_observation,
        "inventory": {
            "path": "tensors.json",
            "tensor_count": len(records),
            "projection_tensor_count": sum(
                record["semantic_role"] == "router_projection_weight" for record in records
            ),
            "correction_tensor_count": sum(
                record["semantic_role"] == "selection_correction_bias" for record in records
            ),
            "payload_bytes": sum(int(record["byte_length"]) for record in records),
        },
        "release": {
            "repository": release["repository"],
            "tag": release["tag"],
            "url": f"https://github.com/{release['repository']}/releases/tag/{release['tag']}",
            "ordered_assets": assets,
        },
        "extraction_tool": tool,
        "consumer": {
            "verification_command": (
                "python3 scripts/issue75/verify_router_pack.py --manifest manifest.json "
                "--inventory tensors.json --config extraction-config.json --assets-dir <download-dir> "
                "--work-dir <fresh-empty-dir> --smoke-output smoke-test.json"
            ),
            "requires_full_model": False,
            "requires_gpu": False,
            "external_commands": ["tar", "zstd"],
        },
        "exclusions": [
            "dynamic selected expert IDs or router traces",
            "cache hit, admission, eviction, or capacity evidence",
            "storage, H2D, peer-transfer, or runtime-performance evidence",
            "runtime cost models or Perfetto traces",
        ],
    }


def readme_document(config: dict[str, Any], assets: list[dict[str, Any]]) -> str:
    names = "\n".join(f"- `{asset['filename']}`" for asset in assets)
    return f"""# Kimi K3 immutable router tensor pack

This directory records the static router input for
`moonshotai/Kimi-K3@{config['source_artifact']['revision']}` qualified by Phase 13.5.
The authoritative binary bytes are published at the immutable release tag
`{config['release']['tag']}` in these ordered assets:

{names}

The pack contains exactly 92 native F32 router projection matrices and 92 native
F32 selection-correction vectors. It intentionally contains no dynamic route,
cache, transfer, or performance evidence.

On a fresh CPU-only machine, download the assets into one directory and run the
verification command recorded in `manifest.json`. Verification checks asset and
per-tensor sizes/hashes, exact routed-layer coverage, and a bounded vector-norm /
cosine-similarity smoke operation without access to the full 1.56-TB model.
"""


def main() -> int:
    args = parse_args()
    project_root = args.project_root.resolve()
    config_path = args.config.resolve()
    identity_path = args.artifact_identity_manifest.resolve()
    output_dir = args.output_dir.resolve()
    asset_dir = args.asset_dir.resolve()
    payload_root = args.payload_staging_dir.resolve()
    for path, label in ((output_dir, "output"), (asset_dir, "asset"), (payload_root, "payload staging")):
        if path.exists():
            raise PackError(f"{label} directory already exists; refusing to overwrite: {path}")
    output_dir.mkdir(parents=True)
    asset_dir.mkdir(parents=True)
    payload_root.mkdir(parents=True)

    config = load_json(config_path)
    validate_config(config)
    semantics_sources = validate_semantics_sources(project_root, config)
    project_revision = str(git_output(project_root, "rev-parse", "HEAD")).strip()
    tool = tool_identity(project_root, project_revision)
    identity, verified_files = validate_artifact_identity(identity_path, config, args.hash_workers)

    gguf_python_root = args.gguf_python_root or project_root / "llama.cpp" / "gguf-py"
    records, gguf_observation = scan_tensors(verified_files, config, gguf_python_root.resolve())
    extract_tensors(records, verified_files, payload_root)
    validate_tensor_records(records, config)
    payload_validation = validate_payload_tree(payload_root, records)

    assets = build_assets(records, payload_root, asset_dir, config)
    inventory = {
        "schema_version": "kimi-k3-router-tensor-inventory-v1",
        "pack_id": config["pack_id"],
        "source_artifact_identity_manifest_sha256": config["source_artifact"][
            "identity_manifest_sha256"
        ],
        "tensor_count": len(records),
        "payload_bytes": payload_validation["payload_bytes"],
        "tensors": records,
    }
    manifest = manifest_document(
        config,
        identity,
        verified_files,
        records,
        assets,
        gguf_observation,
        semantics_sources,
        tool,
    )
    shutil.copyfile(config_path, output_dir / "extraction-config.json")
    write_json(output_dir / "tensors.json", inventory)
    write_json(output_dir / "manifest.json", manifest)
    smoke = static_smoke_test(
        payload_root,
        records,
        config["smoke_test"]["layers"],
        *config["smoke_test"]["experts"],
    )
    smoke["verification_scope"] = "local extracted payload before release upload"
    smoke["payload_validation"] = payload_validation
    write_json(output_dir / "smoke-test.json", smoke)
    (output_dir / "README.md").write_text(readme_document(config, assets))
    write_checksums(
        output_dir,
        ["README.md", "extraction-config.json", "manifest.json", "smoke-test.json", "tensors.json"],
        output_dir / "checksums.sha256",
    )
    write_json(
        asset_dir / "asset-upload-manifest.json",
        {
            "schema_version": "kimi-k3-router-pack-asset-upload-v1",
            "release": config["release"],
            "assets": assets,
        },
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

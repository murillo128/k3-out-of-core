#!/usr/bin/env python3
"""Verify and inventory the immutable full-Kimi-K3 split GGUF artifact."""

from __future__ import annotations

import argparse
import gc
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "llama.cpp" / "gguf-py"))

import gguf  # noqa: E402

from common import (  # noqa: E402
    CONFIG_SHA256,
    EXPERT_BUNDLE_BYTES,
    MODEL_REPOSITORY,
    MODEL_REVISION,
    file_identity,
    sha256,
    write_json,
)


EXPERT_RE = re.compile(r"^blk\.(\d+)\.ffn_(gate|up|down)_exps\.weight$")


def field(reader: gguf.GGUFReader, name: str):
    value = reader.get_field(name)
    if value is None:
        raise RuntimeError(f"missing GGUF metadata: {name}")
    return value.contents()


def inspect_split(path: Path) -> tuple[dict[str, object], dict[int, dict[str, int]]]:
    reader = gguf.GGUFReader(path, mode="r")
    experts: dict[int, dict[str, int]] = {}
    types: dict[str, int] = {}
    for tensor in reader.tensors:
        types[tensor.tensor_type.name] = types.get(tensor.tensor_type.name, 0) + 1
        match = EXPERT_RE.fullmatch(tensor.name)
        if match:
            layer = int(match.group(1))
            projection = match.group(2)
            if tensor.tensor_type != gguf.GGMLQuantizationType.MXFP4:
                raise RuntimeError(f"{tensor.name} is {tensor.tensor_type.name}, expected MXFP4")
            experts.setdefault(layer, {})[projection] = tensor.n_bytes
    metadata = {
        "name": path.name,
        "size": path.stat().st_size,
        "split_no": int(field(reader, "split.no")),
        "split_count": int(field(reader, "split.count")),
        "split_tensors_count": int(field(reader, "split.tensors.count")),
        "tensor_count": len(reader.tensors),
        "architecture": field(reader, "general.architecture"),
        "expert_count": int(field(reader, "kimi-k3.expert_count")),
        "expert_used_count": int(field(reader, "kimi-k3.expert_used_count")),
        "tensor_types": types,
    }
    del reader
    gc.collect()
    return metadata, experts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--conversion-log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-revision", required=True)
    parser.add_argument("--nested-revision", required=True)
    parser.add_argument("--converter-revision", required=True)
    parser.add_argument("--skip-hash", action="store_true")
    args = parser.parse_args()

    files = sorted(args.model_dir.glob("kimi-k3-bf16-*-of-*.gguf"))
    if len(files) != 33:
        raise SystemExit(f"expected 33 GGUF splits, found {len(files)}")
    if sha256(args.config) != CONFIG_SHA256:
        raise SystemExit("source config identity mismatch")

    split_metadata: list[dict[str, object]] = []
    layer_projections: dict[int, dict[str, int]] = {}
    for index, path in enumerate(files):
        metadata, experts = inspect_split(path)
        if (metadata["split_no"] != index or metadata["split_count"] != 33 or
                metadata["architecture"] != "kimi-k3" or metadata["expert_count"] != 896 or
                metadata["expert_used_count"] != 16):
            raise SystemExit(f"split metadata mismatch: {path}")
        split_metadata.append(metadata)
        for layer, projections in experts.items():
            if layer in layer_projections:
                raise SystemExit(f"expert layer duplicated across splits: {layer}")
            layer_projections[layer] = projections

    if set(layer_projections) != set(range(1, 93)):
        raise SystemExit("routed expert layers are not exactly 1..92")
    for layer, projections in layer_projections.items():
        if set(projections) != {"gate", "up", "down"}:
            raise SystemExit(f"layer {layer} does not have all three expert projections")
        if sum(projections.values()) // 896 != EXPERT_BUNDLE_BYTES:
            raise SystemExit(f"layer {layer} expert bundle byte identity mismatch")

    identities = []
    for path in files:
        identity = {"name": path.name, "path": str(path), "size": path.stat().st_size}
        if not args.skip_hash:
            identity["sha256"] = sha256(path)
        identities.append(identity)
    manifest = {
        "schema_version": "issue73-k3-artifact-v1", "status": "pass",
        "source": {
            "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION,
            "config": file_identity(args.config),
        },
        "revisions": {
            "project": args.project_revision, "nested": args.nested_revision,
            "converter": args.converter_revision,
        },
        "artifact": {
            "repository": MODEL_REPOSITORY, "revision": MODEL_REVISION,
            "variant": "project-bf16-trunk-native-mxfp4-experts",
            "total_bytes": sum(path.stat().st_size for path in files),
            "files": identities, "split_metadata": split_metadata,
            "routed_layers": len(layer_projections), "experts_per_layer": 896,
            "selected_experts": 16, "expert_bundle_bytes": EXPERT_BUNDLE_BYTES,
            "runtime_model_path": str(files[0]),
        },
        "conversion_log": file_identity(args.conversion_log),
    }
    write_json(args.output, manifest)
    print(f"ISSUE73_MODEL_VERIFY status=pass splits={len(files)} bytes={manifest['artifact']['total_bytes']}")


if __name__ == "__main__":
    main()

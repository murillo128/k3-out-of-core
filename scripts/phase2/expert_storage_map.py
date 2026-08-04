#!/usr/bin/env python3
"""Build and validate versioned expert-storage maps from authoritative loader metadata."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "expert-storage-map-v1"
PROJECTIONS = ("gate", "up", "down")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _span(offset: int, length: int, logical_offset: int = 0) -> dict[str, int]:
    return {"file_offset": offset, "length": length, "logical_offset": logical_offset}


def classify_spans(spans: list[dict[str, int]]) -> str:
    if len(spans) == 1:
        return "contiguous"
    if len(spans) > 1:
        lengths = {span["length"] for span in spans}
        strides = {
            spans[index + 1]["file_offset"] - spans[index]["file_offset"]
            for index in range(len(spans) - 1)
        }
        if len(lengths) == 1 and len(strides) == 1:
            return "strided"
    return "segmented"


def validate_projection_layout(projection: dict[str, Any]) -> None:
    spans = projection.get("spans")
    if not isinstance(spans, list) or not spans:
        raise ValueError("projection must contain at least one source span")
    logical_cursor = 0
    for span in spans:
        if set(span) != {"file_offset", "length", "logical_offset"}:
            raise ValueError("source span fields do not match version 1")
        if span["file_offset"] < 0 or span["length"] <= 0 or span["logical_offset"] != logical_cursor:
            raise ValueError("invalid or non-covering source span")
        logical_cursor += span["length"]
    if projection.get("expert_slice_bytes") != logical_cursor:
        raise ValueError("source spans do not reconstruct the expert slice")
    if projection.get("layout_kind") != classify_spans(spans):
        raise ValueError("layout kind disagrees with source spans")


def build_projection(tensor: dict[str, Any], source: dict[str, Any], expert_id: int) -> dict[str, Any]:
    shape = tensor["logical_shape"]
    strides = tensor["physical_strides"]
    expert_axis = 2
    expert_count = shape[expert_axis]
    if expert_id < 0 or expert_id >= expert_count:
        raise ValueError("expert id is outside the tensor's expert axis")
    slice_bytes = strides[expert_axis]
    if slice_bytes <= 0 or slice_bytes * expert_count != tensor["byte_size"]:
        raise ValueError(f"{tensor['tensor_name']} is not an expert-contiguous K3 tensor")
    span = _span(tensor["file_offset"] + expert_id * slice_bytes, slice_bytes)
    projection = {
        "tensor_name": tensor["tensor_name"],
        "source_file_index": tensor["source_file_index"],
        "source_file_identity": source["identity"],
        "source_file_size": source["size"],
        "tensor_base_offset": tensor["file_offset"],
        "tensor_byte_size": tensor["byte_size"],
        "ggml_type": {"id": tensor["ggml_type_id"], "name": tensor["ggml_type_name"]},
        "logical_shape": shape,
        "physical_strides": strides,
        "expert_axis": expert_axis,
        "expert_slice_bytes": slice_bytes,
        "layout_kind": "contiguous",
        "spans": [span],
        "gguf_alignment": tensor["gguf_alignment"],
        "runtime": {
            "buffer_type": tensor["runtime_buffer_type"],
            "layout_transform": tensor["runtime_layout_transform"],
            "backend_transform": tensor["runtime_backend_transform"],
            "repack": tensor["runtime_repack"],
        },
    }
    validate_projection_layout(projection)
    return projection


def build_storage_map(
    probe: dict[str, Any],
    model: dict[str, Any],
    llama_cpp_revision: str,
    published_gguf_revision: str,
) -> dict[str, Any]:
    if probe.get("status") != 0:
        raise ValueError("loader did not return authoritative file-backing metadata")
    sources = {source["index"]: source for source in probe["source_files"]}
    tensors = {tensor["tensor_name"]: tensor for tensor in probe["tensors"]}
    names = {}
    for name in tensors:
        match = re.fullmatch(r"blk\.(\d+)\.ffn_(gate|up|down)_exps\.weight", name)
        if match is None:
            raise ValueError(f"unsupported routed tensor name: {name}")
        names.setdefault(int(match.group(1)), set()).add(match.group(2))
    routed_layers = sorted(names)
    if not routed_layers or any(value != set(PROJECTIONS) for value in names.values()):
        raise ValueError("routed projection tensor set is incomplete")
    expert_counts = {tensors[f"blk.{layer}.ffn_{projection}_exps.weight"]["logical_shape"][2]
        for layer in routed_layers for projection in PROJECTIONS}
    if len(expert_counts) != 1:
        raise ValueError("routed projections disagree on expert count")
    expert_count = expert_counts.pop()
    if expert_count <= 0:
        raise ValueError("routed expert count is invalid")

    entries = []
    for layer in routed_layers:
        for expert_id in range(expert_count):
            projections = {}
            for projection_name in PROJECTIONS:
                tensor_name = f"blk.{layer}.ffn_{projection_name}_exps.weight"
                tensor = tensors[tensor_name]
                projections[projection_name] = build_projection(
                    tensor, sources[tensor["source_file_index"]], expert_id
                )
            entries.append(
                {
                    "layer": layer,
                    "expert_id": expert_id,
                    "atomic_bundle_bytes": sum(
                        projection["expert_slice_bytes"] for projection in projections.values()
                    ),
                    "projections": projections,
                }
            )

    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            **model,
            "published_gguf_revision": published_gguf_revision,
            "llama_cpp_revision": llama_cpp_revision,
        },
        "source_files": probe["source_files"],
        "expert_count": expert_count,
        "routed_layers": routed_layers,
        "entries": entries,
    }


def validate_source_bytes(storage_map: dict[str, Any]) -> dict[str, Any]:
    sources = {
        source["index"]: (source, Path(source["identity"]))
        for source in storage_map["source_files"]
    }
    tensor_projections: dict[str, list[dict[str, Any]]] = {}
    span_count = 0
    for entry in storage_map["entries"]:
        if set(entry["projections"]) != set(PROJECTIONS):
            raise ValueError("atomic bundle is missing a routed projection")
        if entry["atomic_bundle_bytes"] != sum(
            projection["expert_slice_bytes"] for projection in entry["projections"].values()
        ):
            raise ValueError("atomic bundle size mismatch")
        for projection in entry["projections"].values():
            validate_projection_layout(projection)
            source, path = sources[projection["source_file_index"]]
            if path.stat().st_size != source["size"]:
                raise ValueError("source file size changed")
            if projection["source_file_identity"] != source["identity"]:
                raise ValueError("source identity mismatch")
            if projection["tensor_base_offset"] % projection["gguf_alignment"] != 0:
                raise ValueError("tensor base is not GGUF aligned")
            for span in projection["spans"]:
                if span["file_offset"] + span["length"] > source["size"]:
                    raise ValueError("expert span is outside the source file")
                span_count += 1
            tensor_projections.setdefault(projection["tensor_name"], []).append(projection)

    tensor_evidence = []
    for tensor_name, projections in sorted(tensor_projections.items()):
        expert_count = storage_map["expert_count"]
        if len(projections) != expert_count:
            raise ValueError(f"{tensor_name} does not have all expert slices")
        projections.sort(key=lambda projection: projection["spans"][0]["file_offset"])
        first = projections[0]
        source_path = sources[first["source_file_index"]][1]
        base = first["tensor_base_offset"]
        size = first["tensor_byte_size"]
        expected_offsets = [base + index * first["expert_slice_bytes"] for index in range(expert_count)]
        observed_offsets = [projection["spans"][0]["file_offset"] for projection in projections]
        if observed_offsets != expected_offsets or sum(
            projection["spans"][0]["length"] for projection in projections
        ) != size:
            raise ValueError(f"{tensor_name} expert spans do not exactly cover the source tensor")
        with source_path.open("rb") as source_file:
            source_file.seek(base)
            whole = source_file.read(size)
            reconstructed = bytearray()
            for projection in projections:
                span = projection["spans"][0]
                source_file.seek(span["file_offset"])
                reconstructed.extend(source_file.read(span["length"]))
        if len(whole) != size or bytes(reconstructed) != whole:
            raise ValueError(f"{tensor_name} source-byte reconstruction failed")
        tensor_evidence.append(
            {
                "tensor_name": tensor_name,
                "byte_size": size,
                "sha256": hashlib.sha256(whole).hexdigest(),
            }
        )

    return {
        "entry_count": len(storage_map["entries"]),
        "projection_count": len(storage_map["entries"]) * 3,
        "span_count": span_count,
        "tensor_count": len(tensor_evidence),
        "tensors": tensor_evidence,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")

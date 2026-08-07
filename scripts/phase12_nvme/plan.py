#!/usr/bin/env python3
"""Create deterministic native benchmark plans for the Phase 12-NVMe corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
from common import SHUFFLE, Scale, canonical_bytes, selected_experts, sha256_bytes, sha256_file  # noqa: E402
from corpus import HEADER_SIZE, INDEX  # noqa: E402


@dataclass(frozen=True)
class PlanOperation:
    ordinal: int
    source: int
    path: Path
    offset: int
    length: int
    sha256: str
    layer: int
    expert: int


def _route_key_ranks(request: str, token: int) -> tuple[int, ...]:
    if request == "COLD_SPREAD":
        return tuple(range(16))
    if request == "LOGICAL_SHUFFLE":
        return SHUFFLE
    if request == "HALF_HOT":
        shift = (3 * token) % 8
        return tuple(range(8)) + tuple(8 + ((rank + shift) % 8) for rank in range(8))
    raise ValueError(f"unknown request class: {request}")


def _window_8(group: list[PlanOperation]) -> list[PlanOperation]:
    pending = list(group)
    output: list[PlanOperation] = []
    while pending:
        candidate = min(
            range(min(8, len(pending))),
            key=lambda index: (pending[index].source, str(pending[index].path), pending[index].offset, pending[index].ordinal),
        )
        output.append(pending.pop(candidate))
    return output


def _load_checked_metadata(corpus: Path) -> tuple[dict[str, object], Scale, list[dict[str, object]]]:
    generation = json.loads((corpus / "generation.json").read_text())
    scale = Scale(**generation["scale"])
    route = json.loads((corpus / "route-corpus.json").read_text())
    if sha256_bytes(canonical_bytes(route)) != generation["route_sha256"]:
        raise ValueError("route checksum mismatch")
    manifests: dict[str, dict[str, object]] = {}
    for layout in ("a", "b"):
        directory = corpus / f"layout-{layout}"
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_sha = sha256_file(manifest_path)
        if manifest_sha != generation[f"layout_{layout}_manifest_sha256"]:
            raise ValueError(f"Layout {layout.upper()} manifest checksum mismatch")
        seal = json.loads((directory / "SEALED.json").read_text())
        if seal != {"schema_version": "phase12p-seal-v1", "manifest_sha256": manifest_sha}:
            raise ValueError(f"Layout {layout.upper()} seal mismatch")
        manifests[layout] = manifest
    entries = json.loads((corpus / "layout-a/index.json").read_text())["entries"]
    if sha256_bytes(canonical_bytes(entries)) != manifests["a"]["entries_sha256"]:
        raise ValueError("Layout A index checksum mismatch")
    keys = sorted((layer, expert) for layer in range(scale.layers) for expert in selected_experts(scale, layer))
    if [(entry["layer"], entry["expert"]) for entry in entries] != keys:
        raise ValueError("Layout A index ordering or coverage mismatch")
    a_path = corpus / "layout-a/projection-spans.bin"
    b_path = corpus / "layout-b/contiguous-experts.bin"
    if a_path.stat().st_size != scale.logical_bytes:
        raise ValueError("Layout A logical EOF mismatch")
    expected_b_size = HEADER_SIZE + scale.useful_bytes + len(entries) * INDEX.size
    if b_path.stat().st_size != expected_b_size:
        raise ValueError("Layout B EOF mismatch")
    with b_path.open("rb", buffering=0) as stream:
        try:
            header = json.loads(stream.read(HEADER_SIZE).rstrip(b"\0"))
        except ValueError as error:
            raise ValueError("invalid Layout B header") from error
        basis = dict(header)
        basis_sha = basis.pop("header_basis_sha256", None)
        if basis_sha != sha256_bytes(canonical_bytes(basis)) or header.get("magic") != "K3P12PCONTIG":
            raise ValueError("Layout B header checksum/magic mismatch")
        stream.seek(int(header["index_offset"]))
        index_bytes = stream.read(int(header["index_length"]))
    if len(index_bytes) != len(entries) * INDEX.size or sha256_bytes(index_bytes) != header["index_sha256"]:
        raise ValueError("Layout B index checksum mismatch")
    for ordinal, entry in enumerate(entries):
        packed = INDEX.unpack_from(index_bytes, ordinal * INDEX.size)
        if packed[:2] != (entry["layer"], entry["expert"]) or packed[2] != entry["layout_b_offset"]:
            raise ValueError("Layout B fixed index identity mismatch")
        if packed[3] != entry["bundle_bytes"] or tuple(packed[4:7]) != tuple(entry["layout_a_offsets"]):
            raise ValueError("Layout B fixed index span mismatch")
        if packed[10].hex() != entry["bundle_sha256"]:
            raise ValueError("Layout B fixed index bundle checksum mismatch")
    return generation, scale, entries


def build_plan(corpus: Path, layout: str, request: str, token: int, order: str) -> list[PlanOperation]:
    _, scale, entries = _load_checked_metadata(corpus)
    by_key = {(int(entry["layer"]), int(entry["expert"])): entry for entry in entries}
    key_ranks = _route_key_ranks(request, token)
    operations: list[PlanOperation] = []
    ordinal = 0
    for layer in range(scale.layers):
        keys = selected_experts(scale, layer)
        group: list[PlanOperation] = []
        for key_rank in key_ranks:
            expert = keys[key_rank]
            entry = by_key[(layer, expert)]
            if layout == "A":
                path = corpus / "layout-a/projection-spans.bin"
                offset = int(entry["layout_a_offsets"][0])
            elif layout == "B":
                path = corpus / "layout-b/contiguous-experts.bin"
                offset = int(entry["layout_b_offset"])
            else:
                raise ValueError("layout must be A or B")
            group.append(PlanOperation(
                ordinal=ordinal,
                source=0,
                path=path.resolve(),
                offset=offset,
                length=int(entry["bundle_bytes"]),
                sha256=str(entry["bundle_sha256"]),
                layer=layer,
                expert=expert,
            ))
            ordinal += 1
        operations.extend(_window_8(group) if order == "LOCALITY_WINDOW_8" else group)
    if order == "PHYSICAL_OFFSET":
        operations.sort(key=lambda item: (item.source, str(item.path), item.offset, item.ordinal))
    elif order not in ("LOGICAL_SELECTED", "LOCALITY_WINDOW_8"):
        raise ValueError("unsupported submission order")
    return operations


def encode_plan(operations: list[PlanOperation]) -> bytes:
    lines = ["# ordinal\tsource\tpath\toffset\tlength\tbundle_sha256"]
    for operation in operations:
        if "\t" in str(operation.path) or "\n" in str(operation.path):
            raise ValueError("plan paths may not contain tabs or newlines")
        lines.append(
            f"{operation.ordinal}\t{operation.source}\t{operation.path}\t{operation.offset}\t{operation.length}\t{operation.sha256}"
        )
    return ("\n".join(lines) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--layout", choices=("A", "B"), required=True)
    parser.add_argument("--request", choices=("COLD_SPREAD", "LOGICAL_SHUFFLE", "HALF_HOT"), required=True)
    parser.add_argument("--token", type=int, choices=range(32), required=True)
    parser.add_argument("--order", choices=("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    encoded = encode_plan(build_plan(args.corpus, args.layout, args.request, args.token, args.order))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded)
    print(json.dumps({
        "schema_version": "phase12-nvme-plan-v1",
        "path": str(args.output),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "operations": len([line for line in encoded.splitlines() if not line.startswith(b"#")]),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

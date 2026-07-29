#!/usr/bin/env python3
"""Capture and byte-validate Phase 2 expert storage maps."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from expert_storage_map import build_storage_map, sha256_file, validate_source_bytes, write_json


PUBLISHED_GGUF_REVISION = "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"
ARTIFACTS = {
    "f16": {
        "name": "Kimi-K3-0.40B-F16.gguf",
        "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
        "expected_routed_type": "f16",
    },
    "mxfp4": {
        "name": "Kimi-K3-0.40B-MXFP4.gguf",
        "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
        "expected_routed_type": "mxfp4",
    },
}


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)


def compile_probe(root: Path, temporary: Path) -> tuple[Path, list[str]]:
    build_bin = root / "llama.cpp/build-cpu/bin"
    output = temporary / "storage-probe"
    command = [
        "c++",
        "-std=c++17",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
        f"-I{root / 'llama.cpp/include'}",
        f"-I{root / 'llama.cpp/ggml/include'}",
        str(root / "scripts/phase2/storage_probe.cpp"),
        f"-L{build_bin}",
        f"-Wl,-rpath,{build_bin}",
        "-lllama",
        "-lggml",
        "-lggml-base",
        "-o",
        str(output),
    ]
    completed = run(command, root)
    if completed.returncode != 0:
        raise RuntimeError(f"storage probe compilation failed: {completed.stderr}")
    return output, command


def probe_model(root: Path, binary: Path, model: Path, input_mode: str = "path") -> dict[str, Any]:
    command = [
        str(binary),
        "--model",
        str(model),
        "--gpu-layers",
        "0",
        "--input-mode",
        input_mode,
    ]
    completed = run(command, root)
    if completed.returncode != 0:
        raise RuntimeError(f"{input_mode} storage probe failed: {completed.stderr}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{input_mode} storage probe returned invalid JSON") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--llama-cpp-revision", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/2026-07-29/skynet/phase2-observability"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    schema_path = root / "schemas/phase2/expert-storage-map-v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    schema_validator = Draft202012Validator(schema)

    with tempfile.TemporaryDirectory(prefix="phase2-storage-") as directory:
        binary, compile_command = compile_probe(root, Path(directory))
        maps = {}
        validations = {}
        for artifact, expected in ARTIFACTS.items():
            model_path = (root / "models/gguf" / expected["name"]).resolve()
            if model_path.stat().st_size != expected["size"] or sha256_file(model_path) != expected["sha256"]:
                raise RuntimeError(f"{artifact} model identity mismatch")
            probe = probe_model(root, binary, model_path)
            if len(probe.get("source_files", [])) != 1 or len(probe.get("tensors", [])) != 21:
                raise RuntimeError(f"{artifact} source/tensor coverage mismatch")
            if {tensor["ggml_type_name"] for tensor in probe["tensors"]} != {
                expected["expected_routed_type"]
            }:
                raise RuntimeError(f"{artifact} routed projection type mismatch")
            storage_map = build_storage_map(
                probe,
                {
                    "name": expected["name"],
                    "size": expected["size"],
                    "sha256": expected["sha256"],
                    "source_revision": expected["source_revision"],
                },
                args.llama_cpp_revision,
                PUBLISHED_GGUF_REVISION,
            )
            schema_validator.validate(storage_map)
            validation = validate_source_bytes(storage_map)
            if validation["entry_count"] != 56 or validation["projection_count"] != 168:
                raise RuntimeError(f"{artifact} map cardinality mismatch")
            map_path = output / f"phase2-{artifact}-expert-storage-map-v1.json"
            write_json(map_path, storage_map)
            try:
                recorded_map_path = str(map_path.relative_to(root))
            except ValueError:
                recorded_map_path = str(map_path)
            maps[artifact] = {
                "path": recorded_map_path,
                "sha256": sha256_file(map_path),
                "source_file_count": len(probe["source_files"]),
                "runtime_layout_transforms": sum(
                    bool(tensor["runtime_layout_transform"]) for tensor in probe["tensors"]
                ),
                "runtime_backend_transforms": sum(
                    bool(tensor["runtime_backend_transform"]) for tensor in probe["tensors"]
                ),
                "runtime_repacks": sum(bool(tensor["runtime_repack"]) for tensor in probe["tensors"]),
            }
            validations[artifact] = validation

        unsupported_model = (root / "models/gguf" / ARTIFACTS["f16"]["name"]).resolve()
        unsupported = {
            mode: probe_model(root, binary, unsupported_model, mode)
            for mode in ("file-pointer", "user-metadata")
        }
        if any(value != {"status": -3, "source_file_count": 0} for value in unsupported.values()):
            raise RuntimeError(f"anonymous/user metadata status mismatch: {unsupported}")

    result = {
        "status": "pass",
        "llama_cpp_revision": args.llama_cpp_revision,
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "probe_compile_command": compile_command,
        "schema": {
            "path": str(schema_path.relative_to(root)),
            "sha256": sha256_file(schema_path),
        },
        "maps": maps,
        "byte_validation": validations,
        "unsupported_inputs": unsupported,
        "totals": {
            "artifacts": 2,
            "entries": 112,
            "projections": 336,
            "source_tensors": 42,
            "anonymous_input_modes": 2,
        },
    }
    write_json(output / "phase2-storage-validation.json", result)
    print(json.dumps(result["totals"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

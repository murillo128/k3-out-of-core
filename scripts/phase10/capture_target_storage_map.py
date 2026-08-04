#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase2"))

from expert_storage_map import build_storage_map, write_json  # noqa: E402
from prefetch_common import Phase10Error, load_json, validate_profile  # noqa: E402


def capture(probe: Path, model: Path, profile: dict, nested_head: str) -> dict:
    layers = profile["target"]["routed_layers"]
    if layers != list(range(layers[0], layers[-1] + 1)):
        raise Phase10Error("storage metadata probe requires contiguous routed layers")
    command = [str(probe), "--model", str(model), "--gpu-layers", "0",
        "--routed-layer-begin", str(layers[0]), "--routed-layer-end", str(layers[-1])]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise Phase10Error(f"storage metadata probe failed ({completed.returncode}): {completed.stderr[-2000:]}")
    import json
    metadata = json.loads(completed.stdout)
    target = profile["target"]
    sources = metadata.get("source_files", [])
    if len(sources) != len(target["files"]) or any(
            source["size"] != target["files"][index]["size"] for index, source in enumerate(sources)):
        raise Phase10Error("storage metadata sources differ from the exact target package")
    total_size = sum(item["size"] for item in target["files"])
    model_record = {"name": target["files"][0]["name"], "size": total_size,
        "sha256": target["files"][0]["sha256"] if len(target["files"]) == 1 else target["package_sha256"],
        "package_sha256": target["package_sha256"], "files": target["files"]}
    storage_map = build_storage_map(metadata, model_record, nested_head, "runtime-package")
    expected = {(item["layer"], item["expert"]): item["physical_bytes"]
        for item in target["expert_bytes"]}
    actual = {(item["layer"], item["expert_id"]): item["atomic_bundle_bytes"]
        for item in storage_map["entries"]}
    if actual != expected:
        raise Phase10Error("storage metadata bundle bytes differ from the exact target fingerprint")
    return storage_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a target-bound runtime expert storage map")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        profile = load_json(args.profile)
        validate_profile(profile)
        write_json(args.output, capture(args.probe.resolve(), args.model.resolve(), profile, args.nested_head))
        print(args.output)
        return 0
    except (OSError, ValueError, Phase10Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

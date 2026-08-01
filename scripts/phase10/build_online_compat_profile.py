#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from prefetch_common import Phase10Error, load_json, validate_profile, write_json


def target_fingerprint(probe: Path, model: Path) -> dict[str, Any]:
    completed = subprocess.run(
        [str(probe), "--fingerprint", "--model", str(model)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        tail = completed.stderr.decode("utf-8", errors="replace").splitlines()[-24:]
        raise Phase10Error(f"target fingerprint probe failed ({completed.returncode}):\n" + "\n".join(tail))
    try:
        document = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Phase10Error(f"target fingerprint probe emitted invalid JSON: {error}") from error
    if not isinstance(document, dict) or set(document) != {"schema_version", "target"} or \
            document["schema_version"] != "phase10-target-fingerprint-v1":
        raise Phase10Error("target fingerprint probe emitted an unsupported document")
    return document["target"]


def adapt_profile(base: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    routed = target["routed_layers"]
    routed_set = set(routed)
    experts = target["experts_per_layer"]
    byte_map = {(item["layer"], item["expert"]): item for item in target["expert_bytes"]}
    result = dict(base)
    result["profile_id"] = f"{base['profile_id']}-online-compat-{target['package_sha256'][:12]}"
    result["tool"] = {"name": "phase10-online-compat-profile", "version": 1}
    result["target"] = target
    result["static_counts"] = [item for item in base["static_counts"]
        if item["layer"] in routed_set and item["expert"] < experts]
    layer_index = {layer: index for index, layer in enumerate(routed)}
    result["transitions"] = [item for item in base["transitions"]
        if item["source_layer"] in layer_index and
           layer_index[item["source_layer"]] + 1 < len(routed) and
           routed[layer_index[item["source_layer"]] + 1] == item["target_layer"] and
           item["source_expert"] < experts and item["target_expert"] < experts]
    seed_count = min(len(base["seed"]), len(result["static_counts"]))
    ranked = sorted(result["static_counts"], key=lambda item: (-item["count"], item["layer"], item["expert"]))[:seed_count]
    result["seed"] = []
    for count in sorted(ranked, key=lambda item: (item["count"], item["layer"], item["expert"])):
        key = byte_map[(count["layer"], count["expert"])]
        result["seed"].append({**count, "payload_bytes": key["payload_bytes"],
            "physical_bytes": key["physical_bytes"]})
    bundle_bytes = target["expert_bytes"][0]["physical_bytes"]
    for cost in result["costs"]:
        cost["storage_bytes"] = bundle_bytes if cost["transport"] in {"BUFFERED", "DIRECT_IO"} else 0
        cost["h2d_bytes"] = bundle_bytes if cost["readiness"] == "DEVICE_READY" else 0
    validate_profile(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind a frozen Phase 10 predictor profile to an exact online compatibility target")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--base-profile", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        base = load_json(args.base_profile)
        validate_profile(base)
        target = target_fingerprint(args.probe.resolve(), args.model.resolve())
        profile = adapt_profile(base, target)
        write_json(args.output, profile)
        return 0
    except (OSError, Phase10Error, KeyError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

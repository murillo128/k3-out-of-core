#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from prefetch_common import Phase10Error, canonical_bytes, load_json, require_fields, validate_profile, write_json


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


def measured_costs(document: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]]:
    require_fields(document, {"schema_version", "status", "target", "measurements", "costs"}, "target costs")
    if document["schema_version"] != "phase10-target-costs-v1" or document["status"] != "pass":
        raise Phase10Error("online compatibility requires eligible target-specific costs")
    identity = document["target"]
    require_fields(identity, {"package_sha256", "files", "tensor_layout_sha256", "expert_bytes_sha256"},
        "target cost identity")
    if identity["package_sha256"] != target["package_sha256"] or identity["files"] != target["files"] or \
            identity["tensor_layout_sha256"] != target["tensor_layout_sha256"] or \
            identity["expert_bytes_sha256"] != hashlib.sha256(canonical_bytes(target["expert_bytes"])).hexdigest():
        raise Phase10Error("target-specific costs describe a different package")
    if not isinstance(document["measurements"], list) or not document["measurements"] or \
            not isinstance(document["costs"], list) or not document["costs"]:
        raise Phase10Error("target-specific cost evidence is empty")
    return copy.deepcopy(document["costs"])


def adapt_profile(
        base: dict[str, Any],
        target: dict[str, Any],
        costs_document: dict[str, Any],
        costs_path: Path) -> dict[str, Any]:
    routed = target["routed_layers"]
    routed_set = set(routed)
    experts = target["experts_per_layer"]
    byte_map = {(item["layer"], item["expert"]): item for item in target["expert_bytes"]}
    result = copy.deepcopy(base)
    base_profile_id = base["profile_id"].split("-online-compat-", 1)[0]
    result["profile_id"] = f"{base_profile_id}-online-compat-{target['package_sha256'][:12]}"
    result["tool"] = {"name": "phase10-online-compat-profile", "version": 2}
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
    result["costs"] = measured_costs(costs_document, target)
    selected = next((cost for cost in result["costs"]
        if cost["transport"] == result["selection"]["transport"] and
           cost["readiness"] == result["selection"]["readiness"]), None)
    if selected is None:
        raise Phase10Error("target-specific costs omit the selected envelope")
    costs_bytes = costs_path.read_bytes()
    costs_sha256 = hashlib.sha256(costs_bytes).hexdigest()
    result["source"]["artifacts"] = [artifact for artifact in result["source"]["artifacts"]
        if "exact-costs" not in artifact["name"] and "target-costs" not in artifact["name"]]
    result["source"]["artifacts"].append({"name": costs_path.name,
        "size": len(costs_bytes), "sha256": costs_sha256})
    result["selection"]["break_even_bps"] = selected["break_even_bps"]
    predictor_training_digest = hashlib.sha256(canonical_bytes({
        "source": {"kind": result["source"]["kind"], "fold": result["source"]["fold"],
            "artifacts": [artifact for artifact in result["source"]["artifacts"]
                if artifact["name"] != costs_path.name]},
        "static_counts": result["static_counts"], "transitions": result["transitions"],
    })).hexdigest()
    result["selection"]["tuning_digest"] = hashlib.sha256(canonical_bytes({
        "predictor_training_digest": predictor_training_digest,
        "target_package_sha256": target["package_sha256"],
        "target_costs_sha256": costs_sha256,
        "policy": result["selection"]["policy"],
        "candidates_per_target": result["selection"]["candidates_per_target"],
        "temporal_window_tokens": result["selection"]["temporal_window_tokens"],
        "transport": result["selection"]["transport"],
        "readiness": result["selection"]["readiness"],
    })).hexdigest()
    validate_profile(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind a frozen Phase 10 predictor profile to an exact online compatibility target")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--base-profile", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--costs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        base = load_json(args.base_profile)
        validate_profile(base)
        target = target_fingerprint(args.probe.resolve(), args.model.resolve())
        costs = load_json(args.costs)
        profile = adapt_profile(base, target, costs, args.costs.resolve())
        write_json(args.output, profile)
        return 0
    except (OSError, Phase10Error, KeyError, TypeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Verify the heterogeneous-layout technical manifest without third-party packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "results/2026-08-05/host-79466/heterogeneous-layout/manifest.json"
REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_WORKFLOW_KEYS = {
    "branch", "comment", "issue", "issue_number", "label", "merge", "pr",
    "pull_request", "review", "review_verdict", "roadmap",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_workflow_metadata(value: Any, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            require(key not in FORBIDDEN_WORKFLOW_KEYS, f"workflow metadata at {location}.{key}")
            reject_workflow_metadata(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_workflow_metadata(nested, f"{location}[{index}]")


def validate_identity(reference: dict[str, Any], external: bool = False) -> None:
    require(SHA256.fullmatch(reference["sha256"]) is not None, "invalid SHA-256")
    require(reference["bytes"] > 0, "empty evidence identity")
    path = Path(reference["storage_path"] if external else ROOT / reference["path"])
    if external and not path.exists():
        return
    require(path.is_file(), f"missing evidence: {path}")
    require(path.stat().st_size == reference["bytes"], f"size drift: {path}")
    require(sha256(path) == reference["sha256"], f"digest drift: {path}")


def validate_manifest(document: dict[str, Any], verify_archives: bool) -> None:
    require(document.get("schema_version") == "heterogeneous-layout-validation-v1", "schema version")
    reject_workflow_metadata(document)

    revisions = document["revisions"]
    for name in ("project_base", "project_technical_head", "nested_base", "nested_head", "gitlink"):
        require(REVISION.fullmatch(revisions[name]) is not None, f"invalid revision: {name}")
    require(revisions["gitlink"] == revisions["nested_head"], "gitlink/nested target mismatch")

    artifact = document["artifact"]
    require(len(artifact["files"]) == 4, "artifact must have four splits")
    require(artifact["revision"] == "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1", "artifact revision drift")
    for item in artifact["files"]:
        require(item["bytes"] > 0 and SHA256.fullmatch(item["sha256"]) is not None, "artifact identity")
    validate_identity(artifact["accepted_verification"])

    builds = {item["name"]: item for item in document["builds"]}
    for name in ("cpu-debug", "cuda-release", "cpu-asan"):
        require(builds[name]["status"] == "pass", f"build failed: {name}")
        require(builds[name]["tests_passed"] == builds[name]["tests_total"] == 8, f"test count: {name}")

    registry = document["layout_registry"]
    require(registry["maximum_classes"] == 8, "layout-class maximum")
    require(registry["class_count"] == len(registry["classes"]) == 3, "class count")
    classes = registry["classes"]
    require([item["id"] for item in classes] == list(range(len(classes))), "class IDs are not dense")
    require(len({item["canonical_digest_fnv1a64"] for item in classes}) == len(classes), "class digest collision")
    require(sorted(item["payload_bytes"] for item in classes) == [10878976, 13303808, 15794176], "payload classes")
    require(len(registry["layer_class_ids"]) == 43, "routed layer map")
    require(all(0 <= value < len(classes) for value in registry["layer_class_ids"]), "invalid layer class")
    require(registry["preflight"]["passed"] is True, "kernel preflight")
    require(registry["preflight"]["consumer_count"] == 9, "kernel preflight count")
    require(registry["preflight"]["allocation_before_workers"] is True, "preflight ordering")

    tiers = document["universal_tiers"]
    for name in ("hot", "cold", "transfer_ring"):
        tier = tiers[name]
        require(tier["actual"] == tier["stride"] * tier["count"], f"{name} allocation arithmetic")
        require(tier["unused"] == tier["requested"] - tier["actual"], f"{name} budget remainder")
        require(len(tier["role_offsets"]) == len(tier["role_extents"]) == 12, f"{name} role bounds")
    require(tiers["transfer_ring"]["count"] in (2, 3, 4), "transfer lane bound")
    for layout_class in classes:
        require(layout_class["hot_padding_bytes"] == tiers["hot"]["stride"] - layout_class["payload_bytes"], "hot padding")
        require(layout_class["cold_padding_bytes"] == tiers["cold"]["stride"] - layout_class["payload_bytes"], "cold padding")
        require(layout_class["lane_padding_bytes"] == tiers["transfer_ring"]["stride"] - layout_class["payload_bytes"], "lane padding")
        require(layout_class["stage_bundles"] > 0 and layout_class["h2d_bundles"] > 0, "class path not exercised")
        require(layout_class["stage_bytes"] == layout_class["h2d_bytes"], "class transfer byte mismatch")

    correctness = document["correctness"]
    kernel = correctness["real_cuda_kernel_comparison"]
    require(kernel["status"] == "pass" and kernel["comparison_count"] == 9, "real CUDA kernel comparison")
    require(kernel["all_bit_exact"] and kernel["all_finite"] and kernel["all_padding_guards_intact"], "CUDA parity")
    provider = correctness["real_provider_path"]
    require(provider["status"] == "pass" and provider["finite"], "real provider path")
    require(provider["class_count"] == 3 and provider["active_background_flights"] == 0, "provider terminal state")
    binding = correctness["sealed_binding_class_match"]
    require(binding["status"] == "pass" and binding["mismatched_valid_class_family_error"] == "invalid_binding",
            "cross-class graph binding did not fail closed")
    sanitizer = correctness["compute_sanitizer"]
    require(sanitizer["status"] == "pass" and sanitizer["errors"] == sanitizer["leaked_bytes"] == 0, "CUDA sanitizer")

    checkpoints = document["checkpoints"]
    require(checkpoints["mechanism"]["status"] == "pass", "mechanism checkpoint")
    require(checkpoints["mechanism"]["final_capable"] is False, "mechanism final capability")
    if checkpoints["full_model"]["status"] == "pending":
        require(document["result"]["status"] == "in_progress", "pending full-model disposition")
        require(document["result"]["final_capable"] is False, "pending result cannot be final")
    else:
        require(checkpoints["full_model"]["final_capable"] is True, "full-model final capability")
        require(document["result"]["status"] in ("positive", "negative"), "final disposition")
        require(document["result"]["final_capable"] is True, "final result capability")

    for archive in document["archives"]:
        require(SHA256.fullmatch(archive["sha256"]) is not None, "archive SHA-256")
        require(archive["bytes"] > 0 and archive["member_count"] > 0, "archive identity")
        index = ROOT / archive["index"]
        require(index.is_file(), f"missing archive index: {index}")
        if verify_archives:
            validate_identity(archive, external=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--verify-archives", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text())
    validate_manifest(document, args.verify_archives)
    print("heterogeneous layout validation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

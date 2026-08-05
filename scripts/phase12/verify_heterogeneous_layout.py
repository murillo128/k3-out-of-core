#!/usr/bin/env python3
"""Verify the heterogeneous-layout technical manifest without third-party packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
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


def validate_archive_index(archive: dict[str, Any], verify_archive: bool) -> None:
    index_path = ROOT / archive["index"]
    require(index_path.is_file(), f"missing archive index: {index_path}")
    index = json.loads(index_path.read_text())
    require(index.get("schema_version") == "evidence-archive-index-v1", "archive index schema")
    require(index["archive"]["path"] == archive["storage_path"], "archive index path")
    for key in ("bytes", "sha256", "member_count"):
        require(index["archive"][key] == archive[key], f"archive index {key}")
    members = index["members"]
    require(len(members) == archive["member_count"], "archive member count")
    paths = [item["path"] for item in members]
    require(paths == sorted(paths) and len(paths) == len(set(paths)), "archive member paths")
    for item in members:
        require(item["bytes"] >= 0 and SHA256.fullmatch(item["sha256"]) is not None,
                f"archive member identity: {item['path']}")
    if not verify_archive:
        return
    validate_identity(archive, external=True)
    archive_path = Path(archive["storage_path"])
    with tempfile.TemporaryDirectory(prefix="heterogeneous-layout-archive-") as temporary:
        extracted_root = Path(temporary)
        subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-xf", str(archive_path), "-C", str(extracted_root)],
            check=True,
        )
        extracted = sorted(path.relative_to(extracted_root).as_posix()
                           for path in extracted_root.rglob("*") if path.is_file())
        require(extracted == paths, "archive contents/index mismatch")
        for item in members:
            path = extracted_root / item["path"]
            require(path.stat().st_size == item["bytes"], f"archive member size: {item['path']}")
            require(sha256(path) == item["sha256"], f"archive member digest: {item['path']}")


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

    matrix = correctness.get("full_model_matrix")
    if matrix is not None:
        require(matrix["status"] == "pass", "full-model matrix")
        runtime = matrix["runtime"]
        require(runtime == {"n_ctx": 4096, "n_batch": 128, "n_ubatch": 128,
                            "generated_tokens": 8, "seed": 1, "temperature": 0},
                "full-model runtime geometry")
        require(matrix["rendered_prompt"]["bytes"] == 150, "rendered prompt size")
        require(SHA256.fullmatch(matrix["rendered_prompt"]["sha256"]) is not None,
                "rendered prompt identity")
        for placement in ("fit", "cpu_moe"):
            require(matrix[placement]["status"] == "pass" and matrix[placement]["processes"] >= 3,
                    f"full-model placement: {placement}")
            require(SHA256.fullmatch(matrix[placement]["generated_text_sha256"]) is not None,
                    f"full-model output identity: {placement}")
        universal = matrix["universal_provider"]
        require(universal["status"] == "pass" and universal["timed_processes"] >= 3 and
                universal["pss_processes"] >= 1, "provider repeat coverage")
        require(len(universal["generated_ids"]) == runtime["generated_tokens"], "provider token count")
        require(universal["all_repeats_exact"] and universal["all_finite"], "provider determinism")
        require(SHA256.fullmatch(universal["generated_and_logits_sha256"]) is not None and
                SHA256.fullmatch(universal["routes_sha256"]) is not None, "provider output identity")
        require(matrix["provider_vs_cpu_generated_ids_equal"], "provider/CPU generated token parity")
        require(matrix["same_kernel_compact_vs_universal_bit_exact"], "same-kernel layout parity")
        analysis = matrix["analysis"]
        require(analysis["archive_member"] == "full-model-analysis.json" and analysis["bytes"] > 0 and
                SHA256.fullmatch(analysis["sha256"]) is not None, "full-model analysis identity")
        full_archive = next((item for item in document["archives"] if item["checkpoint"] == "full_model"), None)
        require(full_archive is not None, "missing full-model archive")
        full_index = json.loads((ROOT / full_archive["index"]).read_text())
        analysis_member = next((item for item in full_index["members"]
                                if item["path"] == analysis["archive_member"]), None)
        require(analysis_member is not None and analysis_member["bytes"] == analysis["bytes"] and
                analysis_member["sha256"] == analysis["sha256"], "full-model analysis/archive mismatch")

        full_bounds = document["resource_bounds"]["full_model"]
        hot = full_bounds["provider_tiers"]["hot"]
        require(hot["actual_bytes"] == hot["slots"] * hot["slot_stride_bytes"], "full-model hot arithmetic")
        require(hot["unused_bytes"] == hot["budget_bytes"] - hot["actual_bytes"], "full-model hot budget")
        require(hot["slots"] >= hot["context_required_slots"] >= 1, "full-model hot capacity")
        cold = full_bounds["provider_tiers"]["cold"]
        require(cold["actual_bytes"] == cold["slots"] * cold["slot_stride_bytes"], "full-model cold arithmetic")
        require(cold["unused_bytes"] == cold["requested_bytes"] - cold["actual_bytes"], "full-model cold budget")
        require(cold["ready_logical_bytes"] <= cold["actual_bytes"], "cold useful residency")
        require(cold["resident_ready_bytes"] <= cold["actual_bytes"] + 4096, "cold physical residency")
        transfer = full_bounds["provider_tiers"]["transfer"]
        require(transfer["actual_bytes"] == transfer["lanes"] * transfer["lane_stride_bytes"],
                "full-model transfer arithmetic")
        require(2 <= transfer["lanes"] <= 4 and transfer["pinned_or_registered_bytes"] <= 1024**3,
                "full-model transfer bound")
        require(transfer["pageable_fallback"] is False, "unexpected pageable transfer fallback")

        safety = full_bounds["provider_safety"]
        require(safety["required_vram_free_mib"] == 6144 and
                safety["minimum_vram_free_mib"] >= safety["required_vram_free_mib"], "provider VRAM reserve")
        require(safety["required_memavailable_bytes"] == 16 * 1024**3 and
                safety["minimum_memavailable_bytes"] >= safety["required_memavailable_bytes"],
                "provider RAM headroom")
        require(safety["declared_os_reserve_bytes"] >= 32 * 1024**3, "provider OS reserve")
        require(safety["peak_rss_kib"] > 0 and safety["peak_pss_kib"] > 0 and
                safety["peak_pss_anon_kib"] > 0 and safety["peak_pss_file_kib_at_peak_pss"] > 0,
                "provider process memory evidence")
        require(safety["major_faults"] == safety["swap_bytes"] == safety["cgroup_memory_event_delta"] == 0,
                "provider memory pressure")
        filesystem = full_bounds["filesystem"]
        require(filesystem["required_available_bytes"] == 55 * 1024**3 and
                filesystem["minimum_available_bytes"] >= filesystem["required_available_bytes"],
                "filesystem reserve")
        storage = full_bounds["provider_storage"]
        require(storage["source_files"] == 4 and storage["directory_entries"] == 11008 and
                storage["spans"] == 33024 and storage["read_requests"] > 0 and
                storage["read_operations"] >= storage["read_requests"] and storage["read_bytes"] > 0,
                "full-model storage accounting")
        require(storage["short_reads"] == storage["io_errors"] == 0, "full-model storage error")
        terminal = full_bounds["provider_terminal"]
        for name in ("active_background_flights", "hot_pins", "cold_transfer_refs", "cold_request_refs",
                     "failed_cleanups", "transcript_dropped", "context_capacity_rejections"):
            require(terminal[name] == 0, f"provider terminal state: {name}")
        latency = full_bounds["provider_latency"]
        require(0 < latency["decode_p50_us"] <= latency["decode_p95_us"] <=
                latency["decode_p99_us"] <= latency["decode_max_us"], "decode latency percentiles")
        require(latency["decode_throughput_tps"] > 0, "decode throughput")

    checkpoints = document["checkpoints"]
    require(checkpoints["mechanism"]["status"] == "pass", "mechanism checkpoint")
    require(checkpoints["mechanism"]["final_capable"] is False, "mechanism final capability")
    if checkpoints["full_model"]["status"] == "pending":
        require(document["result"]["status"] == "in_progress", "pending full-model disposition")
        require(document["result"]["final_capable"] is False, "pending result cannot be final")
    else:
        require(matrix is not None, "missing full-model evidence")
        require(checkpoints["full_model"]["status"] == "pass", "full-model checkpoint")
        require(checkpoints["full_model"]["final_capable"] is True, "full-model final capability")
        require(all(value == "pass" for value in checkpoints["full_model"]["gates"].values()),
                "full-model gate failure")
        require(document["result"]["status"] in ("positive", "negative"), "final disposition")
        require(document["result"]["final_capable"] is True, "final result capability")

    for archive in document["archives"]:
        require(SHA256.fullmatch(archive["sha256"]) is not None, "archive SHA-256")
        require(archive["bytes"] > 0 and archive["member_count"] > 0, "archive identity")
        validate_archive_index(archive, verify_archives)


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

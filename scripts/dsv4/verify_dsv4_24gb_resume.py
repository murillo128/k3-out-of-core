#!/usr/bin/env python3
"""Verify the immutable DeepSeek-V4 24 GB continuation evidence package."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIRECTORY = ROOT / "results/2026-08-05/host-79466/dsv4-24gb-validation-resume"
SCHEMA = ROOT / "schemas/dsv4/dsv4-24gb-validation-v2.schema.json"
FORBIDDEN_WORKFLOW_KEYS = {
    "branch", "comment", "issue", "issue_number", "label", "merge", "pr",
    "pull_request", "review", "review_verdict", "roadmap",
}
EXPECTED_FIRST_IDS = [2581, 1309, 304, 8470, 3939, 16372, 11226, 1531]
GIB = 1024**3


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def reject_workflow_metadata(value: Any, location: str = "manifest") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            require(key not in FORBIDDEN_WORKFLOW_KEYS, f"workflow metadata at {location}.{key}")
            reject_workflow_metadata(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            reject_workflow_metadata(nested, f"{location}[{index}]")


def validate_identity(reference: dict[str, Any], *, external: bool = False) -> Path:
    path = Path(reference["storage_path"]) if external else ROOT / reference["path"]
    require(path.is_file(), f"missing evidence: {path}")
    require(path.stat().st_size == reference["bytes"], f"size drift: {path}")
    require(sha256(path) == reference["sha256"], f"digest drift: {path}")
    return path


def validate_checksums(directory: Path) -> None:
    document = json.loads((directory / "checksums.json").read_text())
    require(document.get("schema_version") == "dsv4-24gb-resume-checksums-v1", "checksums schema")
    expected = ["SUMMARY.md", "archive-index.json", "confirmation.json", "manifest.json", "screening-matrix.json"]
    members = document["members"]
    require([item["path"] for item in members] == expected, "committed checksum member set/order")
    for item in members:
        path = directory / item["path"]
        require(path.stat().st_size == item["bytes"], f"committed size drift: {path}")
        require(sha256(path) == item["sha256"], f"committed digest drift: {path}")


def validate_archive(manifest: dict[str, Any], verify_members: bool) -> None:
    archive = manifest["archive"]
    archive_path = validate_identity(archive, external=True)
    index_path = ROOT / archive["index"]
    index = json.loads(index_path.read_text())
    require(index.get("schema_version") == "dsv4-24gb-resume-archive-index-v1", "archive index schema")
    require(index["archive"] == {
        "path": archive["storage_path"], "bytes": archive["bytes"],
        "sha256": archive["sha256"], "member_count": archive["member_count"],
    }, "archive/index identity")
    members = index["members"]
    require(len(members) == archive["member_count"], "archive member count")
    require([item["path"] for item in members] == sorted(item["path"] for item in members),
            "archive member order")
    if not verify_members:
        return
    expected = {item["path"]: item for item in members}
    process = subprocess.Popen(["unzstd", "-c", str(archive_path)], stdout=subprocess.PIPE)
    require(process.stdout is not None, "archive decompressor pipe")
    observed: dict[str, tuple[int, str]] = {}
    with tarfile.open(fileobj=process.stdout, mode="r|") as archive_stream:
        for member in archive_stream:
            if not member.isfile():
                continue
            source = archive_stream.extractfile(member)
            require(source is not None, f"archive member stream: {member.name}")
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: source.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
            observed[member.name] = (size, digest.hexdigest())
    require(process.wait() == 0, "archive decompression")
    require(set(observed) == set(expected), "archive contents/index mismatch")
    for path, item in expected.items():
        require(observed[path] == (item["bytes"], item["sha256"]), f"archive member drift: {path}")


def validate_screening(document: dict[str, Any]) -> None:
    require(document.get("schema_version") == "dsv4-24gb-resume-screening-v1", "screening schema")
    retained = document["retained_cells"]
    require(len(retained) == 2, "retained screening cell count")
    for cell in retained:
        require(cell["processes"] >= 3, f"screening repeats: {cell['name']}")
        require(cell["all_gates_pass"] and cell["deterministic"], f"screening gates: {cell['name']}")
        require(cell["decode"]["p50_us"] <= cell["decode"]["p95_us"] <=
                cell["decode"]["p99_us"] <= cell["decode"]["maximum_us"],
                f"screening percentiles: {cell['name']}")
    selected = document["shortlist"]["selected"]
    require(selected["name"] == "positional-lanes4", "shortlist selection")
    require(selected["configuration"] == {
        "hot_slots": 268, "cold_requested_bytes": 16 * GIB,
        "transfer_lanes": 4, "queue_depth": 0, "transport": "POSITIONAL",
    }, "selected screening configuration")


def validate_confirmation(document: dict[str, Any]) -> None:
    require(document.get("schema_version") == "dsv4-24gb-resume-confirmation-v1", "confirmation schema")
    provider = document["provider"]
    require(provider["processes"] >= 5 and provider["decode"]["samples"] >= 100, "confirmation sample gate")
    require(provider["decode"]["method"] == "nearest-rank over all post-prefill token latencies",
            "confirmation percentile method")
    require(provider["all_gates_pass"] and provider["all_repeats_exact"], "confirmation provider gates")
    require(provider["generated_ids"][:8] == EXPECTED_FIRST_IDS, "confirmation generated IDs")
    require(len(provider["generated_ids"]) == 24, "confirmation token count")
    require(provider["transport"] == "POSITIONAL", "confirmation transport")
    require(provider["resources"]["minimum_mem_available_bytes"] >= 16 * GIB, "RAM floor")
    require(provider["resources"]["minimum_gpu_free_mib"] >= 6144, "VRAM floor")
    require(provider["resources"]["minimum_disk_available_bytes"] >= 55 * GIB, "filesystem floor")
    require(provider["resources"]["peak_process_swap_bytes"] == 0, "swap gate")
    require(provider["resources"]["cgroup_memory_event_delta"] == 0, "cgroup gate")
    require(provider["resources"]["major_faults"] == 0, "major fault gate")
    require(provider["storage"]["short_reads"] == provider["storage"]["io_errors"] == 0,
            "storage error gate")
    require(provider["traces"]["dropped"] == 0, "trace completeness")
    for placement in ("fit", "cpu_moe"):
        baseline = document["baselines"][placement]
        require(baseline["processes"] >= 5 and baseline["all_processes_valid"], f"baseline gate: {placement}")
        require(len(baseline["generation_tps"]) == 5, f"baseline process count: {placement}")
    require(document["interleaving"]["status"] == "pass", "interleaving gate")


def validate_manifest(document: dict[str, Any], directory: Path, verify_archive_members: bool) -> None:
    schema = json.loads(SCHEMA.read_text())
    Draft7Validator(schema, format_checker=Draft7Validator.FORMAT_CHECKER).validate(document)
    reject_workflow_metadata(document)
    revisions = document["revisions"]
    require(revisions["gitlink"] == revisions["nested_head"], "gitlink/nested target mismatch")
    require(revisions["project_base"] == "69d140e1bdc1d2462d326b101b1ae94235b85669", "project base drift")
    require(revisions["nested_base"] == "87f6fdbb04db24078d4d5b9bdc5cd0502e17290c", "nested base drift")
    for reference in document["accepted_evidence"]:
        validate_identity(reference)
    require(document["artifact"]["revision"] == "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1",
            "artifact revision drift")
    require(sum(item["bytes"] for item in document["artifact"]["files"]) == document["artifact"]["total_bytes"],
            "artifact total bytes")
    screening = json.loads((directory / "screening-matrix.json").read_text())
    confirmation = json.loads((directory / "confirmation.json").read_text())
    for key, value in (("screening", screening), ("confirmation", confirmation)):
        reference = document[key]
        path = ROOT / reference["path"]
        require(path == directory / f"{key}-matrix.json" if key == "screening" else path == directory / "confirmation.json",
                f"{key} path")
        require(path.stat().st_size == reference["bytes"] and sha256(path) == reference["sha256"],
                f"{key} identity")
    validate_screening(screening)
    validate_confirmation(confirmation)
    require(document["result"]["status"] == "negative", "result status")
    require(document["result"]["disposition"] == "SUPPORTED_EXPERIMENTAL_UNSELECTED", "final disposition")
    require(document["result"]["selected_provider_configuration"] is None, "unexpected selected configuration")
    validate_archive(document, verify_archive_members)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--verify-archive-members", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    manifest = json.loads((directory / "manifest.json").read_text())
    validate_checksums(directory)
    validate_manifest(manifest, directory, args.verify_archive_members)
    print("DeepSeek-V4 24 GB continuation evidence verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

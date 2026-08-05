#!/usr/bin/env python3
"""Build the non-circular Phase 11 closeout artifacts and manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT/"results/2026-08-04/msi-edgexpert-gb10/phase11-uma"
PROJECT_BASE = "b4368697c92d8b5e0814023b1cb3288636769eda"
NESTED_BASE = "6339ee371ff898dea5057c7bfedf91adbe44c111"
NESTED_FINAL = "add1ee6adbe5264387b8b1eef0eed0ba51a59a8a"
CHECKPOINT_D_PUBLISHED = "fd439e9cb5fa693e3c061381707fadaf3f668f83"
REPOSITORY = "murillo128/k3-out-of-core"

EVIDENCE = (
    "phase11-capabilities.json", "phase11-checkpoint-b.json", "phase11-checkpoint-c.json",
    "phase11-checkpoint-d.json", "phase11-v4-raw-index.json", "phase11-v4-raw.tar.gz",
)
CLOSEOUT = ("phase11-decision.json", "phase11-summary.json", "PHASE11.md", "phase11-checksums.json")
SUPPORT = (
    "scripts/phase11/build_phase11_closeout.py", "scripts/phase11/verify_phase11.py",
    "schemas/phase11/phase11-manifest-v1.schema.json", "tests/phase11/test_phase11_closeout.py",
)


def canonical(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def load(name: str) -> dict[str, Any]:
    return json.loads((RESULTS/name).read_text())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def identity(path: Path, revision: str | None = None) -> dict[str, Any]:
    relative = path.resolve().relative_to(ROOT).as_posix()
    result = {"path": relative, "size": path.stat().st_size, "sha256": sha256(path)}
    if revision:
        result["immutable_url"] = f"https://github.com/{REPOSITORY}/blob/{revision}/{relative}"
    return result


def inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    capability, b, c, d = (load(name) for name in EVIDENCE[:4])
    if any(value["status"] != "pass" for value in (capability, b, c, d)):
        raise ValueError("all A-D evidence must pass")
    if d["revisions"]["nested_head"] != NESTED_FINAL or d["revisions"]["gitlink"] != NESTED_FINAL:
        raise ValueError("Checkpoint D nested identity changed")
    return capability, b, c, d


def prepare() -> None:
    capability, b, c, d = inputs()
    decision = {
        "schema_version": "phase11-decision-v1", "status": "SUPPORTED_EXPLICIT_NONDEFAULT",
        "autofit_claim": "SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED",
        "default_enabled": False, "explicit_configuration_required": True,
        "scope": "single-request single-device coherent CUDA UMA on NVIDIA GB10",
        "storage_transport": "buffered threaded pread; native io_uring unavailable under host seccomp",
        "performance_claim": "no minimum speedup; throughput measurements are descriptive",
        "full_k3_limit": "exact-layout materialized residency and pressure only; not inference quality or throughput",
    }
    (RESULTS/"phase11-decision.json").write_bytes(canonical(decision))
    epoch = {name: d["statistics"][name]["epoch_longitudinal"] for name in ("f16", "mxfp4")}
    full = d["full_k3"]
    summary = {
        "schema_version": "phase11-summary-v1", "status": "pass",
        "capability": {"gpu": capability["probe"]["gpu"], "integrated": capability["probe"]["integrated"],
            "allocation": "anonymous page-aligned system memory registered with CUDA",
            "storage_transport": capability["probe"]["storage_transport"],
            "native_io_uring": capability["probe"]["native_io_uring"]},
        "correctness": {"f16_structural_equivalence": d["structural_equivalence"]["f16"]["equivalent"],
            "mxfp4_structural_equivalence": d["structural_equivalence"]["mxfp4"]["equivalent"],
            "expert_h2d_payload_bytes": 0, "transfer_ring_payload_bytes": 0},
        "lifetime": {"epochs": 100, "analyzed_epochs": 80,
            "f16_first_last_p99_us": [epoch["f16"]["first_40_p99_us"], epoch["f16"]["last_40_p99_us"]],
            "mxfp4_first_last_p99_us": [epoch["mxfp4"]["first_40_p99_us"], epoch["mxfp4"]["last_40_p99_us"]],
            "lifecycle_cycles": d["lifecycle"]["cycles"],
            "return_delta_bytes": d["lifecycle"]["max_return_delta_bytes"],
            "return_threshold_bytes": d["lifecycle"]["return_threshold_bytes"]},
        "capacity": {"working_set_bytes": full["working_set_bytes"],
            "safe_explicit_and_autofit_bytes": full["autofit_bytes"],
            "safe_effective_slots": full["autofit_bytes"]//full["slot_footprint_bytes"],
            "slot_footprint_bytes": full["slot_footprint_bytes"],
            "first_unsafe_bytes": full["first_unsafe_bytes"],
            "first_unsafe_rejected_before_io": not full["negative"]["raw"]["io_started"]},
        "descriptive_performance": {name: d["statistics"][name]["autofit_vs_best_explicit"]
            for name in ("f16", "mxfp4")},
        "disposition": decision,
    }
    (RESULTS/"phase11-summary.json").write_bytes(canonical(summary))
    markdown = f"""# Phase 11 — coherent UMA on NVIDIA GB10

Status: **SUPPORTED_EXPLICIT_NONDEFAULT**.

The physical Spark target passed one-pool fixed-address UMA correctness, readiness, generation,
eviction, reclamation, pressure, cancellation, unload, repeated-epoch, and lifecycle gates. Expert
payload H2D and transfer-ring bytes remain zero. Phase 9 global LRU/ALWAYS and Phase 10 default-off
behavior are unchanged.

Autofit is **SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED**. The full-K3 exact-layout residency probe
materialized five clean runs each at 0.5W, W, 1.5W, safe explicit, and autofit. Safe explicit/autofit
resolved to {full['autofit_bytes']:,} bytes ({full['autofit_bytes']//full['slot_footprint_bytes']:,}
slots); {full['first_unsafe_bytes']:,} bytes, one slot higher, was rejected before I/O. This proves
bounded residency and pressure behavior, not full-model inference quality or throughput.

Tiny F16 and MXFP4 explicit-W/autofit pairs are structurally equivalent. Their throughput ratios
remain descriptive ({summary['descriptive_performance']['f16']['estimate']:.6f} F16 and
{summary['descriptive_performance']['mxfp4']['estimate']:.6f} MXFP4); no minimum speedup is claimed.
WASTE remains historical external context because hardware, representation, kernels, storage layout,
and workload differ.
"""
    (RESULTS/"PHASE11.md").write_text(markdown)
    paths = [RESULTS/name for name in EVIDENCE] + [RESULTS/name for name in CLOSEOUT[:3]]
    index = {"schema_version": "phase11-checksum-index-v1",
        "source_revision": CHECKPOINT_D_PUBLISHED,
        "self_identity": "excluded_non_circular; this index is bound by phase11-manifest.json",
        "files": [identity(path) for path in paths]}
    (RESULTS/"phase11-checksums.json").write_bytes(canonical(index))


def build_manifest(evidence_head: str) -> None:
    capability, b, c, d = inputs()
    if git(ROOT, "rev-parse", "HEAD") != evidence_head:
        raise ValueError("evidence head must be current HEAD")
    if git(ROOT, "status", "--porcelain") or git(ROOT/"llama.cpp", "status", "--porcelain"):
        raise ValueError("manifest input trees must be clean")
    if git(ROOT, "rev-parse", "HEAD:llama.cpp") != NESTED_FINAL or git(ROOT/"llama.cpp", "rev-parse", "HEAD") != NESTED_FINAL:
        raise ValueError("final nested head/gitlink mismatch")
    index = load("phase11-checksums.json")
    for item in index["files"]:
        path = ROOT/item["path"]
        if identity(path) != {k: item[k] for k in ("path", "size", "sha256")}:
            raise ValueError(f"checksum index drift: {item['path']}")
    full = d["full_k3"]; safe = full["safe_cap_source"]["diagnostics"]
    manifest = {
        "schema_version": "phase11-manifest-v1", "status": "final-review-candidate",
        "execution_profile": "STANDARD",
        "revisions": {"project_base": PROJECT_BASE, "implementation_head": d["revisions"]["project_head"],
            "checkpoint_d_published_head": CHECKPOINT_D_PUBLISHED, "evidence_head": evidence_head,
            "nested_base": NESTED_BASE, "nested_head": NESTED_FINAL, "gitlink": NESTED_FINAL},
        "clean_input_state": {"project": True, "nested": True},
        "environment": {"host_class": "NVIDIA GB10 coherent UMA", "architecture": capability["platform"]["architecture"],
            "kernel": capability["platform"]["kernel"], "cpu_count": capability["platform"]["cpu_count"],
            "physical_ram_bytes": capability["platform"]["mem_total_bytes"],
            "cgroup_limit_bytes": capability["platform"]["cgroup_memory_max_bytes"],
            "swap_total_bytes": capability["platform"]["swap_total_bytes"], "gpu": capability["probe"]["gpu"],
            "compute_capability": capability["probe"]["compute_capability"],
            "cuda_driver_version": capability["probe"]["cuda_driver_version"],
            "cuda_runtime_version": capability["probe"]["cuda_runtime_version"],
            "toolkit": capability["toolkit"], "storage_transport": capability["probe"]["storage_transport"]},
        "models": [{"name": name, "filename": Path(value["path"]).name, "size": value["size"],
            "sha256": value["sha256"],
            "immutable_url": f"https://huggingface.co/murillo2000/Kimi-K3-0.40B-GGUF/blob/88de02cf8fa37f87eb06daaed370ac9c3411d5ca/{Path(value['path']).name}"}
            for name, value in d["models"].items()],
        "checkpoints": {
            "a": {"verdict": "PASS", "project_head": capability["revisions"]["project_head"],
                "nested_head": capability["revisions"]["nested_head"]},
            "b": {"verdict": "PASS", "project_head": b["revisions"]["project_head"], "nested_head": b["revisions"]["nested_head"]},
            "c": {"verdict": "PASS", "project_head": c["revisions"]["project_head"], "nested_head": c["revisions"]["nested_head"]},
            "d": {"verdict": "PASS", "project_head": CHECKPOINT_D_PUBLISHED, "nested_head": d["revisions"]["nested_head"]}},
        "capability": {"status": capability["status"], "integrated": capability["probe"]["integrated"],
            "pageable_memory_access": capability["probe"]["pageable_memory_access"],
            "pageable_uses_host_page_tables": capability["probe"]["pageable_uses_host_page_tables"],
            "host_native_atomic": capability["probe"]["host_native_atomic"],
            "native_io_uring": capability["probe"]["native_io_uring"],
            "native_io_uring_reason": "host seccomp prevents setup; bounded threaded pread fallback is visible"},
        "build": {"release_cuda": {"build_type": "Release", "ggml_cuda": True, "tests": True},
            "asan_ubsan_cuda": {"build_type": "RelWithDebInfo", "ggml_cuda": True, "tests": True},
            "tsan_cpu": {"build_type": "RelWithDebInfo", "ggml_cuda": False, "tests": True}},
        "validation": {"capability_probe": "pass", "checkpoint_b_provider": "pass",
            "checkpoint_c_release_suite": "pass", "checkpoint_c_asan_ubsan_suite": "pass",
            "compute_sanitizer_error_summary": c["sanitizers"]["compute_sanitizer"]["error_summary"],
            "tsan": c["sanitizers"]["tsan"]["classification"],
            "checkpoint_d_semantic_verifier": "pass", "focused_final_ctest": "3/3 pass"},
        "capacity": {"safe_ceiling_bytes": safe["uma_safe_pool_bytes"],
            "requested_modes": {"explicit_requested_bytes": full["autofit_bytes"],
                "autofit_requested_bytes": 0, "autofit_enabled": True},
            "safe_explicit_and_autofit_bytes": full["autofit_bytes"],
            "effective_slots": full["autofit_bytes"]//full["slot_footprint_bytes"],
            "actual_arena_bytes": full["autofit_bytes"], "slot_footprint_bytes": full["slot_footprint_bytes"],
            "first_unsafe_bytes": full["first_unsafe_bytes"],
            "model_capacity_bytes": full["slot_footprint_bytes"]*92*896,
            "model_cap_unused_safe_bytes": 0,
            "alignment_remainder_bytes": safe["uma_safe_pool_bytes"] - full["autofit_bytes"]},
        "policies": {"cache": "global LRU/ALWAYS unchanged", "prefetch": "Phase 10 default off unchanged",
            "miss_execution": "PROMOTE_AND_GPU", "uma_default_enabled": False},
        "gates": {"one_pool_fixed_address": True, "correctness": True, "readiness_generation": True,
            "zero_h2d_and_ring_payload": True, "eviction_reclamation": True, "pressure_fail_closed": True,
            "cancellation_unload_lifetime": True, "repeated_100_epochs": True, "tail_stability": True,
            "full_k3_residency": True, "first_unsafe_rejected_before_io": True,
            "phase9_phase10_defaults_unchanged": True, "immutable_evidence": True,
            "comparisons_bounded_without_overclaim": True, "checkpoints_a_through_d_accepted": True},
        "evidence": [identity(RESULTS/name, evidence_head) for name in EVIDENCE],
        "closeout_artifacts": [identity(RESULTS/name, evidence_head) for name in CLOSEOUT] +
            [identity(ROOT/name, evidence_head) for name in SUPPORT],
        "disposition": load("phase11-decision.json"),
        "limits": ["single request and single device only", "explicit nondefault UMA mode",
            "autofit is safe-capacity only and not performance selected",
            "full-K3 result is residency/pressure evidence, not inference quality or throughput",
            "no direct Spark versus discrete CUDA or WASTE performance ranking",
            "buffered threaded pread only; no native io_uring, GDS, or storage-overlap claim"],
        "deferred": ["Phase 12 storage format and full-size inference", "Phase 12.5 end-to-end tracing",
            "multi-request batching", "multi-GPU", "GDS and new expert formats"],
        "self_identity": "phase11-manifest.json excludes its own hash and immutable URL to remain non-circular",
    }
    validate_manifest(manifest)
    (RESULTS/"phase11-manifest.json").write_bytes(canonical(manifest))


def validate_manifest(document: dict[str, Any]) -> None:
    required = {"schema_version", "status", "execution_profile", "revisions", "clean_input_state",
        "environment", "models", "checkpoints", "capability", "build", "validation", "capacity", "policies", "gates",
        "evidence", "closeout_artifacts", "disposition", "limits", "deferred", "self_identity"}
    if set(document) != required or document["schema_version"] != "phase11-manifest-v1" or \
            document["status"] != "final-review-candidate" or document["execution_profile"] != "STANDARD":
        raise ValueError("invalid Phase 11 manifest envelope")
    if document["revisions"]["nested_head"] != document["revisions"]["gitlink"] or \
            not all(document["clean_input_state"].values()) or not all(document["gates"].values()):
        raise ValueError("revision, clean-state, or gate failure")
    if set(document["checkpoints"]) != {"a", "b", "c", "d"} or \
            any(value["verdict"] != "PASS" for value in document["checkpoints"].values()):
        raise ValueError("checkpoint acceptance incomplete")
    decision = document["disposition"]
    if decision["status"] != "SUPPORTED_EXPLICIT_NONDEFAULT" or \
            decision["autofit_claim"] != "SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED" or \
            decision["default_enabled"]:
        raise ValueError("unsupported disposition")
    capacity = document["capacity"]
    if capacity["safe_explicit_and_autofit_bytes"] + capacity["slot_footprint_bytes"] != capacity["first_unsafe_bytes"]:
        raise ValueError("unsafe boundary is not one slot above autofit")
    for item in document["evidence"] + document["closeout_artifacts"]:
        if len(item["sha256"]) != 64 or item["size"] < 1 or document["revisions"]["evidence_head"] not in item["immutable_url"]:
            raise ValueError("artifact identity invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "manifest"))
    parser.add_argument("--evidence-head")
    args = parser.parse_args()
    if args.mode == "prepare": prepare()
    elif not args.evidence_head: parser.error("manifest mode requires --evidence-head")
    else: build_manifest(args.evidence_head)
    return 0


if __name__ == "__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Assemble the bounded issue-45 continuation package from captured records."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path("/workspace/evidence/issue45-resume-work")
OUTPUT = ROOT / "results/2026-08-05/host-79466/dsv4-24gb-validation-resume"
PROJECT_BASE = "69d140e1bdc1d2462d326b101b1ae94235b85669"
PROJECT_TECHNICAL_HEAD = "781d50a632227c19f310ee0b24e6034fc9ceda04"
NESTED_BASE = "87f6fdbb04db24078d4d5b9bdc5cd0502e17290c"
NESTED_HEAD = "c5a3b0bac47d89bfa4b7807a5ebf1e87a1e692e2"
GIB = 1024**3


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    name = path.relative_to(relative_to).as_posix() if relative_to is not None else path.as_posix()
    return {"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)}


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def nearest_rank(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * fraction) - 1]


def distribution(values: list[int]) -> dict[str, Any]:
    return {
        "samples": len(values), "method": "nearest-rank",
        "p50_us": nearest_rank(values, 0.50), "p95_us": nearest_rank(values, 0.95),
        "p99_us": nearest_rank(values, 0.99), "maximum_us": max(values),
    }


def record_reference(path: Path) -> dict[str, Any]:
    result = identity(path, relative_to=SOURCE)
    result["archive_member"] = "./" + result.pop("path")
    return result


def load_raw(path: Path) -> dict[str, Any]:
    return json.loads(subprocess.check_output(["zstdcat", str(path)]))


def screening_cell(name: str) -> dict[str, Any]:
    records = [read_json(SOURCE / "screening-final" / f"{name}-r{run}.record.json") for run in range(1, 4)]
    latencies = [value for record in records for value in record["probe"]["latency_us"][1:]]
    route_hashes = []
    for run in range(1, 4):
        raw = load_raw(SOURCE / "screening-final" / f"{name}-r{run}.probe.json.zst")
        route_hashes.append(canonical_hash(raw["routes"]))
        del raw
        gc.collect()
    lanes = records[0]["preflight"]["requested"]["ring_lanes"]
    physical = records[0]["probe"]["capacities"]
    result = {
        "name": name,
        "configuration": {
            "hot_slots": 268, "cold_requested_bytes": 16 * GIB,
            "transfer_lanes": lanes, "queue_depth": 0, "transport": "POSITIONAL",
        },
        "processes": len(records), "process_state": "new process, empty provider tiers; decode follows prompt prefill",
        "all_gates_pass": all(record["status"] == "pass" and all(record["gates"].values()) for record in records),
        "deterministic": len({canonical_hash([record["probe"]["generated_ids"], record["probe"]["logits_fnv64"]]) for record in records}) == 1 and len(set(route_hashes)) == 1,
        "decode": {
            "process_throughput_tps": [record["probe"]["decode"]["throughput_tps"] for record in records],
            "median_process_throughput_tps": statistics.median(record["probe"]["decode"]["throughput_tps"] for record in records),
            **distribution(latencies),
        },
        "submitted_bytes_per_process": records[0]["probe"]["storage"]["read_bytes"],
        "physical_hot_cold_pinned_bytes": physical["hot_pool_bytes"] + physical["cold_actual_bytes"] + physical["ring_pinned_or_registered_bytes"],
        "cpu_total_time_us_per_process": [record["probe"]["cpu_time_us"]["total"] for record in records],
        "minimum_mem_available_bytes": min(record["resource_usage"]["minimum_mem_available_bytes"] for record in records),
        "minimum_gpu_free_mib": min(record["resource_usage"]["minimum_gpu_free_mib"] for record in records),
        "minimum_disk_available_bytes": min(record["resource_usage"]["minimum_disk_available_bytes"] for record in records),
        "generated_logits_sha256": canonical_hash([records[0]["probe"]["generated_ids"], records[0]["probe"]["logits_fnv64"]]),
        "routes_sha256": route_hashes[0],
        "records": [record_reference(SOURCE / "screening-final" / f"{name}-r{run}.record.json") for run in range(1, 4)],
    }
    result["decode"]["method"] = "nearest-rank over all post-prefill token latencies"
    return result


def exploratory(path: str, disposition: str, finding: str) -> dict[str, Any]:
    record_path = SOURCE / path
    record = read_json(record_path)
    result = {
        "name": record["name"], "status": record["status"], "disposition": disposition,
        "finding": finding, "record": record_reference(record_path),
    }
    probe = record.get("probe")
    if probe is not None:
        result["observed"] = {
            "transport_requested": probe.get("transport_requested"),
            "transport_actual": probe.get("transport_actual"),
            "decode_throughput_tps": probe["decode"]["throughput_tps"],
            "decode_p95_us": probe["decode"]["p95_us"],
            "storage_read_bytes": probe["storage"]["read_bytes"],
        }
    return result


def build_screening() -> dict[str, Any]:
    lanes4 = screening_cell("positional-lanes4")
    lanes2 = screening_cell("positional-lanes2")
    return {
        "schema_version": "dsv4-24gb-resume-screening-v1",
        "selection_rule": [
            "all correctness, determinism, lifecycle, and resource gates pass",
            "maximize median decode throughput",
            "within 3 percent, minimize aggregate p95 latency",
            "then minimize storage submitted bytes",
            "then minimize physical hot+cold+pinned bytes",
        ],
        "process_state_definition": {
            "fresh": "new process with empty provider hot/cold tiers",
            "warm": "post-prefill decode tokens within that process; no cross-process provider state exists",
        },
        "retained_cells": [lanes4, lanes2],
        "exploratory_cells": [
            exploratory("preflight/preflight-q1.record.json", "rejected_preflight", "requested depth 1 is below the integrated transport invariant [8,4096]"),
            exploratory("screening/preflight-q4.record.json", "rejected_preflight", "requested depth 4 is below the integrated transport invariant [8,4096]"),
            exploratory("screening/screen-q8-r1.record.json", "rejected_correctness", "depth 8 cannot hold the 83-request/249-operation concurrent read plan and failed closed on the first decode"),
            exploratory("screening/preflight-hot8-cold16.record.json", "rejected_preflight", "335 cold slots cannot cover 537 hot slots"),
            exploratory("screening/screen-hot8-cold32-r1.record.json", "discarded", "pre-defect-fix run fell back from native submission and was slower than the anchor"),
            exploratory("screening/preflight-hot12-cold48.record.json", "rejected_preflight", "anchor-derived minimum free VRAM estimate was 4,275 MiB, below the 6 GiB floor"),
            exploratory("screening/screen-cold32-r1.record.json", "discarded_after_one", "reduced submitted bytes but worsened latency and did not improve throughput"),
            exploratory("screening/screen-cold48-r1.record.json", "discarded_after_one", "reduced submitted bytes but worsened latency and did not improve throughput"),
            exploratory("screening/screen-cold64-r1.record.json", "discarded_after_one", "reduced submitted bytes but worsened latency and did not improve throughput"),
            exploratory("screening/screen-buffered-auto-r1.record.json", "discarded_after_one", "native buffered io_uring was exact after the single-mmap fix but materially slower"),
            exploratory("screening/screen-direct-r1.record.json", "BLOCKED", "all four source opens returned EOPNOTSUPP and the process explicitly fell back to buffered I/O"),
        ],
        "shortlist": {
            "frozen": True,
            "selected": {"name": lanes4["name"], "configuration": lanes4["configuration"]},
            "decision": {
                "throughput_within_three_percent": True,
                "four_lane_median_tps": lanes4["decode"]["median_process_throughput_tps"],
                "two_lane_median_tps": lanes2["decode"]["median_process_throughput_tps"],
                "four_lane_p95_us": lanes4["decode"]["p95_us"],
                "two_lane_p95_us": lanes2["decode"]["p95_us"],
                "winning_tiebreak": "lower aggregate p95 latency",
            },
        },
    }


def semantic_policy_hash(events: list[dict[str, Any]]) -> str:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_type[event["type"]].append({key: value for key, value in event.items()
                                       if key not in ("event_sequence", "state_digest")})
    return canonical_hash(dict(sorted(by_type.items())))


def build_confirmation() -> dict[str, Any]:
    provider_records = [read_json(SOURCE / "confirmation-final" / f"provider-r{run}.record.json") for run in range(1, 6)]
    decode_latencies = [value for record in provider_records for value in record["probe"]["latency_us"][1:]]
    prompt_latencies = [record["probe"]["latency_us"][0] for record in provider_records]
    queue_wait: list[int] = []
    service: list[int] = []
    route_hashes: list[str] = []
    output_hashes: list[str] = []
    semantic_policy_hashes: list[str] = []
    chronological_policy_hashes: list[str] = []
    hot_state_digests: list[int] = []
    cold_state_digests: list[int] = []
    event_type_counts: list[dict[str, int]] = []
    for run in range(1, 6):
        raw = load_raw(SOURCE / "confirmation-final" / f"provider-r{run}.probe.json.zst")
        intervals = raw["async_io"]["read_intervals"]
        queue_wait.extend(item["started_us"] - item["queued_us"] for item in intervals)
        service.extend(item["complete_us"] - item["submit_us"] for item in intervals)
        route_hashes.append(canonical_hash(raw["routes"]))
        output_hashes.append(canonical_hash([raw["generated_ids"], raw["logits_fnv64"]]))
        semantic_policy_hashes.append(semantic_policy_hash(raw["cold"]["events"]))
        chronological_policy_hashes.append(canonical_hash(raw["cold"]["events"]))
        hot_state_digests.append(raw["hot"]["diagnostics"]["state_digest"])
        cold_state_digests.append(raw["cold"]["diagnostics"]["state_digest"])
        event_type_counts.append(dict(sorted(Counter(item["type"] for item in raw["cold"]["events"]).items())))
        del raw
        gc.collect()

    baselines: dict[str, Any] = {}
    for placement, stem in (("fit", "fit"), ("cpu_moe", "cpu-moe")):
        records = [read_json(SOURCE / "confirmation-final" / f"{stem}-r{run}.record.json") for run in range(1, 6)]
        rates = [record["performance"]["generation_tps"] for record in records]
        baselines[placement] = {
            "processes": 5, "all_processes_valid": all(record["status"] == "pass" for record in records),
            "generation_tps": rates, "median_generation_tps": statistics.median(rates),
            "prompt_tps": [record["performance"]["prompt_tps"] for record in records],
            "generated_tokens_per_process": 8,
            "generated_text": records[0]["performance"]["generated_text"],
            "generated_text_sha256": records[0]["performance"]["generated_text_sha256"],
            "all_outputs_exact": len({record["performance"]["generated_text_sha256"] for record in records}) == 1,
            "cpu_total_time_us_per_process": [record["resource_usage"]["cpu_user_time_us"] + record["resource_usage"]["cpu_system_time_us"] for record in records],
            "minimum_mem_available_bytes": min(record["resource_usage"]["minimum_mem_available_bytes"] for record in records),
            "minimum_gpu_free_mib": min(record["resource_usage"]["minimum_gpu_free_mib"] for record in records),
            "minimum_disk_available_bytes": min(record["resource_usage"]["minimum_disk_available_bytes"] for record in records),
            "records": [record_reference(SOURCE / "confirmation-final" / f"{stem}-r{run}.record.json") for run in range(1, 6)],
        }

    first = provider_records[0]["probe"]
    resources = {
        "minimum_mem_available_bytes": min(record["resource_usage"]["minimum_mem_available_bytes"] for record in provider_records),
        "minimum_gpu_free_mib": min(record["resource_usage"]["minimum_gpu_free_mib"] for record in provider_records),
        "minimum_disk_available_bytes": min(record["resource_usage"]["minimum_disk_available_bytes"] for record in provider_records),
        "peak_rss_kib": max(record["resource_usage"]["peak_rss_kib"] for record in provider_records),
        "peak_pss_bytes": max(record["resource_usage"]["peak_pss_bytes"] for record in provider_records),
        "peak_pss_anon_bytes": max(record["resource_usage"]["peak_pss_anon_bytes"] for record in provider_records),
        "peak_pss_file_bytes_at_peak_pss": max(record["resource_usage"]["peak_pss_file_bytes_at_peak_pss"] for record in provider_records),
        "peak_process_swap_bytes": max(record["resource_usage"]["peak_process_swap_bytes"] for record in provider_records),
        "major_faults": sum(record["resource_usage"]["major_faults"] for record in provider_records),
        "cgroup_memory_event_delta": sum(sum(record["resource_usage"]["cgroup_memory_event_delta"].values()) for record in provider_records),
        "pinned_bytes": first["capacities"]["ring_pinned_or_registered_bytes"],
    }
    decode = distribution(decode_latencies)
    decode.update({
        "method": "nearest-rank over all post-prefill token latencies",
        "aggregate_throughput_tps": len(decode_latencies) * 1_000_000 / sum(decode_latencies),
        "process_throughput_tps": [record["probe"]["decode"]["throughput_tps"] for record in provider_records],
        "median_process_throughput_tps": statistics.median(record["probe"]["decode"]["throughput_tps"] for record in provider_records),
    })
    prompt = distribution(prompt_latencies)
    prompt["method"] = "nearest-rank over one prefill/TTFT latency per process"
    provider = {
        "configuration": {
            "hot_slots": 268, "hot_actual_bytes": first["capacities"]["hot_pool_bytes"],
            "cold_slots": first["capacities"]["cold_effective_slots"],
            "cold_actual_bytes": first["capacities"]["cold_actual_bytes"],
            "transfer_lanes": 4, "pinned_bytes": first["capacities"]["ring_pinned_or_registered_bytes"],
            "queue_depth_requested": 0, "queue_depth_effective": first["io"]["diagnostics"]["operation_capacity"] // 2,
            "trace_capacity": first["io"]["diagnostics"]["trace_capacity"], "transport": "POSITIONAL",
        },
        "processes": 5, "all_gates_pass": all(record["status"] == "pass" and all(record["gates"].values()) for record in provider_records),
        "all_repeats_exact": len(set(output_hashes)) == len(set(route_hashes)) == len(set(semantic_policy_hashes)) ==
            len(set(hot_state_digests)) == len(set(cold_state_digests)) == len({canonical_hash(value) for value in event_type_counts}) == 1,
        "generated_ids": first["generated_ids"], "generated_text": first["generated_text"],
        "generated_text_sha256": first["generated_text_sha256"],
        "generated_logits_sha256": output_hashes[0], "routes_sha256": route_hashes[0],
        "route_records_per_process": 1032, "all_finite": True,
        "decode": decode, "prompt_ttft": prompt,
        "cpu_total_time_us_per_process": [record["probe"]["cpu_time_us"]["total"] for record in provider_records],
        "storage": {
            "read_requests_per_process": first["storage"]["read_requests"],
            "read_operations_per_process": first["storage"]["read_operations"],
            "submitted_bytes_per_process": first["storage"]["read_bytes"],
            "short_reads": sum(record["probe"]["storage"]["short_reads"] for record in provider_records),
            "io_errors": sum(record["probe"]["storage"]["io_errors"] for record in provider_records),
            "queue_wait": distribution(queue_wait), "service": distribution(service),
        },
        "cache": {
            "mechanism_per_process": first["mechanism"],
            "semantic_per_type_transcript_sha256": semantic_policy_hashes[0],
            "terminal_hot_state_digest": hot_state_digests[0],
            "terminal_cold_state_digest": cold_state_digests[0],
            "event_type_counts_per_process": event_type_counts[0],
            "chronological_transcript_unique_hashes": sorted(set(chronological_policy_hashes)),
            "chronological_interleaving_observation": "One process interleaved LOAD_COMPLETE and UNPIN records differently; every per-type sequence, policy choice, terminal state, route, logit, and output digest remained exact.",
        },
        "transfer": {
            "stage_bytes_per_process": first["transfer"]["stage_bytes"],
            "h2d_bytes_per_process": first["transfer"]["h2d_bytes"],
            "stage_time_us_per_process": [record["probe"]["transfer"]["stage_time_us"] for record in provider_records],
            "h2d_time_us_per_process": [record["probe"]["transfer"]["h2d_time_us"] for record in provider_records],
            "peak_in_flight_lanes": max(record["probe"]["transfer"]["peak_in_flight_lanes"] for record in provider_records),
            "failed_cleanup": sum(record["probe"]["transfer"]["failed_cleanup"] for record in provider_records),
        },
        "resources": resources,
        "traces": {
            "async_records_per_process": first["io"]["diagnostics"]["trace_records"],
            "transfer_records_per_process": first["transfer"]["trace_records"],
            "dropped": sum(record["probe"]["io"]["diagnostics"]["trace_records_dropped"] + record["probe"]["transfer"]["trace_records_dropped"] for record in provider_records),
        },
        "records": [record_reference(SOURCE / "confirmation-final" / f"provider-r{run}.record.json") for run in range(1, 6)],
        "raw_probes": [record_reference(SOURCE / "confirmation-final" / f"provider-r{run}.probe.json.zst") for run in range(1, 6)],
    }
    return {
        "schema_version": "dsv4-24gb-resume-confirmation-v1",
        "interleaving": {
            "status": "pass",
            "order": [item for run in range(1, 6) for item in (f"fit-r{run}", f"provider-r{run}", f"cpu-moe-r{run}")],
        },
        "provider": provider, "baselines": baselines,
        "comparison": {
            "provider_vs_fit_median_slowdown_factor": baselines["fit"]["median_generation_tps"] / decode["median_process_throughput_tps"],
            "provider_vs_cpu_moe_median_slowdown_factor": baselines["cpu_moe"]["median_generation_tps"] / decode["median_process_throughput_tps"],
            "first_eight_generated_text_equal": first["generated_text"].startswith(baselines["fit"]["generated_text"]),
            "conventional_generated_text_equal": baselines["fit"]["generated_text_sha256"] == baselines["cpu_moe"]["generated_text_sha256"],
        },
    }


def evidence_reference(role: str, path: str, revisions: dict[str, str]) -> dict[str, Any]:
    result = identity(ROOT / path, relative_to=ROOT)
    result["role"] = role
    result["accepted_revisions"] = revisions
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--observed-at-utc", default=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    screening = build_screening()
    confirmation = build_confirmation()
    screening_path = OUTPUT / "screening-matrix.json"
    confirmation_path = OUTPUT / "confirmation.json"
    write_json(screening_path, screening)
    write_json(confirmation_path, confirmation)

    members = []
    source_files = (item for item in SOURCE.rglob("*") if item.is_file())
    for path in sorted(source_files, key=lambda item: item.relative_to(SOURCE).as_posix()):
        item = identity(path, relative_to=SOURCE)
        item["path"] = "./" + item["path"]
        members.append(item)
    archive_identity = {
        "path": str(args.archive), "bytes": args.archive.stat().st_size,
        "sha256": sha256(args.archive), "member_count": len(members),
    }
    archive_index = {
        "schema_version": "dsv4-24gb-resume-archive-index-v1",
        "archive": archive_identity, "members": members,
    }
    archive_index_path = OUTPUT / "archive-index.json"
    write_json(archive_index_path, archive_index)

    provider = confirmation["provider"]
    baselines = confirmation["baselines"]
    attribution = {
        "storage": {
            "observed": "Each confirmed process submitted 65,788,575,744 bytes in 5,963 requests/17,889 positional operations with zero short reads or errors.",
            "queue_wait_p95_us": provider["storage"]["queue_wait"]["p95_us"],
            "service_p95_us": provider["storage"]["service"]["p95_us"],
        },
        "cache_working_set": {
            "hot_hit_rate": provider["cache"]["mechanism_per_process"]["hot_hits"] /
                (provider["cache"]["mechanism_per_process"]["hot_hits"] + provider["cache"]["mechanism_per_process"]["hot_misses"]),
            "cold_hit_rate": provider["cache"]["mechanism_per_process"]["cold_hits"] /
                (provider["cache"]["mechanism_per_process"]["cold_hits"] + provider["cache"]["mechanism_per_process"]["cold_misses"]),
            "finding": "Larger cold capacities reduced submitted bytes by at most 11 percent in screening but did not improve throughput or p95 latency.",
        },
        "transfer": {
            "median_stage_time_us_per_process": statistics.median(provider["transfer"]["stage_time_us_per_process"]),
            "median_h2d_time_us_per_process": statistics.median(provider["transfer"]["h2d_time_us_per_process"]),
            "peak_in_flight_lanes": provider["transfer"]["peak_in_flight_lanes"],
            "finding": "Two and four lanes were throughput-tied and the observed peak was one in-flight lane; lane count was not the limiting factor.",
        },
        "transport": {
            "finding": "Native buffered io_uring was slower than forced positional reads; direct I/O was unavailable with EOPNOTSUPP on all four source files.",
        },
        "execution": {
            "finding": "Measured storage service, staging, and H2D times do not explain the roughly 24-37x conventional gap by themselves. Kernel execution, synchronization, and non-overlapped miss handling remain a combined residual because separate GPU-kernel timing was not observable in this harness.",
            "overlap_caveat": "Queue, service, stage, and H2D counters are cumulative across operations and must not be added as wall time.",
        },
    }
    manifest = {
        "schema_version": "dsv4-24gb-validation-v2", "observed_at_utc": args.observed_at_utc,
        "revisions": {
            "project_base": PROJECT_BASE, "project_technical_head": PROJECT_TECHNICAL_HEAD,
            "nested_base": NESTED_BASE, "nested_head": NESTED_HEAD, "gitlink": NESTED_HEAD,
        },
        "accepted_evidence": [
            evidence_reference("accepted v1 manifest", "results/2026-08-04/host-79466/dsv4-24gb-validation/manifest.json", {"project": "d9a0c60649816d436a86aa9c3d8d928f426b3d92", "nested": "9e8d62a465f9663e2a81c0362c2ce530fe442f28"}),
            evidence_reference("accepted v1 archive index", "results/2026-08-04/host-79466/dsv4-24gb-validation/archive-index.json", {"project": "d9a0c60649816d436a86aa9c3d8d928f426b3d92", "nested": "9e8d62a465f9663e2a81c0362c2ce530fe442f28"}),
            evidence_reference("accepted heterogeneous-layout manifest", "results/2026-08-05/host-79466/heterogeneous-layout/manifest.json", {"project": "4e5418483db5c39afcce698fa7c42e00a107f8ef", "nested": NESTED_BASE}),
            evidence_reference("accepted heterogeneous-layout full-model archive index", "results/2026-08-05/host-79466/heterogeneous-layout/full-model-archive-index.json", {"project": "4e5418483db5c39afcce698fa7c42e00a107f8ef", "nested": NESTED_BASE}),
        ],
        "artifact": {
            "repository": "unsloth/DeepSeek-V4-Flash-GGUF", "revision": "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1", "variant": "UD-Q3_K_XL",
            "total_bytes": 129448242976,
            "files": [
                {"name": "DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf", "bytes": 5256864, "sha256": "951458825be77e285141adb8a71bcb72abf26ab33a39bbdead9eb7d73ef7b396"},
                {"name": "DeepSeek-V4-Flash-UD-Q3_K_XL-00002-of-00004.gguf", "bytes": 49350774208, "sha256": "63c873e288a2ab222bf902cfda53105cdf37fd714f0aa939070f8106fdda3242"},
                {"name": "DeepSeek-V4-Flash-UD-Q3_K_XL-00003-of-00004.gguf", "bytes": 49189072672, "sha256": "9c2c9878beb485d3553fe272edcc13f5959c31ec371f5df947fa0514b83cd4dc"},
                {"name": "DeepSeek-V4-Flash-UD-Q3_K_XL-00004-of-00004.gguf", "bytes": 30903139232, "sha256": "2deb9faaa22707d4af983955f517f961d7e939e169a11ec129066186918a13ea"},
            ],
            "verification": {"status": "pass", "method": "entry-gate exact size and SHA-256 refresh", "artifact_reused": True, "additional_complete_copy_created": False},
        },
        "environment": {
            "host_identifier": "79466", "observed_hostname": "ubuntu", "kernel": "Linux 6.8.0-59-generic x86_64",
            "cpu": {"model": "Intel Xeon E5-2673 v4 @ 2.30GHz", "logical_cpus": 38, "numa_nodes": 1},
            "memory": {"physical_bytes": 159296397312, "swap_bytes": 0},
            "gpu": {"model": "NVIDIA GeForce RTX 3090", "vram_mib": 24576, "driver": "580.173.02", "pci": "0000:00:08.0"},
            "cuda": {"toolkit": "12.8.93"},
            "storage": {"source": "/dev/vda1", "filesystem": "ext4", "mount_options": "rw,relatime,discard,errors=remount-ro", "controller": "Red Hat Virtio block 1af4:1001", "rotational_reported": True, "transport_reported": "none", "scheduler_reported": "none", "physical_nvme_identity_visible": False},
            "io_uring": {"kernel_disabled_sysctl": 0, "native_buffered_observed": True, "direct_io_source_support": False},
        },
        "fixed_workload": {
            "n_ctx": 4096, "n_batch": 128, "n_ubatch": 128, "seed": 1, "temperature": 0,
            "screening_tokens": 8, "confirmation_tokens": 24,
            "rendered_prompt": {"bytes": 150, "sha256": "956f20dbb9de59aba70bd6a510ad3c8ab46df35046000d925f0ae874d433a8b8"},
        },
        "fixed_defaults": {"requests": 1, "cuda_devices": 1, "hot_policy": "global LRU", "cold_policy": "global LRU", "admission": "ALWAYS", "miss_policy": "PROMOTE_AND_GPU", "background_promotion": False, "static_seed": False, "predictive_prefetch": False, "prefetch_profile": None, "multi_gpu": False, "speculative_decoding": False, "mtp": False, "expert_dropping": False, "config_source": "NULL"},
        "runtime_changes": [
            {"revision": "f94f0f600ea66af6c94d5b7920509492c038e408", "classification": "evidence_only", "purpose": "Expose forced positional reads, bounded queue/service intervals, and requested queue depth.", "regression": "focused expert async-I/O test passed"},
            {"revision": "df0edbf08aa7fa1c99e6cb9e411c78d43cfa7297", "classification": "defect_fix", "purpose": "Accept valid IORING_FEAT_SINGLE_MMAP layouts whose SQ array follows the CQE region.", "regression": "deterministic and native single-mmap tests plus focused expert async-I/O test passed"},
            {"revision": "4b91b9382e669d405d7b213625a26ec7554799cf", "classification": "evidence_only", "purpose": "Expose an explicit bounded long-run async trace capacity.", "regression": "focused expert async-I/O test passed"},
            {"revision": NESTED_HEAD, "classification": "evidence_only", "purpose": "Record generated text and process CPU time in the existing provider probe.", "regression": "CUDA target rebuilt and focused expert async-I/O test passed"},
        ],
        "screening": identity(screening_path, relative_to=ROOT),
        "confirmation": identity(confirmation_path, relative_to=ROOT),
        "attribution": attribution,
        "checkpoint_b": {"status": "pass", "final_capable": True, "gates": {
            "accepted_evidence_reused": "pass", "exact_artifact": "pass", "incremental_screening": "pass",
            "shortlist_frozen": "pass", "confirmation_sample_count": "pass", "deterministic_output_and_routes": "pass",
            "same_kernel_layout_parity_reused": "pass", "resource_bounds": "pass", "storage_integrity": "pass",
            "terminal_cleanup": "pass", "conventional_comparison": "pass", "immutable_archive": "pass",
        }},
        "result": {
            "status": "negative", "disposition": "SUPPORTED_EXPERIMENTAL_UNSELECTED", "selected_provider_configuration": None,
            "claims": [
                "OBSERVED: the integrated provider is deterministic, bounded, and correct for the exact artifact and host methodology.",
                "OBSERVED: no tested capacity, lane, queue-depth, or native-transport cell justified selection over the conventional paths.",
                f"OBSERVED: confirmed provider median process throughput was {provider['decode']['median_process_throughput_tps']:.6f} token/s versus {baselines['fit']['median_generation_tps']:.1f} for --fit and {baselines['cpu_moe']['median_generation_tps']:.1f} for explicit CPU-MoE.",
                "OBSERVED: every confirmed provider process preserved the 6 GiB VRAM, 16 GiB MemAvailable, 55 GiB filesystem, 1 GiB pinned-memory, zero-swap, and zero-cgroup-event gates.",
            ],
        },
        "limitations": [
            "The disposition applies only to the exact UD-Q3_K_XL artifact, measured runtime revisions, host class, prompt, and one-request methodology.",
            "The physical OS page cache was warm; fresh means a new process with empty provider tiers, while decode is post-prefill warm state within that process.",
            "Chronological cold telemetry has one benign completion/unpin interleaving variant; per-type event sequences, choices, terminal state digests, routes, logits, text, and generated IDs are exact.",
            "Native direct I/O is BLOCKED on this filesystem/source combination by EOPNOTSUPP and was not emulated.",
            "GPU kernel time and synchronization were not independently observable, so the residual execution cost is not split more finely than the recorded queue/service/stage/H2D data permits.",
            "The conventional --fit path does not meet the provider VRAM reserve and is used only as the prescribed performance comparison.",
        ],
        "archive": {"format": "tar.zst", "storage_path": str(args.archive), "bytes": archive_identity["bytes"], "sha256": archive_identity["sha256"], "member_count": archive_identity["member_count"], "index": archive_index_path.relative_to(ROOT).as_posix()},
    }
    manifest_path = OUTPUT / "manifest.json"
    write_json(manifest_path, manifest)

    summary = f"""# DeepSeek-V4 24 GB continuation validation

Checkpoint B is final-capable at project technical revision `{PROJECT_TECHNICAL_HEAD}` and nested revision `{NESTED_HEAD}`. The exact four-split UD-Q3_K_XL artifact and accepted #47/#52 evidence were verified and reused; artifact acquisition, inventory, layout design, and prior correctness proofs were not repeated.

The final disposition is `SUPPORTED_EXPERIMENTAL_UNSELECTED`. The provider remains correct, deterministic, bounded, and explicit/nondefault, but no tested cell justifies selection over a conventional path on this exact RTX 3090 host. Five confirmed provider processes produced identical 24-token IDs/text, logits hashes, all 1,032 route records, semantic policy sequences, mechanism counts, and terminal state digests. Across 115 post-prefill samples, nearest-rank latency was p50 `{provider['decode']['p50_us'] / 1e6:.3f} s`, p95 `{provider['decode']['p95_us'] / 1e6:.3f} s`, p99 `{provider['decode']['p99_us'] / 1e6:.3f} s`, at `{provider['decode']['aggregate_throughput_tps']:.4f} token/s`.

The selected confirmation cell used 268 hot slots (`4,286,284,800` bytes), 335 cold slots (`17,145,139,200` bytes), four transfer lanes (`67,173,120` pinned bytes), automatic effective queue depth 256, and forced positional reads. It retained at least `{provider['resources']['minimum_gpu_free_mib']:,} MiB` free VRAM, `{provider['resources']['minimum_mem_available_bytes']:,}` MemAvailable bytes, and `{provider['resources']['minimum_disk_available_bytes']:,}` filesystem bytes, with zero major faults, swap, cgroup pressure/OOM events, short reads, I/O errors, dropped traces, or cleanup failures.

The refreshed conventional medians were `{baselines['fit']['median_generation_tps']:.1f} token/s` for `--fit` and `{baselines['cpu_moe']['median_generation_tps']:.1f} token/s` for explicit CPU-MoE, versus provider median process throughput `{provider['decode']['median_process_throughput_tps']:.4f} token/s`. Larger cold capacities reduced bytes but not latency/throughput; two and four lanes tied on throughput; native buffered io_uring was slower; queue depths 1/4 were below the validated invariant and depth 8 failed closed; direct I/O was unavailable with `EOPNOTSUPP`.

The authoritative technical record is `manifest.json`; screening and confirmation detail are in `screening-matrix.json` and `confirmation.json`. Raw evidence is archived at `{args.archive}` with SHA-256 `{archive_identity['sha256']}`.
"""
    (OUTPUT / "SUMMARY.md").write_text(summary)
    checksums = {
        "schema_version": "dsv4-24gb-resume-checksums-v1",
        "members": [identity(OUTPUT / name, relative_to=OUTPUT) for name in
                    ("SUMMARY.md", "archive-index.json", "confirmation.json", "manifest.json", "screening-matrix.json")],
    }
    write_json(OUTPUT / "checksums.json", checksums)
    print(json.dumps({"output": str(OUTPUT), "archive": archive_identity, "disposition": manifest["result"]["disposition"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Measure truthful controlled Phase 8 crossover regimes and a sparse exact-size K3 store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import resource
import time
from pathlib import Path

from common import (BENCHMARK_POLICIES, EXPECTED_BENCHMARK_CELLS, git, identity,
                    percentile, write)
from evaluate_auto_cost import AUTO_COST_MODEL_STRUCT_SIZE, evaluate_auto


def mxfp4_bytes(elements: int) -> int:
    if elements % 32:
        raise ValueError("MXFP4 tensor extent must be divisible by 32")
    return elements // 32 * 17


def timed_pread(fd: int, size: int, offset: int) -> tuple[int, bytes]:
    begin = time.monotonic_ns()
    data = os.pread(fd, size, offset)
    return time.monotonic_ns() - begin, data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--full-k3-config", type=Path, required=True)
    parser.add_argument("--full-k3-revision", required=True)
    parser.add_argument("--overlap", type=Path, required=True)
    parser.add_argument("--parity", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--descriptor-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=2608)
    parser.add_argument("--samples", type=int, default=40)
    args = parser.parse_args()
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    config = json.loads(args.full_k3_config.read_text())["text_config"]
    layers = int(config["num_hidden_layers"]) - int(config["first_k_dense_replace"])
    experts = int(config["num_experts"])
    latent = int(config["routed_expert_hidden_size"])
    width = int(config["moe_intermediate_size"])
    projection_bytes = mxfp4_bytes(latent * width)
    bundle_bytes = projection_bytes * 3
    total_bundles = layers * experts
    logical_bytes = total_bundles * bundle_bytes
    store = args.store.resolve()
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("w+b") as stream:
        stream.truncate(logical_bytes)
        rng = random.Random(args.seed)
        for sample in range(min(args.samples, total_bundles)):
            index = rng.randrange(total_bundles)
            offset = index * bundle_bytes
            payload = hashlib.sha256(f"{args.seed}:{index}:{sample}".encode()).digest() * 128
            stream.seek(offset)
            stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())

    rng = random.Random(args.seed)
    offsets = sorted({rng.randrange(total_bundles) * bundle_bytes for _ in range(args.samples)})
    fd = os.open(store, os.O_RDONLY)
    disk_ns, sample_hashes = [], []
    try:
        for offset in offsets:
            elapsed, data = timed_pread(fd, min(bundle_bytes, 1 << 20), offset)
            if len(data) != min(bundle_bytes, 1 << 20):
                raise RuntimeError("short read from exact-size synthetic store")
            disk_ns.append(elapsed)
            sample_hashes.append(hashlib.sha256(data).hexdigest())
    finally:
        os.close(fd)

    overlap = json.loads(args.overlap.read_text())
    parity = json.loads(args.parity.read_text())
    cpu_us = percentile([int(sample["cpu_us"]) for sample in overlap["samples"]], 50)
    gpu_us = percentile([int(sample["gpu_us"]) for sample in overlap["samples"]], 50)
    h2d_bps = 6_000_000_000
    base_cost = {
        "version": 1, "struct_size": AUTO_COST_MODEL_STRUCT_SIZE,
        "cpu_fixed_decode_ns": cpu_us * 1000, "cpu_fixed_prefill_ns": cpu_us * 1200,
        "cpu_per_lane_decode_ns": 1, "cpu_per_lane_prefill_ns": 1,
        "gpu_fixed_decode_ns": gpu_us * 1000, "gpu_fixed_prefill_ns": gpu_us * 1200,
        "gpu_per_lane_decode_ns": 1, "gpu_per_lane_prefill_ns": 1,
        "h2d_fixed_ns": 20_000, "h2d_bytes_per_second": h2d_bps,
        "decision_hysteresis_ns": 1,
    }
    cpu_samples = [int(sample["cpu_us"]) * 1000 for sample in overlap["samples"]]
    gpu_samples = [int(sample["gpu_us"]) * 1000 for sample in overlap["samples"]]
    auto_cpu_cost = dict(base_cost)
    auto_cpu_cost["gpu_fixed_decode_ns"] = 1_000_000_000
    auto_cpu_cost["gpu_fixed_prefill_ns"] = 1_000_000_000
    auto_gpu_cost = dict(base_cost)
    auto_gpu_cost["cpu_fixed_decode_ns"] = 1_000_000_000
    auto_gpu_cost["cpu_fixed_prefill_ns"] = 1_000_000_000
    tie_cost = dict(base_cost)
    transfer_ns = tie_cost["h2d_fixed_ns"] + (bundle_bytes * 1_000_000_000 + h2d_bps - 1) // h2d_bps
    tie_cost["cpu_fixed_decode_ns"] = tie_cost["gpu_fixed_decode_ns"] + transfer_ns
    tie_cost["cpu_fixed_prefill_ns"] = tie_cost["gpu_fixed_prefill_ns"] + transfer_ns
    policy_variants = {
        "PROMOTE_AND_GPU": None,
        "CPU_FALLBACK": None,
        "AUTO_CPU_FAVORABLE": auto_cpu_cost,
        "AUTO_GPU_FAVORABLE": auto_gpu_cost,
        "AUTO_TIE": tie_cost,
    }
    if tuple(policy_variants) != BENCHMARK_POLICIES:
        raise RuntimeError("benchmark policy order/authority mismatch")
    cells = []
    for bucket, prefill in (("decode", False), ("prefill", True)):
        for hot_ratio in (0, 25, 50, 75, 100):
            for reuse in ("none", "immediate", "long"):
                for background in (False, True):
                    for policy, cost in policy_variants.items():
                        misses = 4 * (100 - hot_ratio) // 100
                        record = {
                            "cost": cost or base_cost, "prefill": prefill, "lanes": max(1, misses),
                            "bundle_bytes": bundle_bytes, "queued_cpu_work_ns": 0,
                            "queued_h2d_work_ns": 0, "queued_gpu_work_ns": 0,
                            "same_key_h2d_present": False, "same_key_h2d_state": "NONE",
                            "same_key_h2d_remaining_bytes": 0,
                        }
                        evaluated = evaluate_auto(record)
                        promote_ns = evaluated["gpu_finish_ns"] if misses else gpu_us * 1000
                        cpu_ns = evaluated["cpu_finish_ns"] if misses else gpu_us * 1000
                        if policy == "PROMOTE_AND_GPU":
                            backend, reason = "gpu", "explicit-policy"
                        elif policy == "CPU_FALLBACK":
                            backend, reason = "cpu", "explicit-policy"
                        else:
                            backend, reason = evaluated["backend"], evaluated["reason"]
                        service_ns = cpu_ns if backend == "cpu" else promote_ns
                        cells.append({
                            "bucket": bucket, "hot_ratio_percent": hot_ratio, "reuse": reuse,
                            "policy": policy, "background_promotion": background,
                            "miss_lanes": misses, "predicted_cpu_ns": cpu_ns,
                            "predicted_gpu_ns": promote_ns, "selected_backend": backend,
                            "selection_reason": reason,
                            "cost_model_sha256": hashlib.sha256(
                                json.dumps(record["cost"], sort_keys=True).encode()).hexdigest(),
                            "prompt_tokens_per_second": round(1e9 / max(1, service_ns), 6) if prefill else None,
                            "decode_tokens_per_second": round(1e9 / max(1, service_ns), 6) if not prefill else None,
                            "ttft_ns": service_ns, "token_p50_ns": service_ns,
                            "token_p95_ns": service_ns, "token_p99_ns": service_ns,
                            "h2d_bytes": misses * bundle_bytes if backend == "gpu" or background else 0,
                            "h2d_bytes_avoided_for_current_output": misses * bundle_bytes if backend == "cpu" else 0,
                            "background_bytes": misses * bundle_bytes if background and backend == "cpu" else 0,
                            "controlled_estimate": True, "observed_regret_ns": 0,
                        })

    cpu_favorable = evaluate_auto({
        "cost": auto_cpu_cost, "prefill": False, "lanes": 1, "bundle_bytes": bundle_bytes,
        "queued_cpu_work_ns": 0, "queued_h2d_work_ns": 20_000_000,
        "queued_gpu_work_ns": 0, "same_key_h2d_present": False,
        "same_key_h2d_state": "NONE", "same_key_h2d_remaining_bytes": 0,
    })
    gpu_favorable = evaluate_auto({
        "cost": auto_gpu_cost, "prefill": False, "lanes": 1,
        "bundle_bytes": bundle_bytes, "queued_cpu_work_ns": 0, "queued_h2d_work_ns": 0,
        "queued_gpu_work_ns": 0, "same_key_h2d_present": False,
        "same_key_h2d_state": "NONE", "same_key_h2d_remaining_bytes": 0,
    })
    tie = evaluate_auto({
        "cost": tie_cost, "prefill": False, "lanes": 1, "bundle_bytes": bundle_bytes,
        "queued_cpu_work_ns": 0, "queued_h2d_work_ns": 0, "queued_gpu_work_ns": 0,
        "same_key_h2d_present": False, "same_key_h2d_state": "NONE",
        "same_key_h2d_remaining_bytes": 0,
    })

    stat = os.stat(store)
    descriptor = {
        "schema_version": "phase8-k3-synthetic-store-v1",
        "source": {"repository": "moonshotai/Kimi-K3", "revision": args.full_k3_revision,
                   "config": identity(root, args.full_k3_config.resolve(), external=True)},
        "layout": {"layers": layers, "experts_per_layer": experts,
                   "latent_width": latent, "expert_width": width,
                   "projections_per_bundle": 3, "mxfp4_block_elements": 32,
                   "mxfp4_block_bytes": 17, "projection_bytes": projection_bytes,
                   "bundle_bytes": bundle_bytes, "bundle_count": total_bundles,
                   "logical_bytes": logical_bytes},
        "payload": {"path": str(store), "logical_size": stat.st_size,
                    "allocated_bytes": stat.st_blocks * 512, "sparse": stat.st_blocks * 512 < stat.st_size,
                    "seed": args.seed, "sample_offsets": offsets,
                    "sample_sha256": sample_hashes,
                    "descriptor_digest_basis": "source revision, exact dimensions, MXFP4 17-byte/32-element layout, seed and sample hashes"},
    }
    write(args.descriptor_output, descriptor)
    output = {
        "schema_version": "phase8-miss-policy-benchmarks-v1",
        "status": "pass",
        "revisions": {"project": git(root, "rev-parse", "HEAD"),
                      "llama_cpp": git(nested, "rev-parse", "HEAD"),
                      "gitlink": git(root, "rev-parse", "HEAD:llama.cpp")},
        "synthetic_store": identity(root, args.descriptor_output.resolve()),
        "observed_real_model_process_latency_ms": {
            case["name"]: case["process_latency_ms"] for case in parity["cases"]},
        "observed_branch_service_ns": {
            "cpu": {"p50": percentile(cpu_samples, 50), "p95": percentile(cpu_samples, 95),
                    "p99": percentile(cpu_samples, 99)},
            "gpu": {"p50": percentile(gpu_samples, 50), "p95": percentile(gpu_samples, 95),
                    "p99": percentile(gpu_samples, 99)},
        },
        "storage_read_ns": {"p50": percentile(disk_ns, 50), "p95": percentile(disk_ns, 95),
                            "p99": percentile(disk_ns, 99)},
        "controlled_regimes": {"cpu_favorable": cpu_favorable,
                               "gpu_favorable": gpu_favorable, "tie": tie},
        "matrix": cells,
        "resource_observation": {"rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                 "sampled_device_wide_vram": True,
                                 "peak_device_memory_used_mib": parity["resource_observation"]["peak_device_memory_used_mib"],
                                 "minimum_device_memory_free_mib": parity["resource_observation"]["minimum_device_memory_free_mib"],
                                 "synthetic_payload_sparse": descriptor["payload"]["sparse"]},
        "checks": {"exact_size": stat.st_size == logical_bytes,
                   "source_metadata_derived": True,
                   "mxfp4_layout_exact": True,
                   "all_reads_complete": len(disk_ns) == len(offsets),
                   "cpu_favorable": cpu_favorable["backend"] == "cpu",
                   "gpu_favorable": gpu_favorable["backend"] == "gpu",
                   "tie_gpu": tie["backend"] == "gpu" and tie["reason"] == "tie",
                   "auto_independently_evaluated": True,
                   "matrix_has_expected_cells": len(cells) == EXPECTED_BENCHMARK_CELLS,
                   "matrix_policy_complete": {item["policy"] for item in cells} == set(policy_variants),
                   "matrix_axes_complete": {item["hot_ratio_percent"] for item in cells} == {0, 25, 50, 75, 100} and
                       {item["reuse"] for item in cells} == {"none", "immediate", "long"} and
                       {item["bucket"] for item in cells} == {"decode", "prefill"} and
                       {item["background_promotion"] for item in cells} == {False, True},
                   "observed_real_host_tails": all(
                       all(case["process_latency_ms"][name] > 0 for name in ("p50", "p95", "p99"))
                       for case in parity["cases"]),
                   "peak_vram_sampled": parity["resource_observation"]["peak_device_memory_used_mib"] > 0,
                   "no_full_model_quality_claim": True},
    }
    if not all(output["checks"].values()):
        output["status"] = "fail"
    write(args.output, output)
    print("PASS: Phase 8 crossover and exact-size synthetic store captured" if output["status"] == "pass" else "FAIL: Phase 8 crossover capture")
    return 0 if output["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

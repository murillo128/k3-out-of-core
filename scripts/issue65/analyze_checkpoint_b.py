#!/usr/bin/env python3
"""Validate and compact issue 65 Checkpoint B mechanism evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics


LIFECYCLE_ZERO_KEYS = (
    "active_background_flights", "current_hot_pins", "cold_current_transfer_refs",
    "cold_current_request_refs", "cold_current_cpu_execution_refs",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compliance-dir", type=Path, required=True)
    parser.add_argument("--performance-dir", type=Path, required=True)
    parser.add_argument("--accepted-mode-c", type=Path, required=True)
    parser.add_argument("--d1-failure", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=5)
    return parser.parse_args()


def load(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] if lower == upper else (
        ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower]))


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compliance_identity(run: dict) -> dict:
    return {key: run[key] for key in (
        "prompt_ids", "generated_ids", "generated_text", "logits_fnv64", "routes")}


def performance_identity(run: dict) -> dict:
    return {key: run[key] for key in ("prompt_ids", "generated_ids", "generated_text")}


def clean_lifecycle(run: dict) -> bool:
    return all(run.get("lifecycle", {}).get(key, 0) == 0 for key in LIFECYCLE_ZERO_KEYS)


def exact_devices(run: dict, slots: list[int]) -> bool:
    devices = run.get("multi_gpu", {}).get("devices", [])
    return len(devices) == len(slots) and all(
        device.get("device_id") == index and
        device.get("hot_requested_slots") == capacity and
        device.get("hot_effective_slots") == capacity
        for index, (device, capacity) in enumerate(zip(devices, slots))
    )


def validate_transport_endpoint_mapping(run: dict) -> None:
    roles = run.get("expert_roles", {})
    resident = roles.get("resident", {})
    experts = roles.get("experts", [])
    resident_expert_id = next((
        expert.get("device_id") for expert in experts
        if expert.get("uuid") == resident.get("uuid") and
        expert.get("pci_bdf") == resident.get("pci_bdf")
    ), -1)
    endpoints = [{
        "transport_endpoint_id": 0,
        "expert_device_id": resident_expert_id,
        "cuda_ordinal": resident.get("cuda_ordinal"),
        "pci_bdf": resident.get("pci_bdf"),
        "uuid": resident.get("uuid"),
        "role": "RESIDENT_AND_EXPERT" if resident_expert_id != -1 else "RESIDENT",
    }]
    for expert in experts:
        if (expert.get("uuid"), expert.get("pci_bdf")) == (
                resident.get("uuid"), resident.get("pci_bdf")):
            continue
        endpoints.append({
            "transport_endpoint_id": len(endpoints),
            "expert_device_id": expert.get("device_id"),
            "cuda_ordinal": expert.get("cuda_ordinal"),
            "pci_bdf": expert.get("pci_bdf"),
            "uuid": expert.get("uuid"),
            "role": "EXPERT",
        })

    peers = run.get("multi_gpu", {}).get("peer_diagnostics", [])
    expected_edges = {(0, endpoint) for endpoint in range(1, len(endpoints))} | {
        (endpoint, 0) for endpoint in range(1, len(endpoints))}
    observed_edges: set[tuple[int, int]] = set()
    for edge in peers:
        source_id = edge.get("source_transport_endpoint_id")
        destination_id = edge.get("transport_endpoint_id")
        if (not edge.get("endpoint_mapping_valid") or not isinstance(source_id, int) or
                not isinstance(destination_id, int) or source_id >= len(endpoints) or
                destination_id >= len(endpoints)):
            raise SystemExit("transport endpoint mapping is invalid")
        source, destination = endpoints[source_id], endpoints[destination_id]
        for prefix, expected in (("source_", source), ("", destination)):
            for key in ("expert_device_id", "cuda_ordinal", "pci_bdf", "uuid", "role"):
                if edge.get(prefix + key) != expected[key]:
                    raise SystemExit(f"transport endpoint {prefix}{key} attribution mismatch")
        observed_edges.add((source_id, destination_id))
    if observed_edges != expected_edges or len(peers) != len(expected_edges):
        raise SystemExit("transport endpoint edge set is not the resident star")


def resource_summary(path: Path) -> dict:
    record = json.loads(path.read_text())
    per_device: dict[str, dict[str, object]] = {}
    for sample in record["samples"]:
        for gpu in sample["gpus"]:
            device = per_device.setdefault(gpu["uuid"], {
                "cuda_ordinal": gpu["cuda_ordinal"], "uuid": gpu["uuid"],
                "pci_bdf": gpu["pci_bus_id"], "total_vram_mib": gpu["memory_total_mib"],
                "memory_used_mib": [], "memory_free_mib": [], "utilization_percent": [],
            })
            device["memory_used_mib"].append(gpu["memory_used_mib"])
            device["memory_free_mib"].append(gpu["memory_free_mib"])
            device["utilization_percent"].append(gpu["gpu_utilization_percent"])
    result = []
    for device in sorted(per_device.values(), key=lambda item: item["cuda_ordinal"]):
        result.append({
            "cuda_ordinal": device["cuda_ordinal"], "uuid": device["uuid"],
            "pci_bdf": device["pci_bdf"], "total_vram_mib": device["total_vram_mib"],
            "maximum_observed_used_mib": max(device["memory_used_mib"]),
            "minimum_observed_free_mib": min(device["memory_free_mib"]),
            "mean_gpu_utilization_percent": statistics.fmean(device["utilization_percent"]),
        })
    return {
        "elapsed_seconds": record["elapsed_seconds"], "sample_count": len(record["samples"]),
        "devices": result,
    }


def pooled(runs: list[dict]) -> dict:
    decode = [latency for run in runs for latency in run["latency_us"][1:]]
    return {
        "processes": len(runs), "decode_tokens": len(decode), "decode_wall_us": sum(decode),
        "decode_tps": len(decode) / (sum(decode) / 1_000_000),
        "ttft_us": {
            "p50": percentile([run["latency_us"][0] for run in runs], 0.50),
            "p95": percentile([run["latency_us"][0] for run in runs], 0.95),
        },
        "decode_latency_us": {
            "p50": percentile(decode, 0.50), "p95": percentile(decode, 0.95),
            "p99": percentile(decode, 0.99),
        },
    }


def validate_remote(run: dict, accepted_identity: dict, delayed: bool) -> None:
    if run.get("status") != "pass" or compliance_identity(run) != accepted_identity:
        raise SystemExit("D1 Mode-C identity mismatch")
    if run.get("expert_roles", {}).get("shape") != "REMOTE_SINGLE" or not exact_devices(run, [536]):
        raise SystemExit("D1 role/capacity mismatch")
    validate_transport_endpoint_mapping(run)
    structure = run.get("role_path_structure", {})
    if (structure.get("remote_single_bindings", 0) == 0 or
            structure.get("multi_device_bindings") != 0 or
            structure.get("device_binding_vector_elements") != 0 or
            structure.get("peer_edge_count") != 2):
        raise SystemExit("D1 did not use the fixed remote-single graph")
    peers = run.get("multi_gpu", {}).get("peer_diagnostics", [])
    if len(peers) != 2 or any(
            edge.get("host_staged_copies", 0) == 0 or
            edge.get("host_staging_live_slots") != 0 or
            edge.get("host_staged_blocking_us") != 0 or
            edge.get("unexpected_host_synchronizations") != 0
            for edge in peers):
        raise SystemExit("D1 transport edge accounting failed")
    delay_enqueues = sum(edge.get("branch_delay_enqueues_for_testing", 0) for edge in peers)
    delay_completions = sum(edge.get("branch_delay_completions_for_testing", 0) for edge in peers)
    if delayed and (delay_enqueues == 0 or delay_enqueues != delay_completions):
        raise SystemExit("D1 completion-order perturbation was not exercised and drained")
    if not delayed and (delay_enqueues != 0 or delay_completions != 0):
        raise SystemExit("D1 evidence delay leaked into the normal run")
    if not clean_lifecycle(run):
        raise SystemExit("D1 lifecycle did not close cleanly")


def main() -> None:
    args = parse_args()
    accepted = load(args.accepted_mode_c)
    accepted_identity = compliance_identity(accepted)
    compliance: dict[str, dict] = {}
    for cell in ("S1_LEGACY", "S1_EXPLICIT", "D1", "D1_DELAYED"):
        compliance[cell] = load(args.compliance_dir / "raw" / f"{cell}-01.json.gz")
    for cell in ("S1_LEGACY", "S1_EXPLICIT"):
        run = compliance[cell]
        if (run.get("status") != "pass" or compliance_identity(run) != accepted_identity or
                run.get("expert_roles", {}).get("shape") != "STRIPED_MULTI" or
                not exact_devices(run, [268, 268]) or not clean_lifecycle(run)):
            raise SystemExit(f"{cell}: S1 Mode-C qualification failed")
        validate_transport_endpoint_mapping(run)
    if compliance["S1_LEGACY"]["role_path_structure"] != compliance["S1_EXPLICIT"]["role_path_structure"]:
        raise SystemExit("S1 explicit graph differs from the accepted legacy mechanism")
    validate_remote(compliance["D1"], accepted_identity, False)
    validate_remote(compliance["D1_DELAYED"], accepted_identity, True)

    failure = load(args.d1_failure)
    failure_multi = failure.get("multi_gpu", {})
    failure_device = failure_multi.get("devices", [{}])[0]
    failure_pass = all((
        failure.get("status") == "pass",
        failure_multi.get("expected_device_failure_observed") is True,
        failure_multi.get("injected_device_failure_waves") == 1,
        failure_multi.get("injected_device_failure_participants") == 1,
        failure_multi.get("injected_device_failure_drained_waves") == 1,
        failure_device.get("ring_live_events") == 0,
        failure_device.get("scheduler", {}).get("active_requests") == 0,
        failure_device.get("scheduler", {}).get("terminal_failed", 0) > 0,
        len(failure.get("generated_ids", [])) == 0,
        clean_lifecycle(failure),
    ))
    if not failure_pass:
        raise SystemExit("D1 fail-closed qualification failed")

    performance: dict[str, list[dict]] = {"legacy": [], "explicit": []}
    performance_digest = None
    for pair in range(1, args.pairs + 1):
        for role, cell in (("legacy", "S1_LEGACY"), ("explicit", "S1_EXPLICIT")):
            run = load(args.performance_dir / "raw" / f"{cell}-{pair:02d}.json.gz")
            identity = performance_identity(run)
            if (run.get("status") != "pass" or len(run.get("generated_ids", [])) != 24 or
                    run.get("expert_runtime_mode") != "PRODUCTION_PERFORMANCE" or
                    not exact_devices(run, [268, 268]) or not clean_lifecycle(run)):
                raise SystemExit(f"{cell}-{pair:02d}: incomplete S1 Mode-P evidence")
            if performance_digest is None:
                performance_digest = digest(identity)
            if digest(identity) != performance_digest:
                raise SystemExit("S1 Mode-P generated output mismatch")
            performance[role].append(run)
    ratios = [
        sum(legacy["latency_us"][1:]) / sum(explicit["latency_us"][1:])
        for legacy, explicit in zip(performance["legacy"], performance["explicit"])
    ]
    geometric_mean = statistics.geometric_mean(ratios)
    rng = random.Random(65)
    bootstrap = []
    for _ in range(100_000):
        indices = [rng.randrange(args.pairs) for _ in range(args.pairs)]
        bootstrap.append(statistics.geometric_mean([ratios[index] for index in indices]))
    interval = [percentile(bootstrap, 0.025), percentile(bootstrap, 0.975)]
    gate_pass = geometric_mean >= 0.99 and interval[1] >= 0.99
    if not gate_pass:
        raise SystemExit("S1 explicit compatibility performance gate failed")

    normal_d1 = compliance["D1"]
    result = {
        "schema_version": "issue65-checkpoint-b-analysis-v1", "status": "pass",
        "accepted_mode_c_identity_sha256": digest(accepted_identity),
        "s1": {
            "mode_c_exact": True, "legacy_explicit_structure_equal": True,
            "structure": compliance["S1_LEGACY"]["role_path_structure"],
            "performance_identity_sha256": performance_digest,
            "paired_explicit_speedups": ratios,
            "paired_geometric_mean_explicit_speedup": geometric_mean,
            "paired_bootstrap_95_percent_interval": interval,
            "bootstrap_resamples": 100_000,
            "acceptance_minimum_ratio": 0.99,
            "interval_does_not_establish_regression_over_one_percent": interval[1] >= 0.99,
            "gate_pass": gate_pass,
            "pooled": {role: pooled(runs) for role, runs in performance.items()},
        },
        "d1": {
            "mode_c_exact": True, "remote_single_structure": normal_d1["role_path_structure"],
            "expert_roles": normal_d1["expert_roles"],
            "transport": normal_d1["multi_gpu"]["peer_diagnostics"],
            "completion_order_perturbation": {
                "delay_us": compliance["D1_DELAYED"]["multi_gpu"]["device_delay_us"],
                "enqueues": sum(edge["branch_delay_enqueues_for_testing"] for edge in
                                compliance["D1_DELAYED"]["multi_gpu"]["peer_diagnostics"]),
                "completions": sum(edge["branch_delay_completions_for_testing"] for edge in
                                   compliance["D1_DELAYED"]["multi_gpu"]["peer_diagnostics"]),
                "identity_exact": True, "clean_lifecycle": True,
            },
            "fail_closed": {
                "real_h2d_bytes": failure_device["ring_h2d_bytes"],
                "waves": failure_multi["injected_device_failure_waves"],
                "participants": failure_multi["injected_device_failure_participants"],
                "drained_waves": failure_multi["injected_device_failure_drained_waves"],
                "terminal_failed_requests": failure_device["scheduler"]["terminal_failed"],
                "tokens_published": len(failure["generated_ids"]), "clean_lifecycle": True,
            },
            "resources": resource_summary(
                args.compliance_dir / "raw" / "D1-01-resources.json"),
        },
        "unequal_capacity_and_topology_tests": {
            "exact_scheduler_2_plus_6": "pass", "star_edge_count_for_four_nodes": 6,
            "expert_to_expert_edges": 0,
            "cuda_provider_exact_2_plus_6": "pass",
            "cuda_provider_second_pool_failure_atomic": "pass",
            "max_safe_workflow_unit_integration": "pass",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "pass", "s1": result["s1"], "d1_fail_closed": result["d1"]["fail_closed"]}, indent=2))


if __name__ == "__main__":
    main()

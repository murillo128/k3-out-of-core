#!/usr/bin/env python3
"""Validate and compact issue 65 final-capable Checkpoint C evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.phase13.analyze_iteration_trace import query, summarize_case, union_ns


CELLS = ("S0_LEGACY", "S0_EXPLICIT", "S1_LEGACY", "S1_EXPLICIT", "D1", "D2", "A1")
COMPLIANCE_CELLS = CELLS + ("D1_DELAYED",)
LIFECYCLE_ZERO_KEYS = (
    "active_background_flights", "current_hot_pins", "cold_current_transfer_refs",
    "cold_current_request_refs", "cold_current_cpu_execution_refs",
)
ACCEPTED_MODE_C_IDENTITY = "60658621b12340bc02d1fbb614142e4a17c5dd52eb529bfd4b0b2eb1a1255889"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compliance-dir", type=Path, required=True)
    parser.add_argument("--performance-dir", type=Path, required=True)
    parser.add_argument("--d2-manifest", type=Path, required=True)
    parser.add_argument("--a1-device0-manifest", type=Path, required=True)
    parser.add_argument("--a1-device1-manifest", type=Path, required=True)
    parser.add_argument("--a1-combined-rejection-log", type=Path, required=True)
    parser.add_argument("--a1-combined-rejection-exit-code", type=int, required=True)
    parser.add_argument("--a1-combined-rejection-output", type=Path, required=True)
    parser.add_argument("--failure-output", type=Path, required=True)
    parser.add_argument("--failure-log", type=Path, required=True)
    parser.add_argument("--p2p-rejection-log", type=Path, required=True)
    parser.add_argument("--p2p-rejection-exit-code", type=int, required=True)
    parser.add_argument("--p2p-rejection-output", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def load(path: Path) -> dict:
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_identity(path: Path) -> dict[str, object]:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return {"path": str(path), "size": path.stat().st_size, "sha256": checksum.hexdigest()}


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] if lower == upper else (
        ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower]))


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
        if (expert.get("uuid"), expert.get("pci_bdf")) ==
           (resident.get("uuid"), resident.get("pci_bdf"))
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
    expected_edges = {(0, endpoint) for endpoint in range(1, len(endpoints))} | {
        (endpoint, 0) for endpoint in range(1, len(endpoints))}
    peers = run.get("multi_gpu", {}).get("peer_diagnostics", [])
    observed_edges: set[tuple[int, int]] = set()
    for edge in peers:
        source_id = edge.get("source_transport_endpoint_id")
        destination_id = edge.get("transport_endpoint_id")
        if (not edge.get("endpoint_mapping_valid") or not isinstance(source_id, int) or
                not isinstance(destination_id, int) or source_id >= len(endpoints) or
                destination_id >= len(endpoints)):
            raise SystemExit("transport endpoint mapping is invalid")
        for prefix, expected in (("source_", endpoints[source_id]), ("", endpoints[destination_id])):
            for key in ("expert_device_id", "cuda_ordinal", "pci_bdf", "uuid", "role"):
                if edge.get(prefix + key) != expected[key]:
                    raise SystemExit(f"transport endpoint {prefix}{key} attribution mismatch")
        observed_edges.add((source_id, destination_id))
    if observed_edges != expected_edges or len(peers) != len(expected_edges):
        raise SystemExit("transport endpoint edge set is not the resident star")


def pooled(runs: list[dict]) -> dict:
    decode = [latency for run in runs for latency in run["latency_us"][1:]]
    ttft = [run["latency_us"][0] for run in runs]
    return {
        "processes": len(runs),
        "decode_tokens": len(decode),
        "decode_wall_us": sum(decode),
        "decode_tps": len(decode) / (sum(decode) / 1_000_000),
        "ttft_us": {"p50": percentile(ttft, 0.50), "p95": percentile(ttft, 0.95)},
        "decode_latency_us": {
            "p50": percentile(decode, 0.50),
            "p95": percentile(decode, 0.95),
            "p99": percentile(decode, 0.99),
        },
    }


def paired_ratio(
        numerator: list[dict], denominator: list[dict], *, seed: int,
) -> dict[str, object]:
    ratios = [
        sum(den["latency_us"][1:]) / sum(num["latency_us"][1:])
        for num, den in zip(numerator, denominator)
    ]
    geometric_mean = statistics.geometric_mean(ratios)
    rng = random.Random(seed)
    bootstrap: list[float] = []
    for _ in range(100_000):
        indices = [rng.randrange(len(ratios)) for _ in ratios]
        bootstrap.append(statistics.geometric_mean([ratios[index] for index in indices]))
    return {
        "paired_process_ratios": ratios,
        "geometric_mean_ratio": geometric_mean,
        "paired_bootstrap_95_percent_interval": [
            percentile(bootstrap, 0.025), percentile(bootstrap, 0.975)],
        "bootstrap_resamples": 100_000,
    }


def read_log(path: Path) -> str:
    with gzip.open(path, "rt", errors="replace") as stream:
        return stream.read()


def parse_buffer_bytes(log: str, label: str) -> dict[int, int]:
    pattern = re.compile(rf"CUDA(\d+) {re.escape(label)} buffer size (?:is|=)\s*([0-9.]+) MiB")
    result: dict[int, int] = {}
    for ordinal, value in pattern.findall(log):
        result[int(ordinal)] = max(result.get(int(ordinal), 0), round(float(value) * 1024 * 1024))
    return result


def aggregate_resources(raw_dir: Path, cell: str, pairs: int) -> dict:
    records = [json.loads((raw_dir / f"{cell}-{pair:02d}-resources.json").read_text())
               for pair in range(1, pairs + 1)]
    devices: dict[str, dict[str, object]] = {}
    for record in records:
        for sample in record["samples"]:
            for gpu in sample["gpus"]:
                device = devices.setdefault(gpu["uuid"], {
                    "cuda_ordinal": gpu["cuda_ordinal"], "uuid": gpu["uuid"],
                    "pci_bdf": gpu["pci_bus_id"], "total_vram_mib": gpu["memory_total_mib"],
                    "used": [], "free": [], "utilization": [],
                })
                device["used"].append(gpu["memory_used_mib"])
                device["free"].append(gpu["memory_free_mib"])
                device["utilization"].append(gpu["gpu_utilization_percent"])
    return {
        "processes": pairs,
        "elapsed_seconds": [record["elapsed_seconds"] for record in records],
        "devices": [{
            "cuda_ordinal": item["cuda_ordinal"], "uuid": item["uuid"],
            "pci_bdf": item["pci_bdf"], "total_vram_mib": item["total_vram_mib"],
            "maximum_observed_used_mib": max(item["used"]),
            "minimum_observed_free_mib": min(item["free"]),
            "mean_gpu_utilization_percent": statistics.fmean(item["utilization"]),
        } for item in sorted(devices.values(), key=lambda item: item["cuda_ordinal"])],
    }


def route_owner_load(run: dict) -> dict:
    selected = [expert for route in run["routes"] for expert in route["selected_experts"]]
    counts = [sum(expert % 2 == owner for expert in selected) for owner in range(2)]
    return {
        "ownership_rule": "original_expert_id modulo 2",
        "logical_experts_per_owner": [128, 128],
        "selected_lanes_per_owner": counts,
        "selected_lane_fraction_per_owner": [count / len(selected) for count in counts],
        "directory_owner_only_violations": run["multi_gpu"]["directory_owner_only_violations"],
    }


def cell_telemetry(run: dict) -> dict:
    devices = run.get("multi_gpu", {}).get("devices", [])
    hits = sum(device.get("hot_hits", 0) for device in devices)
    misses = sum(device.get("hot_misses", 0) for device in devices)
    cold = run.get("cold", {}).get("diagnostics", {})
    storage = run.get("storage", {})
    async_io = run.get("async_io", {}).get("diagnostics", {})
    peers = run.get("multi_gpu", {}).get("peer_diagnostics", [])
    return {
        "hot": {
            "hits": hits, "misses": misses,
            "hit_fraction": hits / (hits + misses),
            "admissions": sum(device.get("hot_admissions", 0) for device in devices),
            "evictions": sum(device.get("hot_evictions", 0) for device in devices),
        },
        "cold_policy": {
            "demands": cold.get("demands"), "hits": cold.get("hits"),
            "hit_fraction": cold.get("hits", 0) / cold.get("demands", 1),
            "victim_selections": cold.get("victim_selections"),
        },
        "storage": {
            "read_bytes": storage.get("read_bytes"), "read_operations": storage.get("read_operations"),
            "read_requests": storage.get("read_requests"), "io_errors": storage.get("io_errors"),
            "queue_wait_us": async_io.get("read_queue_wait_us"),
            "queue_wait_max_us": async_io.get("read_queue_wait_max_us"),
        },
        "per_device": [{
            key: device.get(key) for key in (
                "device_id", "cuda_ordinal", "uuid", "pci_bdf", "hot_requested_slots",
                "hot_effective_slots", "hot_pool_bytes", "hot_hits", "hot_misses",
                "hot_admissions", "hot_evictions", "h2d_bytes", "ring_actual_bytes",
                "ring_h2d_time_us", "ring_stage_bytes", "ring_waves", "ring_live_events")
        } for device in devices],
        "peer_transport": {
            "transport": run.get("multi_gpu", {}).get("peer_transport"),
            "pinned_host_staging_bytes_total": run.get("multi_gpu", {}).get("peer_staging_bytes"),
            "host_staged_bytes": sum(edge.get("host_staged_bytes", 0) for edge in peers),
            "host_staged_copies": sum(edge.get("host_staged_copies", 0) for edge in peers),
            "cross_device_event_waits": sum(edge.get("cross_device_event_waits", 0) for edge in peers),
            "host_blocking_us": sum(edge.get("host_staged_blocking_us", 0) for edge in peers),
            "staging_reuse_waits": sum(edge.get("host_staging_reuse_waits", 0) for edge in peers),
            "unexpected_host_synchronizations": sum(
                edge.get("unexpected_host_synchronizations", 0) for edge in peers),
        },
        "physical_feasibility": {
            key: run.get("multi_gpu", {}).get(key) for key in (
                "physical_feasibility_scan_calls", "physical_feasibility_scan_decode_calls",
                "physical_feasibility_scan_time_ns", "physical_feasibility_scan_decode_time_ns",
                "physical_feasibility_skips")
        },
    }


def memory_ledger(
        run: dict, resources: dict, log: str, max_safe: object, manifest: object,
        reserve_bytes: int,
) -> list[dict]:
    model_bytes = parse_buffer_bytes(log, "model")
    graph_bytes = parse_buffer_bytes(log, "compute")
    roles = run["expert_roles"]
    expert_by_ordinal = {}
    for item, role in zip(run["multi_gpu"]["devices"], roles["experts"]):
        ordinal = item["cuda_ordinal"] if item["cuda_ordinal"] >= 0 else role["cuda_ordinal"]
        expert_by_ordinal[ordinal] = item
    resource_by_ordinal = {item["cuda_ordinal"]: item for item in resources["devices"]}
    result = []
    for ordinal in (0, 1):
        resource = resource_by_ordinal[ordinal]
        expert = expert_by_ordinal.get(ordinal, {})
        role = next((item for item in roles["experts"] if item["cuda_ordinal"] == ordinal), None)
        result.append({
            "cuda_ordinal": ordinal, "uuid": resource["uuid"], "pci_bdf": resource["pci_bdf"],
            "total_vram_bytes": round(resource["total_vram_mib"] * 1024 * 1024),
            "ordinary_resident_model_bytes": model_bytes.get(ordinal, 0),
            "requested_expert_hot_slots": 0 if role is None else role["hot_slots"],
            "effective_expert_hot_slots": expert.get("hot_effective_slots", 0),
            "expert_hot_cache_bytes": expert.get("hot_pool_bytes", 0),
            "expert_h2d_ring_bytes": expert.get("ring_actual_bytes", 0),
            "peer_device_bytes": 0,
            "pinned_host_staging_bytes_total": run["multi_gpu"].get("peer_staging_bytes", 0),
            "graph_working_reserved_bytes": graph_bytes.get(ordinal, 0),
            "graph_working_peak_bytes": graph_bytes.get(ordinal, 0),
            "configured_safety_reserve_bytes": reserve_bytes,
            "minimum_observed_free_bytes": round(resource["minimum_observed_free_mib"] * 1024 * 1024),
            "maximum_observed_used_bytes": round(resource["maximum_observed_used_mib"] * 1024 * 1024),
            "max_safe_slots": max_safe,
            "max_safe_manifest": manifest,
        })
    return result


def compact_trace(case: dict) -> dict:
    result = dict(case)
    result.pop("cycles", None)
    return result


def dedicated_sequence(trace_processor: Path, trace: Path) -> dict:
    layers = query(trace_processor, trace, """
        SELECT ts, dur, CAST(EXTRACT_ARG(arg_set_id, 'debug.layer') AS INT) AS layer
        FROM slice WHERE category = 'k3.graph' AND name = 'expert_layer_execution' AND dur > 0
        ORDER BY ts
    """)
    lifecycle = query(trace_processor, trace, """
        SELECT name, ts FROM slice WHERE category = 'k3.lifecycle'
          AND name IN ('decode_window_start', 'decode_window_end') ORDER BY ts
    """)
    bounds = {row["name"]: int(row["ts"]) for row in lifecycle}
    cuda_rows = query(trace_processor, trace, """
        SELECT ts, dur, name,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.context_id') AS INT) AS context_id,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.copy_kind') AS INT) AS copy_kind,
          CAST(EXTRACT_ARG(arg_set_id, 'debug.bytes') AS INT) AS bytes
        FROM slice WHERE category = 'k3.cuda' AND dur > 0 ORDER BY ts
    """)
    cuda = [{
        "ts": int(row["ts"]), "dur": int(row["dur"]), "name": row["name"],
        "context_id": None if row["context_id"] == "[NULL]" else int(row["context_id"]),
        "copy_kind": None if row["copy_kind"] == "[NULL]" else int(row["copy_kind"]),
        "bytes": 0 if row["bytes"] == "[NULL]" else int(row["bytes"]),
    } for row in cuda_rows]
    records: list[dict[str, int]] = []
    for current, following in zip(layers, layers[1:]):
        graph_begin = int(current["ts"]) + int(current["dur"])
        graph_end = int(following["ts"])
        if graph_begin < bounds["decode_window_start"] or graph_end > bounds["decode_window_end"]:
            continue
        events = [event for event in cuda if graph_begin <= event["ts"] < graph_end]
        copies = [event for event in events if event["name"] == "memcpy" and 16_384 <= event["bytes"] < 1_048_576]
        def first_copy(context: int, kind: int, after: int) -> dict | None:
            return next((event for event in copies if event["context_id"] == context and
                         event["copy_kind"] == kind and event["ts"] >= after), None)
        outbound_source = first_copy(1, 2, graph_begin)
        if outbound_source is None:
            continue
        outbound_destination = first_copy(2, 1, outbound_source["ts"] + outbound_source["dur"])
        if outbound_destination is None:
            continue
        remote_kernels = [event for event in events if event["name"] == "kernel" and
                          event["context_id"] == 2 and event["ts"] >=
                          outbound_destination["ts"] + outbound_destination["dur"]]
        if not remote_kernels:
            continue
        return_source = first_copy(2, 2, remote_kernels[0]["ts"])
        if return_source is None:
            continue
        remote_kernels = [event for event in remote_kernels if event["ts"] < return_source["ts"]]
        return_destination = first_copy(1, 1, return_source["ts"] + return_source["dur"])
        if not remote_kernels or return_destination is None:
            continue
        return_complete = return_destination["ts"] + return_destination["dur"]
        resident_kernels = [event for event in events if event["name"] == "kernel" and
                            event["context_id"] == 1 and event["ts"] >= return_complete]
        if not resident_kernels:
            continue
        remote_last = max(event["ts"] + event["dur"] for event in remote_kernels)
        records.append({
            "graph_wall_ns": graph_end - graph_begin,
            "resident_producer_completion_to_outbound_enqueue_ns": outbound_source["ts"] - graph_begin,
            "outbound_d2h_service_ns": outbound_source["dur"],
            "outbound_h2d_service_ns": outbound_destination["dur"],
            "outbound_completion_to_remote_first_kernel_ns":
                remote_kernels[0]["ts"] - (outbound_destination["ts"] + outbound_destination["dur"]),
            "remote_useful_kernel_union_ns": union_ns([
                (event["ts"], event["ts"] + event["dur"]) for event in remote_kernels]),
            "remote_first_to_final_kernel_ns": remote_last - remote_kernels[0]["ts"],
            "remote_final_kernel_to_return_enqueue_ns": return_source["ts"] - remote_last,
            "return_d2h_service_ns": return_source["dur"],
            "return_h2d_service_ns": return_destination["dur"],
            "return_completion_to_resident_merge_ns": resident_kernels[0]["ts"] - return_complete,
            "resident_merge_kernel_union_ns": union_ns([
                (event["ts"], event["ts"] + event["dur"]) for event in resident_kernels]),
        })
    if not records:
        raise SystemExit("no complete D1 transport sequences in trace")
    return {
        "complete_sequences": len(records),
        "mean_ns": {key: statistics.fmean(record[key] for record in records)
                    for key in records[0]},
        "p95_ns": {key: percentile([record[key] for record in records], 0.95)
                   for key in records[0]},
        "service_time_is_non_additive_wall_attribution": True,
    }


def main() -> None:
    args = parse_args()
    compliance = {
        cell: load(args.compliance_dir / "raw" / f"{cell}-01.json.gz")
        for cell in COMPLIANCE_CELLS
    }
    accepted_identity = compliance_identity(compliance["S0_LEGACY"])
    if digest(accepted_identity) != ACCEPTED_MODE_C_IDENTITY:
        raise SystemExit("final Mode-C identity differs from accepted Phase-13 identity")
    for cell, run in compliance.items():
        if run.get("status") != "pass" or compliance_identity(run) != accepted_identity or not clean_lifecycle(run):
            raise SystemExit(f"{cell}: Mode-C correctness/lifecycle failure")

    expected = {
        "S0_LEGACY": ("LOCAL_SINGLE", [268]), "S0_EXPLICIT": ("LOCAL_SINGLE", [268]),
        "S1_LEGACY": ("STRIPED_MULTI", [268, 268]),
        "S1_EXPLICIT": ("STRIPED_MULTI", [268, 268]),
        "D1": ("REMOTE_SINGLE", [536]), "D1_DELAYED": ("REMOTE_SINGLE", [536]),
        "D2": ("REMOTE_SINGLE", [1573]), "A1": ("STRIPED_MULTI", [268, 1305]),
    }
    for cell, (shape, slots) in expected.items():
        run = compliance[cell]
        if run["expert_roles"]["shape"] != shape or not exact_devices(run, slots):
            raise SystemExit(f"{cell}: exact role/capacity mismatch")
        if shape != "LOCAL_SINGLE":
            validate_transport_endpoint_mapping(run)
    for legacy, explicit in (("S0_LEGACY", "S0_EXPLICIT"), ("S1_LEGACY", "S1_EXPLICIT")):
        if compliance[legacy]["role_path_structure"] != compliance[explicit]["role_path_structure"]:
            raise SystemExit(f"{explicit}: role structure differs from legacy")
    local_structure = compliance["S0_EXPLICIT"]["role_path_structure"]
    if any(local_structure.get(key) != 0 for key in (
            "peer_edge_count", "device_binding_vector_elements", "multi_device_bindings",
            "remote_single_bindings", "remap_dynamic_allocations")):
        raise SystemExit("S0 explicit added role-owned steady-state structure")
    for cell in ("D1", "D1_DELAYED", "D2"):
        structure = compliance[cell]["role_path_structure"]
        if (structure.get("remote_single_bindings", 0) == 0 or
                structure.get("multi_device_bindings") != 0 or
                structure.get("device_binding_vector_elements") != 0 or
                structure.get("peer_edge_count") != 2):
            raise SystemExit(f"{cell}: fixed remote-single structure missing")
    delayed_peers = compliance["D1_DELAYED"]["multi_gpu"]["peer_diagnostics"]
    delayed_enqueues = sum(edge["branch_delay_enqueues_for_testing"] for edge in delayed_peers)
    delayed_completions = sum(edge["branch_delay_completions_for_testing"] for edge in delayed_peers)
    if delayed_enqueues != 1032 or delayed_completions != delayed_enqueues:
        raise SystemExit("D1 delayed-completion perturbation did not drain")
    if compliance["A1"]["multi_gpu"]["directory_owner_only_violations"] != 0:
        raise SystemExit("A1 ownership violation")

    d2_manifest = load(args.d2_manifest)
    a1_device0 = load(args.a1_device0_manifest)
    a1_device1 = load(args.a1_device1_manifest)
    for manifest, selected in ((d2_manifest, 1573), (a1_device0, 1305), (a1_device1, 1305)):
        if (manifest.get("status") != "pass" or manifest.get("selected_max_safe_slots") != selected or
                manifest.get("schema_version") != "issue65-max-safe-capacity-v2"):
            raise SystemExit("MAX_SAFE manifest mismatch")
    combined_log = args.a1_combined_rejection_log.read_text(errors="replace")
    if (args.a1_combined_rejection_exit_code != 6 or args.a1_combined_rejection_output.exists() or
            "shared cold cache (provider error 8)" not in combined_log):
        raise SystemExit("A1 combined independent-maxima rejection is absent")

    failure = load(args.failure_output)
    failure_devices = failure.get("multi_gpu", {}).get("devices", [])
    failure_scheduler = failure_devices[0].get("scheduler", {}) if len(failure_devices) == 1 else {}
    if (failure.get("status") != "pass" or failure.get("generated_ids") or
            not clean_lifecycle(failure) or
            not failure.get("multi_gpu", {}).get("expected_device_failure_observed") or
            failure.get("multi_gpu", {}).get("injected_device_failure_waves") != 1 or
            failure.get("multi_gpu", {}).get("injected_device_failure_participants") != 1 or
            failure.get("multi_gpu", {}).get("injected_device_failure_drained_waves") != 1 or
            failure_scheduler.get("terminal_failed") != 80 or
            failure_scheduler.get("terminal_releases") != 80 or
            failure.get("transfer", {}).get("h2d_bytes") != 8_060_928 or
            failure.get("transfer", {}).get("live_h2d_events") != 0 or
            failure.get("transfer", {}).get("live_compute_events") != 0 or
            any(edge.get("host_staging_live_slots") != 0 for edge in
                failure.get("multi_gpu", {}).get("peer_diagnostics", []))):
        raise SystemExit("final in-flight device-failure qualification failed")
    p2p_log = args.p2p_rejection_log.read_text(errors="replace")
    if (args.p2p_rejection_exit_code != 6 or args.p2p_rejection_output.exists() or
            "requested routed-expert P2P transport is not capable in both directions" not in p2p_log):
        raise SystemExit("final P2P fail-closed qualification failed")

    performance: dict[str, list[dict]] = {cell: [] for cell in CELLS}
    performance_digest = None
    for pair in range(1, args.pairs + 1):
        for cell in CELLS:
            run = load(args.performance_dir / "raw" / f"{cell}-{pair:02d}.json.gz")
            identity = digest(performance_identity(run))
            shape, slots = expected[cell]
            if (run.get("status") != "pass" or len(run.get("generated_ids", [])) != 24 or
                    run.get("expert_runtime_mode") != "PRODUCTION_PERFORMANCE" or
                    run["expert_roles"]["shape"] != shape or not exact_devices(run, slots) or
                    not clean_lifecycle(run)):
                raise SystemExit(f"{cell}-{pair:02d}: incomplete Mode-P evidence")
            performance_digest = identity if performance_digest is None else performance_digest
            if identity != performance_digest:
                raise SystemExit("Mode-P generated output differs across cells/processes")
            performance[cell].append(run)

    comparisons = {
        "s0_explicit_over_legacy": paired_ratio(
            performance["S0_EXPLICIT"], performance["S0_LEGACY"], seed=650),
        "s1_explicit_over_legacy": paired_ratio(
            performance["S1_EXPLICIT"], performance["S1_LEGACY"], seed=651),
        "d1_over_s1_explicit": paired_ratio(performance["D1"], performance["S1_EXPLICIT"], seed=652),
        "d2_over_d1": paired_ratio(performance["D2"], performance["D1"], seed=653),
        "d2_over_s0_explicit": paired_ratio(performance["D2"], performance["S0_EXPLICIT"], seed=654),
        "a1_over_s1_explicit": paired_ratio(performance["A1"], performance["S1_EXPLICIT"], seed=655),
        "a1_over_d2": paired_ratio(performance["A1"], performance["D2"], seed=656),
    }
    for name in ("s0_explicit_over_legacy", "s1_explicit_over_legacy"):
        comparison = comparisons[name]
        comparison["acceptance_minimum_ratio"] = 0.99
        comparison["gate_pass"] = (
            comparison["geometric_mean_ratio"] >= 0.99 and
            comparison["paired_bootstrap_95_percent_interval"][1] >= 0.99)
        if not comparison["gate_pass"]:
            raise SystemExit(f"{name}: compatibility gate failed")

    pooled_results = {cell: pooled(runs) for cell, runs in performance.items()}
    ranked = sorted(("S1_EXPLICIT", "D1", "D2", "A1"),
                    key=lambda cell: pooled_results[cell]["decode_tps"], reverse=True)
    resources = {
        cell: aggregate_resources(args.performance_dir / "raw", cell, args.pairs) for cell in CELLS
    }
    manifest_identities = {
        "d2": file_identity(args.d2_manifest),
        "a1_device0": file_identity(args.a1_device0_manifest),
        "a1_device1": file_identity(args.a1_device1_manifest),
    }
    ledgers = {}
    for cell in CELLS:
        max_safe: object = "NOT_MEASURED"
        manifest: object = "NOT_MEASURED"
        reserve = 0
        if cell == "D2":
            max_safe, manifest, reserve = 1573, manifest_identities["d2"], 1_073_741_824
        elif cell == "A1":
            max_safe = [268, 1305]
            manifest = {"device0": manifest_identities["a1_device0"],
                        "device1": manifest_identities["a1_device1"]}
            reserve = 1_073_741_824
        ledgers[cell] = memory_ledger(
            performance[cell][0], resources[cell],
            read_log(args.performance_dir / "raw" / f"{cell}-01.log.gz"),
            max_safe, manifest, reserve)

    trace_pair = load(args.trace_dir / "trace-pair.json")
    trace_verification = {
        case: load(args.trace_dir / case / "verification.json") for case in ("A", "B")
    }
    if (trace_pair.get("status") != "valid" or not trace_pair.get("exact_identity") or
            any(item.get("status") != "valid" or item.get("errors") for item in trace_verification.values())):
        raise SystemExit("S1/D1 trace pair is invalid")
    trace_cases = {
        "S1_EXPLICIT": compact_trace(summarize_case(
            args.trace_processor, args.trace_dir / "A" / "trace.pftrace")),
        "D1": compact_trace(summarize_case(
            args.trace_processor, args.trace_dir / "B" / "trace.pftrace")),
    }
    sequence = dedicated_sequence(args.trace_processor, args.trace_dir / "B" / "trace.pftrace")
    d1_buckets = trace_cases["D1"]["critical_path"]["buckets_fraction"]
    dominant_bucket = max(d1_buckets, key=d1_buckets.get)

    result = {
        "schema_version": "issue65-checkpoint-c-analysis-v1",
        "status": "pass",
        "accepted_mode_c_identity_sha256": digest(accepted_identity),
        "performance_identity_sha256": performance_digest,
        "mode_c": {
            "cells": list(COMPLIANCE_CELLS), "generated_tokens_per_cell": 24,
            "logits_digests_per_cell": 24, "route_records_per_cell": 1032,
            "exact_identity_all_cells": True, "clean_lifecycle_all_cells": True,
            "s0_structure_equal": True, "s1_structure_equal": True,
            "d1_delayed_completion_enqueues": delayed_enqueues,
            "d1_delayed_completion_completions": delayed_completions,
            "a1_owner_load": route_owner_load(compliance["A1"]),
        },
        "capacity": {
            "slot_stride_bytes": 11_835_264, "cold_cache_bytes": 17_179_869_184,
            "cold_effective_slots": 1579, "reserved_demand_slots": 6,
            "d2_max_safe_slots": 1573,
            "a1_independent_max_safe_slots": [1305, 1305],
            "a1_independent_combination": [1305, 1305],
            "a1_independent_combination_rejected": True,
            "a1_combined_rejection": file_identity(args.a1_combined_rejection_log),
            "a1_combined_rejection_exit_code": args.a1_combined_rejection_exit_code,
            "a1_combined_rejection_output_absent": True,
            "a1_final_slots": [268, 1305],
            "a1_reduction_rule": (
                "hold the resident expert cache at the accepted 268-slot baseline and maximize the "
                "non-resident expert GPU after the combined independent maxima fail the shared cold budget"),
            "manifests": manifest_identities,
        },
        "performance": {
            "fresh_processes_per_cell": args.pairs,
            "interleaving": list(CELLS),
            "pooled": pooled_results,
            "comparisons": comparisons,
            "ranked_explicit_topologies_fastest_first": list(ranked),
        },
        "telemetry": {cell: cell_telemetry(performance[cell][0]) for cell in CELLS},
        "resources": resources,
        "memory_ledgers": ledgers,
        "failure_qualification": {
            "in_flight_device_failure": {
                "output": file_identity(args.failure_output),
                "log": file_identity(args.failure_log),
                "real_h2d_bytes": failure["transfer"]["h2d_bytes"],
                "failure_waves": failure["multi_gpu"]["injected_device_failure_waves"],
                "participants": failure["multi_gpu"]["injected_device_failure_participants"],
                "drained_waves": failure["multi_gpu"]["injected_device_failure_drained_waves"],
                "terminal_failed": failure_scheduler["terminal_failed"],
                "terminal_releases": failure_scheduler["terminal_releases"],
                "published_tokens": 0,
                "clean_lifecycle": True,
            },
            "p2p_rejection": {
                "log": file_identity(args.p2p_rejection_log),
                "exit_code": args.p2p_rejection_exit_code,
                "output_absent": True,
                "reason": "required directed P2P capabilities unavailable",
            },
        },
        "trace": {
            "pair": file_identity(args.trace_dir / "trace-pair.json"),
            "selection": trace_pair["selection"], "exact_generated_identity": True,
            "mode_p_logits_exact": trace_pair["logits_fnv64_exact"],
            "verification": trace_verification,
            "cases": trace_cases,
            "d1_transport_sequence": sequence,
            "d1_dominant_exclusive_bucket": dominant_bucket,
            "d1_dominant_exclusive_fraction": d1_buckets[dominant_bucket],
        },
        "interpretation": {
            "fastest_mode_p_topology": ranked[0],
            "largest_safe_expert_hot_capacity": {
                "slots": 1573, "topologies": ["D2", "A1"],
            },
            "best_capacity_performance_tradeoff": max(
                ("D2", "A1"), key=lambda cell: pooled_results[cell]["decode_tps"]),
            "dedicated_dominant_critical_path_bucket": dominant_bucket,
            "transport_topology_scope": "2x RTX 3090 HOST_STAGED; P2P unavailable on this host",
            "automatic_topology_selection_added": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "status": "pass", "ranked": ranked,
        "decode_tps": {cell: pooled_results[cell]["decode_tps"] for cell in pooled_results},
        "s0_ratio": comparisons["s0_explicit_over_legacy"],
        "s1_ratio": comparisons["s1_explicit_over_legacy"],
        "d1_dominant_bucket": dominant_bucket,
    }, indent=2))


if __name__ == "__main__":
    main()

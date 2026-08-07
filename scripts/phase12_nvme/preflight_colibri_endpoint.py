#!/usr/bin/env python3
"""Derive and qualify fixed Colibrì endpoint cache capacities."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from run_colibri_endpoint import (
    EXPERT_BYTES,
    MAX_RSS_BYTES,
    ROUTED_LAYERS,
    derive_max_safe_slots,
    identity,
    meminfo,
    slots_for_capacity_bytes,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accepted-preflight", type=Path, required=True)
    parser.add_argument("--accepted-reference", type=Path, required=True)
    parser.add_argument("--accepted-route", type=Path, required=True)
    parser.add_argument("--pilot", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    accepted_preflight = json.loads(args.accepted_preflight.read_text())
    accepted_reference = json.loads(args.accepted_reference.read_text())
    pilot = json.loads(args.pilot.read_text())
    build = json.loads(args.build_manifest.read_text())
    if accepted_preflight["status"] != "PASS" or accepted_preflight["declared_ceilings"]["memory_bytes"] != MAX_RSS_BYTES:
        raise ValueError("accepted memory ceiling changed")
    if accepted_reference["disposition"] != "accepted" or pilot["status"] != "PASS" or build["status"] != "PASS":
        raise ValueError("required accepted endpoint evidence is unavailable")

    accepted_slots = int(accepted_reference["metrics"]["expert_cache_slots_per_layer"]) * ROUTED_LAYERS
    accepted_cache_bytes = accepted_slots * EXPERT_BYTES
    non_cache_rss = int(accepted_reference["process_resources"]["max_rss_bytes"]) - accepted_cache_bytes
    max_slots = derive_max_safe_slots(MAX_RSS_BYTES, non_cache_rss)
    max_cache_bytes = max_slots * ROUTED_LAYERS * EXPERT_BYTES
    projected_rss = non_cache_rss + max_cache_bytes
    next_projected_rss = projected_rss + ROUTED_LAYERS * EXPERT_BYTES
    if projected_rss > MAX_RSS_BYTES or next_projected_rss <= MAX_RSS_BYTES:
        raise ValueError("MAX_SAFE whole-slot derivation is not maximal")

    capacities = []
    for label, requested_gib in (("8GiB", 8), ("96GiB", 96)):
        requested_bytes = requested_gib * (1 << 30)
        slots = slots_for_capacity_bytes(requested_bytes)
        usable = slots * ROUTED_LAYERS * EXPERT_BYTES
        capacities.append({"label": label, "requested_gib": requested_gib, "requested_bytes": requested_bytes,
                           "slots_per_layer": slots, "usable_cache_bytes": usable,
                           "usable_cache_gib": usable / (1 << 30),
                           "projected_process_rss_bytes": non_cache_rss + usable})
    capacities.append({"label": "MAX_SAFE", "requested_gib": max_cache_bytes / (1 << 30),
                       "requested_bytes": max_cache_bytes, "slots_per_layer": max_slots,
                       "usable_cache_bytes": max_cache_bytes, "usable_cache_gib": max_cache_bytes / (1 << 30),
                       "projected_process_rss_bytes": projected_rss})

    old_lines = args.accepted_route.read_text().splitlines()
    pilot_route = Path(pilot["routing"]["normalized_route"]["path"])
    pilot_lines = pilot_route.read_text().splitlines()
    route_prefix_equal = pilot_lines == old_lines[:len(pilot_lines)]
    old_stdout = Path("/mnt/nvme0/k3-phase12-nvme-evidence/cache-locality/request-00/stdout.bin")
    pilot_stdout = Path(pilot["raw_artifacts"]["stdout.bin"]["path"])
    stdout_prefix_equal = old_stdout.read_bytes().startswith(pilot_stdout.read_bytes())
    final_counters = pilot["cache"]["final_run_counters"]
    failures: list[str] = []
    if not route_prefix_equal or not stdout_prefix_equal:
        failures.append("instrumented pilot differs from the accepted pinned route/output prefix")
    if int(final_counters["v7"]) != 0 or int(final_counters["v6"]) == 0:
        failures.append("instrumented pilot did not prove direct expert reads without full-expert fallback")
    if int(pilot["process_resources"]["maximum_swap_bytes"]) != 0:
        failures.append("instrumented pilot used swap")

    memory = meminfo()
    if memory.get("SwapTotal", 0) - memory.get("SwapFree", 0) != 0:
        failures.append("fresh host preflight has nonzero swap usage")
    document: dict[str, Any] = {
        "schema_version": "phase12-nvme-colibri-endpoint-preflight-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "blocked",
        "accepted_process_rss_ceiling_bytes": MAX_RSS_BYTES,
        "derivation": {
            "accepted_reference_max_rss_bytes": accepted_reference["process_resources"]["max_rss_bytes"],
            "accepted_reference_slots": accepted_slots,
            "accepted_reference_cache_bytes": accepted_cache_bytes,
            "inferred_non_cache_rss_bytes": non_cache_rss,
            "bytes_per_layer_slot": ROUTED_LAYERS * EXPERT_BYTES,
            "rounding": "floor to one equal slot per each of 92 routed layers before process start",
        },
        "capacities": capacities,
        "max_safe_proof": {"selected_slots_per_layer": max_slots, "projected_process_rss_bytes": projected_rss,
                           "next_slot_projected_process_rss_bytes": next_projected_rss,
                           "next_slot_crosses_ceiling": next_projected_rss > MAX_RSS_BYTES,
                           "fixed_before_process_start": True, "auto_growth": False},
        "fresh_host_memory": memory,
        "instrumentation_pilot": {"pilot": identity(args.pilot.resolve()), "build_manifest": identity(args.build_manifest.resolve()),
                                  "accepted_route_prefix_equal": route_prefix_equal,
                                  "accepted_output_prefix_equal": stdout_prefix_equal,
                                  "pilot_route_sha256": hashlib.sha256(pilot_route.read_bytes()).hexdigest(),
                                  "complete_decode_forwards": pilot["routing"]["complete_decode_forwards"],
                                  "direct_expert_reads": final_counters["v6"],
                                  "full_expert_buffered_fallbacks": final_counters["v7"],
                                  "maximum_rss_bytes": pilot["process_resources"]["maximum_rss_bytes"],
                                  "maximum_swap_bytes": pilot["process_resources"]["maximum_swap_bytes"]},
        "claim_boundary": "capacity preflight and evidence-instrumentation qualification only; no endpoint speedup claim and no policy/default/format change",
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "max_safe_slots_per_layer": max_slots,
                      "max_safe_usable_gib": max_cache_bytes / (1 << 30), "failures": failures}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

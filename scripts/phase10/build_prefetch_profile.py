#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from prefetch_common import (Phase10Error, build_fingerprint, canonical_bytes, file_identity, fold_membership,
    load_json, read_phase2_corpus, sha256_bytes, token_events, training_tables, validate_profile, write_json)


MATRIX = {
    "policies": ["DEMAND_BASELINE", "SERIAL_CONTROL", "RANDOM_BASELINE", "STATIC_LAYER", "PREVIOUS_TOKEN",
        "TEMPORAL_FREQUENCY", "CROSS_LAYER_TRANSITION", "BLOCKING_HOT"],
    "temporal_windows": [2, 4, 8, 16, 32, 64],
    "candidate_counts": [1, "n_expert_used", "2*n_expert_used", "topology_capped"],
    "readiness": ["HOST_READY", "DEVICE_READY"],
    "budget_points": ["below_working_set", "at_working_set", "above_working_set"],
    "transports": ["BUFFERED", "DIRECT_IO_WHEN_SUPPORTED"],
    "sequences": ["cold", "warm_decode", "domain_shift"],
    "shortlist_rule_version": 1,
}


def build(args: argparse.Namespace) -> dict:
    corpus = read_phase2_corpus(args.archive, args.artifact)
    storage_map = load_json(args.storage_map)
    target = build_fingerprint(storage_map)
    fold = fold_membership(args.fold)
    counts, transitions, training_rows = training_tables(corpus["traces"], fold["training"], target["routed_layers"])
    validation_rows = len(corpus["traces"][fold["validation"]]["records"])
    test_rows = len(corpus["traces"][fold["test"]]["records"])
    costs_document = load_json(args.costs)
    if costs_document.get("schema_version") != "phase10-transport-break-even-v1" or costs_document.get("status") != "pass":
        raise Phase10Error("cost input is not eligible")
    costs = [record["profile_record"] for record in costs_document["envelopes"] if record["eligible"]]
    if not costs:
        raise Phase10Error("no eligible cost envelope")
    selected_cost = next((record for record in costs
        if record["transport"] == args.transport and record["readiness"] == args.readiness), None)
    if selected_cost is None:
        raise Phase10Error("selected transport/readiness has no cost envelope")
    by_key = {(record["layer"], record["expert"]): record for record in target["expert_bytes"]}
    ranked_seed = sorted(counts, key=lambda value: (-value["count"], value["layer"], value["expert"]))[:args.seed_slots]
    seed = [{**record, "payload_bytes": by_key[(record["layer"], record["expert"])]["payload_bytes"],
        "physical_bytes": by_key[(record["layer"], record["expert"])]["physical_bytes"]} for record in ranked_seed]
    seed.sort(key=lambda value: (value["count"], value["layer"], value["expert"]))
    source_artifacts = [corpus["identities"][prompt] for prompt in fold["training"]]
    source_artifacts.append(corpus["archive"])
    tuning_digest = sha256_bytes(canonical_bytes(MATRIX))
    profile = {
        "schema_version": "expert-prefetch-profile-v1",
        "profile_id": f"phase10-{args.artifact}-fold{args.fold}",
        "tool": {"name": "build_prefetch_profile.py", "version": 1},
        "source": {"kind": "route_trace", "artifacts": source_artifacts,
            "fold": {**fold, "training_rows": training_rows, "validation_rows": validation_rows, "test_rows": test_rows}},
        "target": target,
        "static_counts": counts,
        "transitions": transitions,
        "costs": costs,
        "selection": {"matrix_version": 1, "tuning_digest": tuning_digest, "fold_index": args.fold,
            "candidates_per_target": args.candidates, "temporal_window_tokens": args.temporal_window,
            "transport": args.transport, "readiness": args.readiness,
            "break_even_bps": selected_cost["break_even_bps"]},
        "seed": seed,
    }
    validate_profile(profile)
    return profile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--storage-map", required=True)
    parser.add_argument("--artifact", choices=["f16", "mxfp4"], required=True)
    parser.add_argument("--fold", type=int, choices=range(6), required=True)
    parser.add_argument("--costs", required=True)
    parser.add_argument("--transport", choices=["BUFFERED", "DIRECT_IO", "HOST_TO_DEVICE"], default="BUFFERED")
    parser.add_argument("--readiness", choices=["HOST_READY", "DEVICE_READY"], default="DEVICE_READY")
    parser.add_argument("--candidates", type=int, default=2)
    parser.add_argument("--temporal-window", type=int, default=4)
    parser.add_argument("--seed-slots", type=int, default=14)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        profile = build(args)
        write_json(args.output, profile)
        print(f"{Path(args.output)} {hashlib.sha256(Path(args.output).read_bytes()).hexdigest()}")
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

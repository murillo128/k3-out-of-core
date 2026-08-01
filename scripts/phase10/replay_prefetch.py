#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from prefetch_common import (FNV_OFFSET, Phase10Error, config_digest, cross_candidates, fnv_append, load_json,
    predictor_candidates, require_fields, require_uint, validate_profile, write_json)


def replay(document: dict) -> dict:
    require_fields(document, {"schema_version", "profile_path", "policy", "readiness", "temporal_window_tokens",
        "candidates_per_target", "request_ordinal", "events", "completion_order"}, "replay")
    if document["schema_version"] != "phase10-prefetch-replay-v1":
        raise Phase10Error("unsupported replay schema")
    profile_path = document["profile_path"]
    if not isinstance(profile_path, str) or not profile_path:
        raise Phase10Error("profile_path must be a non-empty string")
    profile = load_json(profile_path)
    validate_profile(profile)
    policy = document["policy"]
    if not isinstance(policy, str) or policy not in {"OFF", "STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY", "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"}:
        raise Phase10Error("unknown policy")
    if not isinstance(document["readiness"], str) or document["readiness"] not in {"HOST_READY", "DEVICE_READY"}:
        raise Phase10Error("unknown readiness")
    candidate_count = require_uint(document["candidates_per_target"], "candidates_per_target", positive=True,
        maximum=profile["target"]["experts_per_layer"])
    request_ordinal = require_uint(document["request_ordinal"], "request_ordinal", positive=True)
    temporal_window = require_uint(document["temporal_window_tokens"], "temporal_window_tokens", maximum=64)
    if policy == "TEMPORAL_FREQUENCY":
        if temporal_window < 2 or temporal_window & (temporal_window - 1) != 0:
            raise Phase10Error("invalid temporal window")
    elif temporal_window != 0:
        raise Phase10Error("unexpected temporal window")
    if not isinstance(document["completion_order"], list) or any(
            require_uint(value, "completion ordinal") < 0 for value in document["completion_order"]):
        raise Phase10Error("invalid completion order")
    if len(document["completion_order"]) != len(set(document["completion_order"])):
        raise Phase10Error("duplicate completion ordinal")
    config = {"struct_size": 128, "policy": policy, "readiness": document["readiness"],
        "temporal_window_tokens": temporal_window, "candidates_per_target": candidate_count}
    profile_sha256 = hashlib.sha256(Path(profile_path).read_bytes()).hexdigest()
    digest = config_digest(config, profile_sha256)
    history: list[list[list[int]]] = []
    rolling = fnv_append(FNV_OFFSET, request_ordinal)
    stream = []
    layers = profile["target"]["routed_layers"]
    if not isinstance(document["events"], list):
        raise Phase10Error("events must be an array")
    for token, event in enumerate(document["events"]):
        require_fields(event, {"token", "layers"}, "event")
        if require_uint(event["token"], "event token") != token or not isinstance(event["layers"], list):
            raise Phase10Error("noncanonical event")
        event_layers = []
        for record in event["layers"]:
            require_fields(record, {"layer", "experts"}, "layer event")
            event_layers.append(require_uint(record["layer"], "event layer"))
        if event_layers != layers:
            raise Phase10Error("noncanonical event")
        routed = []
        for index, record in enumerate(event["layers"]):
            if not isinstance(record["experts"], list):
                raise Phase10Error("selected experts must be an array")
            raw_experts = record["experts"]
            if any(isinstance(expert, bool) or not isinstance(expert, int) for expert in raw_experts):
                raise Phase10Error("selected experts must be integers")
            experts = sorted(raw_experts)
            if len(experts) == 0 or len(experts) > profile["target"]["experts_per_token"] or \
                    len(experts) != len(set(experts)) or any(isinstance(expert, bool) or not isinstance(expert, int) or
                    expert < 0 or expert >= profile["target"]["experts_per_layer"] for expert in experts):
                raise Phase10Error("invalid selected experts")
            routed.append(experts)
            if policy == "CROSS_LAYER_TRANSITION" and index + 1 < len(layers):
                for rank, (expert, score) in enumerate(cross_candidates(profile, layers[index], experts, layers[index + 1], candidate_count)):
                    stream.append({"trigger_token": token, "trigger": "ROUTER_RESULT", "source_layer": layers[index],
                        "target_layer": layers[index + 1], "expert": expert, "rank": rank, "score": score})
        rolling = fnv_append(rolling, token)
        for experts in routed:
            rolling = fnv_append(rolling, len(experts))
            for expert in experts:
                rolling = fnv_append(rolling, expert)
        window = temporal_window if policy == "TEMPORAL_FREQUENCY" else 1
        if len(history) == window:
            history.pop(0)
        history.append(routed)
        if policy not in {"OFF", "CROSS_LAYER_TRANSITION"}:
            for layer in layers:
                for rank, (expert, score) in enumerate(predictor_candidates(
                        profile, policy, history, layer, candidate_count, digest, request_ordinal, token)):
                    stream.append({"trigger_token": token, "trigger": "TOKEN_END", "source_layer": -1,
                        "target_layer": layer, "expert": expert, "rank": rank, "score": score})
    compact_events = json.dumps(document["events"], ensure_ascii=True, separators=(",", ":"))
    return {"schema_version": "phase10-prefetch-replay-output-v1",
        "profile_sha256": profile_sha256, "policy": policy,
        "candidate_stream": stream, "state_digest": FNV_OFFSET if policy == "OFF" else rolling,
        "phase9_passthrough_sha256": hashlib.sha256(compact_events.encode("utf-8")).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = replay(load_json(args.input))
        if args.output:
            write_json(args.output, result)
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

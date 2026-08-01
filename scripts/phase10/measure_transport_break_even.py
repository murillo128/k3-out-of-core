#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from prefetch_common import Phase10Error, break_even, load_json, require_fields, require_sha256, require_uint, write_json


def derive(document: dict) -> dict:
    require_fields(document, {"schema_version", "project_head", "nested_head", "host", "profile_sha256",
        "profile_parse_ns", "model_profile_load_ns", "lead_measurements", "predictor_upper_bound", "envelopes"}, "measurements")
    if document["schema_version"] != "phase10-transport-measurements-v1":
        raise Phase10Error("unsupported measurement schema")
    for name in ("project_head", "nested_head"):
        if not isinstance(document[name], str) or len(document[name]) != 40 or any(
                byte not in "0123456789abcdef" for byte in document[name]):
            raise Phase10Error(f"{name} must be a lowercase commit SHA")
    if not isinstance(document["host"], str) or not document["host"]:
        raise Phase10Error("host must be non-empty")
    require_sha256(document["profile_sha256"], "profile_sha256")
    lead = document["lead_measurements"]
    require_fields(lead, {"basis", "decode_submissions", "token_end_samples", "cross_layer_samples",
        "token_end_p50_ns", "cross_layer_p50_ns", "conservative_lead_p50_ns",
        "provider_event_capacity", "provider_events_dropped"}, "lead measurements")
    if lead["basis"] != "minimum p50 of observed token-end-to-router and adjacent-router intervals":
        raise Phase10Error("invalid lead measurement basis")
    for name in ("decode_submissions", "token_end_samples", "cross_layer_samples", "token_end_p50_ns",
            "cross_layer_p50_ns", "conservative_lead_p50_ns"):
        require_uint(lead[name], name, positive=True)
    require_uint(lead["provider_event_capacity"], "provider event capacity", positive=True)
    if require_uint(lead["provider_events_dropped"], "provider events dropped") != 0 or \
            lead["token_end_samples"] > lead["provider_event_capacity"]:
        raise Phase10Error("provider lead transcript overflowed its fixed capacity")
    if lead["conservative_lead_p50_ns"] != min(lead["token_end_p50_ns"], lead["cross_layer_p50_ns"]):
        raise Phase10Error("conservative lead does not match measured intervals")
    predictor = document["predictor_upper_bound"]
    require_fields(predictor, {"basis", "upper_bound_p95_ns", "measurements"}, "predictor upper bound")
    if predictor["basis"] != "maximum p95 over full-token topology-capped declared predictors" or \
            not isinstance(predictor["measurements"], list) or not predictor["measurements"]:
        raise Phase10Error("invalid predictor upper-bound evidence")
    predictor_p95 = require_uint(predictor["upper_bound_p95_ns"], "predictor upper bound", positive=True)
    measured_p95 = []
    for measurement in predictor["measurements"]:
        require_fields(measurement, {"policy", "temporal_window_tokens", "candidates_per_target",
            "target_layers_per_call", "repetitions", "p50_ns", "p95_ns", "p99_ns"}, "predictor measurement")
        measured_p95.append(require_uint(measurement["p95_ns"], "predictor p95", positive=True))
        require_uint(measurement["p50_ns"], "predictor p50", positive=True)
        require_uint(measurement["p99_ns"], "predictor p99", positive=True)
        require_uint(measurement["repetitions"], "predictor repetitions", positive=True)
    if predictor_p95 != max(measured_p95):
        raise Phase10Error("predictor upper bound does not match measured p95 values")
    if not isinstance(document["envelopes"], list) or not document["envelopes"]:
        raise Phase10Error("measurement envelopes are empty")
    output = []
    for envelope in document["envelopes"]:
        require_fields(envelope, {"transport", "readiness", "supported", "lead_p50_ns", "demand_service_p50_ns",
            "speculative_service_p95_ns", "predictor_compute_p95_ns", "scheduler_demand_delay_p95_ns",
            "displacement_refill_p95_ns", "storage_bytes", "h2d_bytes", "utility_window_predictions",
            "utility_min_observations"}, "measurement envelope")
        if envelope["readiness"] not in {"HOST_READY", "DEVICE_READY"}:
            raise Phase10Error("invalid readiness")
        record = {"lead_ns": require_uint(envelope["lead_p50_ns"], "lead_p50_ns"),
            "demand_service_ns": require_uint(envelope["demand_service_p50_ns"], "demand_service_p50_ns"),
            "speculative_service_ns": require_uint(envelope["speculative_service_p95_ns"], "speculative_service_p95_ns"),
            "predictor_compute_ns": require_uint(envelope["predictor_compute_p95_ns"], "predictor_compute_p95_ns"),
            "scheduler_demand_delay_ns": require_uint(envelope["scheduler_demand_delay_p95_ns"], "scheduler_demand_delay_p95_ns"),
            "displacement_refill_ns": require_uint(envelope["displacement_refill_p95_ns"], "displacement_refill_p95_ns")}
        if record["lead_ns"] != lead["conservative_lead_p50_ns"]:
            raise Phase10Error("envelope did not use the declared conservative lead")
        if record["predictor_compute_ns"] != predictor_p95:
            raise Phase10Error("envelope did not use the declared predictor upper bound")
        if not isinstance(envelope["supported"], bool):
            raise Phase10Error("supported must be boolean")
        eligible = envelope["supported"]
        reason = "eligible"
        try:
            computed = break_even(record)
        except Phase10Error as error:
            eligible = False
            reason = str(error)
            computed = {"hidden_benefit_ns": 0, "waste_cost_ns": 0, "break_even_bps": 0}
        window = require_uint(envelope["utility_window_predictions"], "utility_window_predictions", positive=True, maximum=(1 << 32) - 1)
        minimum = require_uint(envelope["utility_min_observations"], "utility_min_observations", positive=True, maximum=window)
        minimum_successes = (computed["break_even_bps"] * window + 9999) // 10000 if eligible else 0
        profile_record = {"transport": envelope["transport"], "readiness": envelope["readiness"], **record,
            "storage_bytes": require_uint(envelope["storage_bytes"], "storage_bytes"),
            "h2d_bytes": require_uint(envelope["h2d_bytes"], "h2d_bytes"),
            "break_even_bps": computed["break_even_bps"], "utility_window_predictions": window,
            "utility_min_observations": minimum, "utility_min_timely_successes": minimum_successes}
        output.append({"transport": envelope["transport"], "readiness": envelope["readiness"],
            "eligible": eligible, "reason": reason, "conservative_basis": "p50 useful lower envelope; p95 predictor/service/delay/displacement waste upper envelope",
            **computed, "profile_record": profile_record})
    return {"schema_version": "phase10-transport-break-even-v1", "status": "pass" if any(item["eligible"] for item in output) else "fail",
        "project_head": document["project_head"], "nested_head": document["nested_head"], "host": document["host"],
        "profile_sha256": document["profile_sha256"],
        "profile_parse_ns": require_uint(document["profile_parse_ns"], "profile_parse_ns"),
        "model_profile_load_ns": require_uint(document["model_profile_load_ns"], "model_profile_load_ns"),
        "lead_measurements": lead,
        "predictor_upper_bound": predictor,
        "formula": "waste/(hidden+waste)", "waste_external_threshold_transferred": False, "envelopes": output}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--measurements", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = derive(load_json(args.measurements))
        write_json(args.output, result)
        print(Path(args.output))
        return 0 if result["status"] == "pass" else 2
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

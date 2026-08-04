#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from prefetch_common import (Phase10Error, canonical_bytes, load_json, require_capture_heads,
    validate_profile, write_json)
from replay_prefetch import replay


FNV_OFFSET = 1469598103934665603
OUTCOME_FIELDS = {
    "TIMELY_USEFUL": "timely_useful",
    "LATE_JOINED": "late_joined",
    "WASTED_UNUSED": "wasted_unused",
    "CANCELLED_BEFORE_IO": "cancelled_before_io",
    "CANCELLED_DRAINED": "cancelled_drained",
    "REJECTED": "rejected",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024*1024):
            digest.update(chunk)
    return digest.hexdigest()


def evidence_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path) -> dict[str, Any]:
    return {"path": evidence_path(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def package_record(path: Path, target: dict[str, Any]) -> dict[str, Any]:
    return {"first_path": evidence_path(path), "file_count": len(target["files"]),
        "total_size": sum(item["size"] for item in target["files"]),
        "package_sha256": target["package_sha256"],
        "tensor_layout_sha256": target["tensor_layout_sha256"]}


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def run_probe(
        probe: Path,
        profile: Path,
        model: Path,
        identity: str,
        enabled: bool,
        runtime: tuple[str, str, str, str, str]) -> dict[str, Any]:
    command = [str(probe), "--profile", str(profile), "--model", str(model),
        "--identity", identity, "--online" if enabled else "--online-disabled", *runtime]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode != 0:
        tail = completed.stderr.decode(errors="replace").splitlines()[-24:]
        raise Phase10Error(f"online probe failed ({completed.returncode}):\n" + "\n".join(tail))
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Phase10Error(f"online probe emitted invalid JSON: {error}") from error


def validate_capture(document: dict[str, Any], validator: Draft202012Validator, enabled: bool) -> None:
    validator.validate(document)
    if document["profile_enabled"] is not enabled:
        raise Phase10Error("online capture profile state mismatch")
    if len(document["generated_tokens"]) != len(document["logit_sha256"]):
        raise Phase10Error("online token/logit stream lengths differ")
    summary = document["summary"]
    if summary["route_events"] != len(document["routes"]) or \
            summary["prediction_events"] != len(document["predictions"]):
        raise Phase10Error("online summary disagrees with bounded transcript")
    counts = Counter(item["outcome"] for item in document["predictions"])
    for outcome, field in OUTCOME_FIELDS.items():
        if summary[field] != counts[outcome]:
            raise Phase10Error(f"online outcome counter mismatch: {outcome}")
    if summary["active_background_flights"] != 0 or summary["current_pins"] != 0:
        raise Phase10Error("online request teardown retained work or pins")
    if summary["runtime_failed"]:
        raise Phase10Error("online predictor runtime entered a failed state")
    if enabled:
        if not document["routes"] or not document["predictions"]:
            raise Phase10Error("active online capture is empty")
    elif document["routes"] or document["predictions"] or any(summary[field] != 0 for field in (
            "route_events", "prediction_events", "admitted", "rejected", "timely_useful",
            "late_joined", "wasted_unused", "cancelled_before_io", "cancelled_drained",
            "predictor_compute_ns", "circuit_opens")) or summary["circuit_open"] or \
            summary["predictor_state_digest"] != FNV_OFFSET:
        raise Phase10Error("disabled online run created predictor state")


def route_events(capture: dict[str, Any], profile: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    routes = capture["routes"]
    requests = {item["request"] for item in routes}
    if len(requests) != 1:
        raise Phase10Error("online capture contains multiple predictor request ordinals")
    by_token: dict[int, list[dict[str, Any]]] = {}
    for route in routes:
        by_token.setdefault(route["token"], []).append(route)
    if sorted(by_token) != list(range(len(by_token))):
        raise Phase10Error("online route tokens are not canonical")
    layers = profile["target"]["routed_layers"]
    events = []
    for token in range(len(by_token)):
        records = by_token[token]
        if [item["layer"] for item in records] != layers:
            raise Phase10Error("online routed layer order differs from profile")
        events.append({"token": token, "layers": [{"layer": item["layer"],
            "experts": item["selected_experts"]} for item in records]})
    return next(iter(requests)), events


def replay_request(profile_path: Path, profile: dict[str, Any], capture: dict[str, Any]) -> dict[str, Any]:
    request_ordinal, events = route_events(capture, profile)
    sizes = {item["physical_bytes"] for item in profile["target"]["expert_bytes"]}
    if len(sizes) != 1:
        raise Phase10Error("online replay requires uniform expert bundle sizes")
    bundle = next(iter(sizes))
    selection = profile["selection"]
    return {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
        "policy": selection["policy"], "cache_mode": capture["cache_mode"],
        "miss_policy": capture["miss_policy"], "transport": selection["transport"],
        "readiness": selection["readiness"],
        "temporal_window_tokens": selection["temporal_window_tokens"],
        "candidates_per_target": selection["candidates_per_target"],
        "request_ordinal": request_ordinal, "events": events, "completion_order": [],
        "ready_before_deadline": [index for index, item in enumerate(capture["predictions"])
            if item["admitted"] and item["cold_ready" if selection["readiness"] == "HOST_READY" else "device_ready"]],
        "initial_resident": capture["initial_resident"],
        "limits": {"cold_capacity_bytes": capture["cold_slots"]*bundle,
            "hot_capacity_slots": capture["hot_slots"],
            "max_speculative_flights": 4, "max_speculative_storage_bytes_in_flight": 4*bundle,
            "max_speculative_h2d_bytes_in_flight": 4*bundle,
            "max_speculative_storage_bytes_per_token": 4*bundle,
            "max_speculative_h2d_bytes_per_token": 4*bundle,
            "max_speculative_cold_slots": 4, "max_speculative_hot_slots": 4},
        "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}


def run_native(native: Path, request: dict[str, Any], directory: Path) -> dict[str, Any]:
    request_path = directory / "request.json"
    write_json(request_path, request)
    completed = subprocess.run([str(native), str(request_path)], check=False, capture_output=True)
    if completed.returncode != 0:
        raise Phase10Error(f"native replay failed ({completed.returncode}): {completed.stderr.decode(errors='replace')}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise Phase10Error(f"native replay emitted invalid JSON: {error}") from error


def runtime_candidates(capture: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"trigger_token": item["token"], "trigger": item["trigger"],
        "source_layer": item["source_layer"], "target_layer": item["target_layer"],
        "expert": item["expert"], "rank": item["rank"], "score": item["score"]}
        for item in capture["predictions"]]


def replay_candidates(document: dict[str, Any]) -> list[dict[str, Any]]:
    fields = ("trigger_token", "trigger", "source_layer", "target_layer", "expert", "rank", "score")
    return [{field: item[field] for field in fields} for item in document["candidate_stream"]]


def runtime_decisions(capture: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for index, item in enumerate(capture["predictions"]):
        admitted = item["admitted"]
        result.append({"flight_ordinal": index, "token": item["token"],
            "deadline_token": item["deadline_token"], "deadline_layer": item["target_layer"],
            "layer": item["target_layer"], "expert": item["expert"],
            "priority": {2: "PREFETCH_NEXT", 3: "PREFETCH_SPECULATIVE"}.get(item["priority"], "INVALID"),
            "admitted": admitted,
            "cold_slot": item["cold_slot"] if admitted and item["cold_slot"] != 4294967295 else -1,
            "hot_slot": item["hot_slot"] if admitted and item["hot_slot"] != 4294967295 else -1,
            "storage_bytes": item["storage_bytes"] if admitted else 0,
            "h2d_bytes": item["h2d_bytes"] if admitted else 0,
            "ready_before_deadline": admitted and
                item["cold_ready" if item["readiness"] == "HOST_READY" else "device_ready"],
            "outcome": item["outcome"],
            "demand_claimed": item["demand_claimed"],
            "circuit_open_after": item["circuit_open_after"]})
    return result


def replay_decisions(document: dict[str, Any], readiness: str) -> list[dict[str, Any]]:
    primary = {item["flight_ordinal"]: item for item in document["action_stream"]
        if item["type"] in {"ENQUEUE", "REJECTED"}}
    outcomes = {item["flight_ordinal"]: item for item in document["outcome_stream"]}
    result = []
    for index, candidate in enumerate(document["candidate_stream"]):
        action = primary.get(index)
        if action is None:
            raise Phase10Error(f"replay candidate {index} has no admission decision")
        admitted = action["type"] == "ENQUEUE"
        outcome = outcomes.get(index)
        if admitted != (outcome is not None):
            raise Phase10Error(f"replay candidate {index} has inconsistent terminal outcome")
        result.append({"flight_ordinal": index, "token": candidate["trigger_token"],
            "deadline_token": action.get("deadline_token",
                candidate["trigger_token"] if candidate["trigger"] == "ROUTER_RESULT" else
                    candidate["trigger_token"] + 1),
            "deadline_layer": action.get("deadline_layer", candidate["target_layer"]),
            "layer": candidate["target_layer"], "expert": candidate["expert"],
            "priority": action.get("priority", "PREFETCH_SPECULATIVE" if document["policy"] in
                {"STATIC_LAYER", "RANDOM_BASELINE"} else "PREFETCH_NEXT"),
            "admitted": admitted, "cold_slot": action["cold_slot"], "hot_slot": action["hot_slot"],
            "storage_bytes": action.get("storage_bytes", 0), "h2d_bytes": action.get("h2d_bytes", 0),
            "ready_before_deadline": admitted and action.get("completion_phase") == "READY",
            "outcome": outcome["type"] if outcome is not None else "REJECTED",
            "demand_claimed": outcome is not None and outcome["type"] in {"TIMELY_USEFUL", "LATE_JOINED"},
            "circuit_open_after": False})
    return result


def capture_case(
        name: str,
        profile_path: Path,
        model_path: Path,
        probe: Path,
        native: Path,
        identity: str,
        validator: Draft202012Validator,
        raw_dir: Path,
        runtime: tuple[str, str, str, str, str]) -> dict[str, Any]:
    profile = load_json(profile_path)
    validate_profile(profile)
    profile_sha = sha256_file(profile_path)
    active = run_probe(probe, profile_path, model_path, identity, True, runtime)
    disabled = run_probe(probe, profile_path, model_path, identity, False, runtime)
    validate_capture(active, validator, True)
    validate_capture(disabled, validator, False)
    if active["profile_sha256"] != profile_sha or disabled["profile_sha256"] != profile_sha:
        raise Phase10Error("online capture profile identity mismatch")
    expected_runtime = {"cache_mode": runtime[0], "load_mode": runtime[1],
        "miss_policy": runtime[2], "hot_slots": int(runtime[3]), "cold_slots": int(runtime[4])}
    if any(active[field] != value or disabled[field] != value
            for field, value in expected_runtime.items()):
        raise Phase10Error("online runtime configuration mismatch")
    if active["generated_tokens"] != disabled["generated_tokens"]:
        raise Phase10Error("active profile changed generated token IDs")
    if active["logit_sha256"] != disabled["logit_sha256"]:
        raise Phase10Error("active profile changed full-logit bytes")

    request = replay_request(profile_path, profile, active)
    python_output = replay(json.loads(json.dumps(request)))
    with tempfile.TemporaryDirectory(prefix="phase10-online-") as directory:
        native_output = run_native(native, request, Path(directory))
    python_stream = replay_candidates(python_output)
    native_stream = replay_candidates(native_output)
    runtime_stream = runtime_candidates(active)
    if python_output != native_output:
        raise Phase10Error("Python and native online-route replay outputs differ")
    if runtime_stream != python_stream or runtime_stream != native_stream:
        raise Phase10Error("runtime predictor decisions differ from replay")
    runtime_decision_stream = runtime_decisions(active)
    python_decision_stream = replay_decisions(python_output, profile["selection"]["readiness"])
    native_decision_stream = replay_decisions(native_output, profile["selection"]["readiness"])
    if runtime_decision_stream != python_decision_stream or \
            runtime_decision_stream != native_decision_stream:
        raise Phase10Error("runtime admission/deadline/outcome decisions differ from replay")
    state_digest = python_output["predictor_state_digest"]
    if state_digest != native_output["predictor_state_digest"] or \
            state_digest != active["summary"]["predictor_state_digest"]:
        raise Phase10Error("runtime, Python, and native predictor state differ")
    runtime_hierarchy = {"predictions": active["summary"]["prediction_events"],
        "accepted": active["summary"]["admitted"], "rejected": active["summary"]["rejected"],
        "timely_useful": active["summary"]["timely_useful"],
        "late_joined": active["summary"]["late_joined"],
        "wasted_unused": active["summary"]["wasted_unused"],
        "cancelled_before_io": active["summary"]["cancelled_before_io"],
        "cancelled_drained": active["summary"]["cancelled_drained"]}
    replay_hierarchy = {field: python_output["summary"][field] for field in runtime_hierarchy}
    if runtime_hierarchy != replay_hierarchy:
        raise Phase10Error(
            f"runtime and replay hierarchy summaries differ: runtime={runtime_hierarchy} replay={replay_hierarchy}")

    raw = {"schema_version": "phase10-online-raw-v1", "name": name,
        "active": active, "disabled": disabled, "replay_request": request,
        "python_replay": python_output, "native_replay": native_output}
    raw_path = raw_dir / f"{name}.json.gz"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0) as compressed:
            compressed.write(canonical_bytes(raw))

    def capture_summary(document: dict[str, Any]) -> dict[str, Any]:
        return {"profile_enabled": document["profile_enabled"],
            "generated_tokens_sha256": value_sha256(document["generated_tokens"]),
            "logit_stream_sha256": value_sha256(document["logit_sha256"]),
            "initial_resident_sha256": value_sha256(document["initial_resident"]),
            "route_stream_sha256": value_sha256(document["routes"]),
            "prediction_stream_sha256": value_sha256(document["predictions"]),
            "summary": document["summary"]}

    return {"name": name, "status": "pass", "runtime": expected_runtime,
        "profile": file_record(profile_path),
        "model": package_record(model_path, profile["target"]), "raw_capture": file_record(raw_path),
        "active": capture_summary(active), "disabled": capture_summary(disabled),
        "agreement": {"tokens_exact": True, "logits_exact": True,
            "runtime_python_candidates_exact": True, "runtime_native_candidates_exact": True,
            "python_native_replay_exact": True, "predictor_state_exact": True,
            "runtime_replay_hierarchy_exact": True,
            "runtime_python_decisions_exact": True, "runtime_native_decisions_exact": True,
            "candidate_stream_sha256": value_sha256(runtime_stream),
            "decision_stream_sha256": value_sha256(runtime_decision_stream),
            "predictor_state_digest": state_digest}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--native-replay", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--case", action="append", nargs=8,
        metavar=("NAME", "PROFILE", "MODEL", "CACHE", "LOAD", "MISS", "HOT_SLOTS", "COLD_SLOTS"),
        required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_capture_heads(args.project_head, args.nested_head)
        schema_path = Path(__file__).resolve().parents[2] / "schemas/phase10/online-capture-v1.schema.json"
        schema = load_json(schema_path)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        identity = f"{args.project_head}:{args.nested_head}"
        names = [case[0] for case in args.case]
        if len(names) != len(set(names)):
            raise Phase10Error("online case names must be unique")
        cases = []
        for name, profile, model, cache, load, miss, hot_slots, cold_slots in args.case:
            try:
                cases.append(capture_case(name, Path(profile).resolve(), Path(model).resolve(),
                    args.probe.resolve(), args.native_replay.resolve(), identity, validator,
                    args.raw_dir.resolve(), (cache, load, miss, hot_slots, cold_slots)))
            except Phase10Error as error:
                raise Phase10Error(f"online case {name}: {error}") from error
        output = {"schema_version": "phase10-online-equivalence-v1", "status": "pass",
            "project_head": args.project_head, "nested_head": args.nested_head,
            "capture_schema": file_record(schema_path), "cases": cases}
        aggregate_schema = load_json(
            Path(__file__).resolve().parents[2] / "schemas/phase10/online-equivalence-v1.schema.json")
        Draft202012Validator.check_schema(aggregate_schema)
        Draft202012Validator(aggregate_schema).validate(output)
        write_json(args.output, output)
        print(json.dumps({"status": "pass", "cases": len(cases)}, sort_keys=True))
        return 0
    except (OSError, Phase10Error, ValueError, json.JSONDecodeError, ValidationError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

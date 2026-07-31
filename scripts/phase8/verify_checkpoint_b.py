#!/usr/bin/env python3
"""Fail-closed verifier for the bounded Phase 8 Checkpoint B production probe."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from evaluate_auto_cost import evaluate_auto


EXPECTED_CASES = frozenset({
    "auto_operands_none",
    "auto_operands_queued",
    "auto_operands_in_flight",
    "auto_operands_complete_unpublished",
    "frozen_gpu_scheduler_join_mismatch",
    "frozen_gpu_wait_failed",
    "frozen_gpu_publication_failed",
    "normalization_failed",
    "normalization_stale",
    "prepublication_h2d_failure",
    "prepublication_metadata_invalid",
    "prepublication_unload_queued",
    "prepublication_unload_in_flight",
    "prepublication_unload_complete_unpublished",
    "current_output_nonblocking",
    "published_wasted",
    "published_useful",
    "model_f16_smoke",
    "model_mxfp4_smoke",
    "model_f16_destroy_queued",
    "model_f16_destroy_in_flight",
    "model_f16_destroy_complete_unpublished",
    "model_mxfp4_destroy_queued",
    "model_mxfp4_destroy_in_flight",
    "model_mxfp4_destroy_complete_unpublished",
})

AUTO_STATES = {
    "auto_operands_none": (False, "NONE", 0),
    "auto_operands_queued": (True, "QUEUED_OR_STAGING", None),
    "auto_operands_in_flight": (True, "H2D_IN_FLIGHT", None),
    "auto_operands_complete_unpublished": (True, "H2D_COMPLETE_UNPUBLISHED", 0),
}
AUTO_QUEUE_WORK = (0, 0, 0)
FROZEN_CASES = frozenset({
    "frozen_gpu_scheduler_join_mismatch",
    "frozen_gpu_wait_failed",
    "frozen_gpu_publication_failed",
})
NORMALIZATION_CASES = frozenset({
    "normalization_failed",
    "normalization_stale",
    "prepublication_h2d_failure",
    "prepublication_metadata_invalid",
})
UNLOAD_CASES = frozenset({
    "prepublication_unload_queued",
    "prepublication_unload_in_flight",
    "prepublication_unload_complete_unpublished",
})
MODEL_CASES = frozenset(name for name in EXPECTED_CASES if name.startswith("model_"))

ZERO_CLOSEOUT_FIELDS = (
    "scheduler_active",
    "scheduler_queued",
    "ring_queued_workers",
    "ring_running_workers",
    "ring_non_free_lanes",
    "ring_live_events",
    "cold_hot_refs",
    "cold_transfer_refs",
    "cold_request_refs",
    "cold_cpu_execution_refs",
    "hot_pins",
    "published_forward_mappings",
)


class VerificationError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw, object_pairs_hook=no_duplicate_object)
    require(isinstance(value, dict), "probe root must be an object")
    return value, raw


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def require_int(value: Any, name: str) -> int:
    require(isinstance(value, int) and not isinstance(value, bool) and value >= 0,
            f"{name} must be a nonnegative integer")
    return value


def verify_closeout(case_name: str, case: dict[str, Any]) -> None:
    closeout = case.get("closeout")
    require(isinstance(closeout, dict), f"{case_name}: closeout missing")
    require(closeout.get("written") is True and closeout.get("write_count") == 1,
            f"{case_name}: closeout must be written exactly once")
    require(closeout.get("final_invariants_ok") is True,
            f"{case_name}: closeout invariants failed")
    for field in ZERO_CLOSEOUT_FIELDS:
        require(closeout.get(field) == 0, f"{case_name}: nonzero closeout {field}")
    for field in ("scheduler_terminal_complete", "scheduler_terminal_failed",
                  "scheduler_terminal_cancelled", "scheduler_terminal_releases"):
        require_int(closeout.get(field), f"{case_name}.closeout.{field}")


def auto_input(record: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "cost", "prefill", "lanes", "bundle_bytes", "queued_cpu_work_ns",
        "queued_h2d_work_ns", "queued_gpu_work_ns", "same_key_h2d_present",
        "same_key_h2d_state", "same_key_h2d_remaining_bytes",
    )
    return {field: record.get(field) for field in fields}


def verify_auto_record(case_name: str, record: Any) -> None:
    require(isinstance(record, dict), f"{case_name}: AUTO record missing")
    require_int(record.get("request"), f"{case_name}.record.request")
    require(isinstance(record.get("layer"), int) and record["layer"] >= 0,
            f"{case_name}: invalid record layer")
    require(isinstance(record.get("expert"), int) and record["expert"] >= 0,
            f"{case_name}: invalid record expert")
    expected = evaluate_auto(auto_input(record))
    require(record.get("result") == expected,
            f"{case_name}: independent AUTO evaluator mismatch")


def command_flags(command: list[Any]) -> dict[str, str]:
    require(command and isinstance(command[0], str) and command[0].endswith("phase8-checkpoint-b-probe"),
            "unexpected probe executable")
    require(len(command) == 11, "probe command must contain exactly five flag/value pairs")
    result: dict[str, str] = {}
    for index in range(1, len(command), 2):
        flag, value = command[index:index + 2]
        require(isinstance(flag, str) and isinstance(value, str), "probe command entries must be strings")
        require(flag not in result, f"duplicate probe command flag: {flag}")
        result[flag] = value
    require(set(result) == {"--output", "--outer-head", "--nested-head", "--f16", "--mxfp4"},
            "unexpected probe command flags")
    return result


def verify_payload(
        payload: dict[str, Any],
        *,
        expected_outer: str,
        expected_nested: str,
        probe_path: Path | None = None,
        expected_models: dict[str, dict[str, Any]] | None = None) -> None:
    require(payload.get("schema") == "phase8-checkpoint-b-probe-v1", "unexpected probe schema")
    require(payload.get("outer_head") == expected_outer, "stale outer head")
    require(payload.get("nested_head") == expected_nested, "stale nested head")
    require(payload.get("exit_code") == 0, "probe exit code is not zero")
    cwd_value = payload.get("cwd")
    require(isinstance(cwd_value, str) and Path(cwd_value).is_absolute(), "probe cwd must be absolute")
    command = payload.get("command")
    require(isinstance(command, list), "probe command missing")
    flags = command_flags(command)
    require(flags["--outer-head"] == expected_outer and flags["--nested-head"] == expected_nested,
            "probe command head mismatch")
    if probe_path is not None:
        require((Path(cwd_value) / flags["--output"]).resolve() == probe_path.resolve(),
                "probe command output path mismatch")

    models = payload.get("models")
    require(isinstance(models, dict) and set(models) == {"f16", "mxfp4"}, "model binding set mismatch")
    for kind in ("f16", "mxfp4"):
        require(isinstance(models[kind], dict) and models[kind].get("path") == flags[f"--{kind}"],
                f"{kind}: command/model path mismatch")
        if expected_models is not None:
            actual_path = (Path(cwd_value) / models[kind]["path"]).resolve()
            expected = expected_models[kind]
            require(actual_path.is_file(), f"{kind}: model file missing")
            require(actual_path.stat().st_size == expected["size"], f"{kind}: model size mismatch")
            require(sha256_file(actual_path) == expected["sha256"], f"{kind}: model digest mismatch")

    cases = payload.get("cases")
    require(isinstance(cases, dict), "cases must be an object")
    require(set(cases) == EXPECTED_CASES,
            f"case-key mismatch missing={sorted(EXPECTED_CASES - set(cases))} extra={sorted(set(cases) - EXPECTED_CASES)}")
    for name, case in cases.items():
        require(isinstance(case, dict) and case.get("status") == "pass", f"{name}: case did not pass")

    for name, (present, state, remaining) in AUTO_STATES.items():
        case = cases[name]
        require(case.get("decision_digest_entries") == 1, f"{name}: decision digest cardinality mismatch")
        record = case.get("record")
        verify_auto_record(name, record)
        require((record["queued_cpu_work_ns"], record["queued_h2d_work_ns"],
                 record["queued_gpu_work_ns"]) == AUTO_QUEUE_WORK,
                f"{name}: nondeterministic queued-work operands")
        require(record["bundle_bytes"] == 96, f"{name}: unexpected fixture bundle bytes")
        require(record["same_key_h2d_present"] is present, f"{name}: same-key presence mismatch")
        require(record["same_key_h2d_state"] == state, f"{name}: same-key state mismatch")
        if remaining is None:
            require(record["same_key_h2d_remaining_bytes"] == record["bundle_bytes"],
                    f"{name}: remaining bytes mismatch")
        else:
            require(record["same_key_h2d_remaining_bytes"] == remaining,
                    f"{name}: remaining bytes mismatch")

    for name in FROZEN_CASES:
        case = cases[name]
        require(case.get("request_status") in {"failed", "cancelled"}, f"{name}: request did not fail")
        for field, expected in {
            "decision_count": 1, "backend": "gpu", "new_cpu_execution_refs": 0,
            "active_cpu_lanes": 0, "written_cpu_ids": 0, "evaluator_invocations": 1,
            "backend_switches": 0,
        }.items():
            require(case.get(field) == expected, f"{name}: {field} mismatch")
        verify_auto_record(name, case.get("record"))
        require(case["record"]["result"]["backend"] == "gpu", f"{name}: altered backend")
        verify_closeout(name, case)
        require(case["closeout"]["scheduler_terminal_complete"] == 0,
                f"{name}: scheduler completed before publication")

    for name in NORMALIZATION_CASES:
        case = cases[name]
        for field, expected in {
            "terminalized_before_evaluation": True,
            "same_key_h2d_present": False,
            "scheduler_complete_delta": 0,
            "scheduler_failed_or_cancelled_delta": 1,
            "background_drop_or_fail_delta": 1,
            "useful_delta": 0,
            "wasted_delta": 0,
        }.items():
            require(case.get(field) == expected, f"{name}: {field} mismatch")
        verify_auto_record(name, case.get("record"))
        require(case["record"]["same_key_h2d_present"] is False, f"{name}: stale record survived normalization")
        verify_closeout(name, case)
        require(case["closeout"]["scheduler_terminal_complete"] == 0,
                f"{name}: scheduler completed before publication")

    for name in UNLOAD_CASES:
        case = cases[name]
        for field, expected in {
            "scheduler_success_delta": 0,
            "terminal": "cancelled",
            "background_drop_or_fail_delta": 1,
            "useful_delta": 0,
            "wasted_delta": 0,
        }.items():
            require(case.get(field) == expected, f"{name}: {field} mismatch")
        verify_closeout(name, case)
        require(case["closeout"]["scheduler_terminal_complete"] == 0,
                f"{name}: scheduler completed before publication")

    nonblocking = cases["current_output_nonblocking"]
    for field in ("current_remap_returned_while_gate_closed", "later_join_same_scheduler",
                  "later_join_same_lane", "later_join_same_hot_generation"):
        require(nonblocking.get(field) is True, f"current_output_nonblocking: {field} missing")
    verify_auto_record("current_output_nonblocking", nonblocking.get("record"))
    verify_closeout("current_output_nonblocking", nonblocking)

    wasted = cases["published_wasted"]
    useful = cases["published_useful"]
    require(wasted.get("useful_delta") == 0 and wasted.get("wasted_delta") == 1 and
            wasted.get("counted_once") is True, "published_wasted accounting mismatch")
    require(useful.get("useful_delta") == 1 and useful.get("wasted_delta") == 0 and
            useful.get("counted_once") is True, "published_useful accounting mismatch")
    verify_closeout("published_wasted", wasted)
    verify_closeout("published_useful", useful)

    for name in MODEL_CASES:
        case = cases[name]
        kind = "mxfp4" if "mxfp4" in name else "f16"
        require(case.get("model_path") == models[kind]["path"], f"{name}: model path mismatch")
        if name.endswith("smoke"):
            require(case.get("decode_status") == 0 and case.get("cpu_execution_lanes_positive") is True,
                    f"{name}: smoke execution failed")
            route = case.get("route_key")
            require(isinstance(route, dict) and isinstance(route.get("layer"), int) and route["layer"] >= 0 and
                    isinstance(route.get("expert"), int) and route["expert"] >= 0,
                    f"{name}: route key missing")
        else:
            require(case.get("gate_reached_before_model_reset") is True,
                    f"{name}: destruction gate not observed")
            require(case.get("useful_delta") == 0 and case.get("wasted_delta") == 0,
                    f"{name}: prepublication useful/wasted overlap")
        verify_closeout(name, case)


def git_output(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def verify_repository_binding(
        *, actual_outer: str, actual_nested: str, gitlink: str,
        expected_outer: str, expected_nested: str) -> None:
    require(actual_outer == expected_outer, "current outer HEAD mismatch")
    require(actual_nested == expected_nested, "current nested HEAD mismatch")
    require(gitlink == expected_nested, "outer gitlink mismatch")


def expected_models_from_manifest(manifest: Path) -> dict[str, dict[str, Any]]:
    value, _ = load_json(manifest)
    entries = value.get("inputs", {}).get("models", [])
    require(isinstance(entries, list), "Phase 7 model identities missing")
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        require(isinstance(entry, dict), "invalid Phase 7 model entry")
        path = entry.get("path", "")
        if path.endswith("F16.gguf"):
            result["f16"] = entry
        elif path.endswith("MXFP4.gguf"):
            result["mxfp4"] = entry
    require(set(result) == {"f16", "mxfp4"}, "Phase 7 original model set mismatch")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--phase7-manifest", type=Path, required=True)
    parser.add_argument("--outer-head", required=True)
    parser.add_argument("--nested-head", required=True)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[2]
    payload, raw = load_json(args.probe)
    actual_outer = git_output(repo, "rev-parse", "HEAD")
    actual_nested = git_output(repo / "llama.cpp", "rev-parse", "HEAD")
    gitlink = git_output(repo, "ls-tree", "HEAD", "llama.cpp").split()[2]
    verify_repository_binding(
        actual_outer=actual_outer, actual_nested=actual_nested, gitlink=gitlink,
        expected_outer=args.outer_head, expected_nested=args.nested_head)
    verify_payload(
        payload,
        expected_outer=args.outer_head,
        expected_nested=args.nested_head,
        probe_path=args.probe,
        expected_models=expected_models_from_manifest(args.phase7_manifest),
    )
    result = {
        "status": "pass",
        "schema": payload["schema"],
        "outer_head": args.outer_head,
        "nested_head": args.nested_head,
        "probe_path": str(args.probe),
        "probe_size": len(raw),
        "probe_sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": len(EXPECTED_CASES),
        "auto_records_replayed": sum(
            1 for case in payload["cases"].values()
            if isinstance(case, dict) and isinstance(case.get("record"), dict)
        ),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, VerificationError, ValueError, KeyError) as error:
        print(f"checkpoint-b verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)

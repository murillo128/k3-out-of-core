from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase8"))

from evaluate_auto_cost import evaluate_auto  # noqa: E402
from verify_checkpoint_b import (  # noqa: E402
    EXPECTED_CASES,
    VerificationError,
    no_duplicate_object,
    verify_repository_binding,
    verify_payload,
)

OUTER = "a" * 40
NESTED = "b" * 40


def cost(*, gpu: bool = False) -> dict[str, int]:
    value = {
        "version": 1, "struct_size": 96,
        "cpu_fixed_decode_ns": 10, "cpu_fixed_prefill_ns": 20,
        "cpu_per_lane_decode_ns": 2, "cpu_per_lane_prefill_ns": 4,
        "gpu_fixed_decode_ns": 30, "gpu_fixed_prefill_ns": 40,
        "gpu_per_lane_decode_ns": 3, "gpu_per_lane_prefill_ns": 5,
        "h2d_fixed_ns": 7, "h2d_bytes_per_second": 1_000_000_000,
        "decision_hysteresis_ns": 1,
    }
    if gpu:
        value.update(cpu_fixed_decode_ns=10_000, cpu_per_lane_decode_ns=10_000)
    return value


def record(state: str = "NONE", *, gpu: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "request": 1, "layer": 0, "expert": 0, "cost": cost(gpu=gpu),
        "prefill": False, "lanes": 2, "bundle_bytes": 96,
        "queued_cpu_work_ns": 0, "queued_h2d_work_ns": 0, "queued_gpu_work_ns": 0,
        "same_key_h2d_present": state != "NONE", "same_key_h2d_state": state,
        "same_key_h2d_remaining_bytes": (
            96 if state in {"QUEUED_OR_STAGING", "H2D_IN_FLIGHT"} else 0),
    }
    value["result"] = evaluate_auto(value)
    return value


def closeout(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "written": True, "write_count": 1, "final_invariants_ok": True,
        "scheduler_active": 0, "scheduler_queued": 0,
        "scheduler_terminal_complete": 0, "scheduler_terminal_failed": 0,
        "scheduler_terminal_cancelled": 0, "scheduler_terminal_releases": 0,
        "ring_queued_workers": 0, "ring_running_workers": 0,
        "ring_non_free_lanes": 0, "ring_live_events": 0,
        "cold_hot_refs": 0, "cold_transfer_refs": 0, "cold_request_refs": 0,
        "cold_cpu_execution_refs": 0, "hot_pins": 0,
        "published_forward_mappings": 0,
    }
    value.update(updates)
    return value


def fixture() -> dict[str, object]:
    cases: dict[str, dict[str, object]] = {
        name: {"status": "pass"} for name in EXPECTED_CASES
    }
    states = {
        "auto_operands_none": "NONE",
        "auto_operands_queued": "QUEUED_OR_STAGING",
        "auto_operands_in_flight": "H2D_IN_FLIGHT",
        "auto_operands_complete_unpublished": "H2D_COMPLETE_UNPUBLISHED",
    }
    for name, state in states.items():
        cases[name].update(decision_digest_entries=1, record=record(state))

    for name in {"frozen_gpu_scheduler_join_mismatch", "frozen_gpu_wait_failed",
                 "frozen_gpu_publication_failed"}:
        cases[name].update(
            request_status="failed", decision_count=1, backend="gpu",
            new_cpu_execution_refs=0, active_cpu_lanes=0, written_cpu_ids=0,
            evaluator_invocations=1, backend_switches=0, record=record(gpu=True),
            closeout=closeout(scheduler_terminal_failed=1, scheduler_terminal_releases=1))

    for name in {"normalization_failed", "normalization_stale",
                 "prepublication_h2d_failure", "prepublication_metadata_invalid"}:
        cases[name].update(
            terminalized_before_evaluation=True, same_key_h2d_present=False,
            scheduler_complete_delta=0, scheduler_failed_or_cancelled_delta=1,
            background_drop_or_fail_delta=1, useful_delta=0, wasted_delta=0,
            record=record(),
            closeout=closeout(scheduler_terminal_failed=1, scheduler_terminal_releases=1))

    for name in {"prepublication_unload_queued", "prepublication_unload_in_flight",
                 "prepublication_unload_complete_unpublished"}:
        cases[name].update(
            scheduler_success_delta=0, terminal="cancelled",
            background_drop_or_fail_delta=1, useful_delta=0, wasted_delta=0,
            closeout=closeout(scheduler_terminal_cancelled=1, scheduler_terminal_releases=1))

    cases["current_output_nonblocking"].update(
        current_remap_returned_while_gate_closed=True, later_join_same_scheduler=True,
        later_join_same_lane=True, later_join_same_hot_generation=True,
        record=record("QUEUED_OR_STAGING"),
        closeout=closeout(scheduler_terminal_complete=1, scheduler_terminal_releases=1))
    cases["published_wasted"].update(
        useful_delta=0, wasted_delta=1, counted_once=True,
        closeout=closeout(scheduler_terminal_complete=1, scheduler_terminal_releases=1))
    cases["published_useful"].update(
        useful_delta=1, wasted_delta=0, counted_once=True,
        closeout=closeout(scheduler_terminal_complete=1, scheduler_terminal_releases=1))

    for name in [name for name in EXPECTED_CASES if name.startswith("model_")]:
        kind = "mxfp4" if "mxfp4" in name else "f16"
        cases[name]["model_path"] = f"models/{kind}.gguf"
        if name.endswith("smoke"):
            cases[name].update(decode_status=0, cpu_execution_lanes_positive=True,
                               route_key={"layer": 1, "expert": 2})
        else:
            cases[name].update(gate_reached_before_model_reset=True,
                               useful_delta=0, wasted_delta=0)
        cases[name]["closeout"] = closeout()

    return {
        "schema": "phase8-checkpoint-b-probe-v1", "outer_head": OUTER,
        "nested_head": NESTED, "exit_code": 0, "cwd": "/tmp",
        "command": [
            "/tmp/phase8-checkpoint-b-probe", "--output", "/tmp/probe.json",
            "--outer-head", OUTER, "--nested-head", NESTED,
            "--f16", "models/f16.gguf", "--mxfp4", "models/mxfp4.gguf"],
        "models": {"f16": {"path": "models/f16.gguf"},
                   "mxfp4": {"path": "models/mxfp4.gguf"}},
        "cases": cases,
    }


class VerifyCheckpointBTests(unittest.TestCase):
    def assert_rejected(self, mutate) -> None:
        payload = fixture()
        mutate(payload)
        with self.assertRaises((VerificationError, ValueError, KeyError)):
            verify_payload(payload, expected_outer=OUTER, expected_nested=NESTED)

    def test_accepts_complete_bound_fixture(self) -> None:
        verify_payload(fixture(), expected_outer=OUTER, expected_nested=NESTED)

    def test_rejects_missing_state(self) -> None:
        self.assert_rejected(lambda value: value["cases"].pop("auto_operands_in_flight"))

    def test_rejects_altered_backend(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["frozen_gpu_wait_failed"]
                             ["record"]["result"].update(backend="cpu"))

    def test_rejects_cpu_ref_after_frozen_gpu_decision(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["frozen_gpu_wait_failed"].update(
            new_cpu_execution_refs=1))

    def test_rejects_scheduler_success_before_publication(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["prepublication_h2d_failure"]
                             ["closeout"].update(scheduler_terminal_complete=1))

    def test_rejects_dropped_and_wasted_overlap(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["normalization_stale"].update(
            wasted_delta=1))

    def test_rejects_nonzero_final_reference(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["model_f16_destroy_in_flight"]
                             ["closeout"].update(cold_transfer_refs=1))

    def test_rejects_stale_head(self) -> None:
        self.assert_rejected(lambda value: value.update(outer_head="c" * 40))

    def test_rejects_stale_gitlink(self) -> None:
        with self.assertRaises(VerificationError):
            verify_repository_binding(
                actual_outer=OUTER, actual_nested=NESTED, gitlink="c" * 40,
                expected_outer=OUTER, expected_nested=NESTED)

    def test_rejects_duplicate_authority_key(self) -> None:
        with self.assertRaises(VerificationError):
            json.loads('{"outer_head":"a","outer_head":"b"}',
                       object_pairs_hook=no_duplicate_object)


if __name__ == "__main__":
    unittest.main()

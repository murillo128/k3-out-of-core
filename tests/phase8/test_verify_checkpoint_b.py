from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase8"))

from evaluate_auto_cost import evaluate_auto  # noqa: E402
from verify_checkpoint_b import (  # noqa: E402
    EXPECTED_CASES,
    VerificationError,
    verify_checkpoint_b,
    verify_checkpoint_b_file,
)

OUTER = "a" * 40
NESTED = "b" * 40
BACKGROUND_FIELDS = ("submitted", "published_completed", "dropped", "useful", "wasted", "busy")
SCHEDULER_FIELDS = ("terminal_complete", "terminal_failed", "terminal_cancelled", "terminal_releases")
LIFECYCLE_FIELDS = (
    "empty", "queued_or_staging", "h2d_in_flight", "h2d_complete_unpublished",
    "published", "failed", "cancelled",
)


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


def zero_delta() -> dict[str, dict[str, int]]:
    return {
        "background": {field: 0 for field in BACKGROUND_FIELDS},
        "scheduler": {field: 0 for field in SCHEDULER_FIELDS},
    }


def failed_delta(terminal: str = "failed") -> dict[str, dict[str, int]]:
    value = zero_delta()
    value["background"].update(submitted=1, dropped=1)
    value["scheduler"][f"terminal_{terminal}"] = 1
    value["scheduler"]["terminal_releases"] = 1
    return value


def published_delta(*, useful: int, wasted: int) -> dict[str, dict[str, int]]:
    value = zero_delta()
    value["background"].update(
        submitted=1, published_completed=1, useful=useful, wasted=wasted)
    value["scheduler"].update(terminal_complete=1, terminal_releases=1)
    return value


def evidence(delta: dict[str, dict[str, int]]) -> dict[str, object]:
    baseline = {
        "background": {
            "submitted": 10, "published_completed": 7, "dropped": 3,
            "useful": 5, "wasted": 2, "busy": 4,
        },
        "scheduler": {
            "terminal_complete": 6, "terminal_failed": 3,
            "terminal_cancelled": 1, "terminal_releases": 10,
        },
    }
    closeout_background = {
        field: baseline["background"][field] + delta["background"][field]
        for field in BACKGROUND_FIELDS
    }
    closeout_background["lifecycle"] = {field: 0 for field in LIFECYCLE_FIELDS}
    closeout_scheduler = {
        field: baseline["scheduler"][field] + delta["scheduler"][field]
        for field in SCHEDULER_FIELDS
    }
    closeout_scheduler.update(active=0, queued=0)
    return {
        "baseline": baseline,
        "closeout": {
            "written": True, "write_count": 1, "final_invariants_ok": True,
            "background": closeout_background,
            "scheduler": closeout_scheduler,
            "ring": {"queued_workers": 0, "running_workers": 0,
                     "non_free_lanes": 0, "live_events": 0},
            "cold": {"hot_refs": 0, "transfer_refs": 0,
                     "request_refs": 0, "cpu_execution_refs": 0},
            "hot_pins": 0, "published_forward_mappings": 0,
        },
        "observed_delta": copy.deepcopy(delta),
    }


def fixture() -> dict[str, object]:
    cases: dict[str, dict[str, object]] = {
        name: {"status": "pass", **evidence(zero_delta())} for name in EXPECTED_CASES
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
            evidence(failed_delta()), terminal="failed", request_status="failed",
            decision_count=1, backend="gpu", new_cpu_execution_refs=0,
            active_cpu_lanes=0, written_cpu_ids=0, evaluator_invocations=1,
            backend_switches=0, record=record(gpu=True))

    for name in {"normalization_failed", "normalization_stale",
                 "prepublication_h2d_failure", "prepublication_metadata_invalid"}:
        cases[name].update(
            evidence(failed_delta()), terminal="failed",
            terminalized_before_evaluation=True, same_key_h2d_present=False,
            record=record())

    for name in {"prepublication_unload_queued", "prepublication_unload_in_flight",
                 "prepublication_unload_complete_unpublished"}:
        cases[name].update(evidence(failed_delta("cancelled")), terminal="cancelled")

    cases["current_output_nonblocking"].update(
        evidence(published_delta(useful=1, wasted=0)),
        current_remap_returned_while_gate_closed=True, later_join_same_scheduler=True,
        later_join_same_lane=True, later_join_same_hot_generation=True,
        record=record("QUEUED_OR_STAGING"), useful_delta=1, wasted_delta=0)
    cases["published_wasted"].update(
        evidence(published_delta(useful=0, wasted=1)), useful_delta=0, wasted_delta=1)
    cases["published_useful"].update(
        evidence(published_delta(useful=1, wasted=0)), useful_delta=1, wasted_delta=0)

    for name in [name for name in EXPECTED_CASES if name.startswith("model_")]:
        kind = "mxfp4" if "mxfp4" in name else "f16"
        cases[name]["model_path"] = f"models/{kind}.gguf"
        if name.endswith("smoke"):
            cases[name].update(decode_status=0, cpu_execution_lanes_positive=True,
                               route_key={"layer": 1, "expert": 2})
        else:
            cases[name].update(
                evidence(failed_delta()), gate_reached_before_model_reset=True,
                terminal="failed")

    return {
        "schema": "phase8-checkpoint-b-probe-v2", "outer_head": OUTER,
        "nested_head": NESTED, "exit_code": 0, "cwd": "/tmp",
        "command": [
            "/tmp/phase8-checkpoint-b-probe", "--output", "/tmp/probe.json",
            "--outer-head", OUTER, "--nested-head", NESTED,
            "--f16", "models/f16.gguf", "--mxfp4", "models/mxfp4.gguf"],
        "models": {"f16": {"path": "models/f16.gguf"},
                   "mxfp4": {"path": "models/mxfp4.gguf"}},
        "cases": cases,
    }


def set_delta(case: dict[str, object], group: str, field: str, value: int) -> None:
    case["observed_delta"][group][field] = value
    case["closeout"][group][field] = case["baseline"][group][field] + value


class VerifyCheckpointBTests(unittest.TestCase):
    def verify(self, payload: dict[str, object], *, gitlink: str = NESTED) -> None:
        verify_checkpoint_b(
            payload,
            expected_outer=OUTER,
            expected_nested=NESTED,
            actual_outer=OUTER,
            actual_nested=NESTED,
            gitlink=gitlink,
        )

    def assert_rejected(self, mutate) -> None:
        payload = fixture()
        mutate(payload)
        with self.assertRaises((VerificationError, ValueError, KeyError, TypeError)):
            self.verify(payload)

    def test_accepts_complete_bound_fixture(self) -> None:
        self.verify(fixture())

    def test_rejects_v1_schema(self) -> None:
        self.assert_rejected(lambda value: value.update(schema="phase8-checkpoint-b-probe-v1"))

    def test_rejects_missing_or_extra_closeout_field(self) -> None:
        for mutation in (
            lambda value: value["cases"]["model_f16_smoke"]["closeout"].pop("hot_pins"),
            lambda value: value["cases"]["model_f16_smoke"]["closeout"].update(extra=0),
        ):
            with self.subTest(mutation=mutation):
                self.assert_rejected(mutation)

    def test_rejects_omitted_lifecycle_field(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["model_f16_smoke"]["closeout"]
                             ["background"]["lifecycle"].pop("failed"))

    def test_rejects_terminal_contradiction(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["frozen_gpu_wait_failed"].update(
            terminal="cancelled", request_status="cancelled"))

    def test_rejects_erased_terminal_counts(self) -> None:
        mutations = (
            ("frozen_gpu_wait_failed", "terminal_failed"),
            ("prepublication_unload_queued", "terminal_cancelled"),
            ("current_output_nonblocking", "terminal_complete"),
        )
        for case_name, field in mutations:
            with self.subTest(case=case_name, field=field):
                self.assert_rejected(lambda value, n=case_name, f=field:
                                     set_delta(value["cases"][n], "scheduler", f, 0))

    def test_rejects_terminal_release_imbalance(self) -> None:
        self.assert_rejected(lambda value: set_delta(
            value["cases"]["frozen_gpu_wait_failed"], "scheduler", "terminal_releases", 0))

    def test_rejects_background_submission_imbalance(self) -> None:
        self.assert_rejected(lambda value: set_delta(
            value["cases"]["normalization_failed"], "background", "submitted", 0))

    def test_rejects_dropped_and_wasted_overlap(self) -> None:
        self.assert_rejected(lambda value: set_delta(
            value["cases"]["normalization_stale"], "background", "wasted", 1))

    def test_rejects_erased_generation_classification(self) -> None:
        for case_name, field in (("published_useful", "useful"), ("published_wasted", "wasted")):
            with self.subTest(case=case_name, field=field):
                self.assert_rejected(lambda value, n=case_name, f=field:
                                     set_delta(value["cases"][n], "background", f, 0))

    def test_rejects_nonzero_final_state(self) -> None:
        paths = (
            ("scheduler", "active"), ("scheduler", "queued"),
            ("ring", "queued_workers"),
            ("ring", "running_workers"), ("ring", "non_free_lanes"),
            ("ring", "live_events"), ("cold", "hot_refs"),
            ("cold", "transfer_refs"), ("cold", "request_refs"),
            ("cold", "cpu_execution_refs"), (None, "hot_pins"),
            (None, "published_forward_mappings"),
        )
        for group, field in paths:
            with self.subTest(group=group, field=field):
                def mutate(value, g=group, f=field):
                    closeout = value["cases"]["model_f16_destroy_in_flight"]["closeout"]
                    if g is None:
                        closeout[f] = 1
                    else:
                        closeout[g][f] = 1
                self.assert_rejected(mutate)

    def test_rejects_nonzero_background_lifecycle(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["model_f16_destroy_in_flight"]
                             ["closeout"]["background"]["lifecycle"].update(failed=1))

    def test_rejects_invalid_closeout_flags(self) -> None:
        for field, value in (("written", False), ("write_count", 2),
                             ("final_invariants_ok", False)):
            with self.subTest(field=field):
                self.assert_rejected(lambda payload, f=field, v=value:
                                     payload["cases"]["model_f16_smoke"]["closeout"].update({f: v}))

    def test_rejects_missing_case(self) -> None:
        self.assert_rejected(lambda value: value["cases"].pop("auto_operands_in_flight"))

    def test_rejects_altered_backend(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["frozen_gpu_wait_failed"]
                             ["record"]["result"].update(backend="cpu"))

    def test_rejects_cpu_ref_after_frozen_gpu_decision(self) -> None:
        self.assert_rejected(lambda value: value["cases"]["frozen_gpu_wait_failed"].update(
            new_cpu_execution_refs=1))

    def test_rejects_stale_head(self) -> None:
        self.assert_rejected(lambda value: value.update(outer_head="c" * 40))

    def test_rejects_stale_gitlink(self) -> None:
        with self.assertRaises(VerificationError):
            self.verify(fixture(), gitlink="c" * 40)

    def test_rejects_delta_underflow_noninteger_negative_and_mismatch(self) -> None:
        mutations = (
            lambda value: value["cases"]["model_f16_smoke"]["closeout"]
            ["background"].update(submitted=9),
            lambda value: value["cases"]["model_f16_smoke"]["observed_delta"]
            ["background"].update(submitted=1.0),
            lambda value: value["cases"]["model_f16_smoke"]["observed_delta"]
            ["background"].update(submitted=-1),
            lambda value: value["cases"]["model_f16_smoke"]["observed_delta"]
            ["background"].update(submitted=1),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.assert_rejected(mutation)

    def test_rejects_duplicate_authority_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"outer_head":"a","outer_head":"b"}')
            with self.assertRaises(VerificationError):
                verify_checkpoint_b_file(
                    path,
                    expected_outer=OUTER,
                    expected_nested=NESTED,
                    actual_outer=OUTER,
                    actual_nested=NESTED,
                    gitlink=NESTED,
                )


if __name__ == "__main__":
    unittest.main()

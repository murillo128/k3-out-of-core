from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase10"))

from combine_transport_break_even import combine
from capture_offline_replay import score_replay
from capture_profile_compatibility import frozen_tuning_digest, retained_tunings
from capture_transport_measurements import validate_measurement
from measure_transport_break_even import derive
from prefetch_common import (FNV_OFFSET, Phase10Error, break_even, config_digest, cross_candidates, fold_membership,
    load_json, predictor_candidates, splitmix64, validate_profile, write_json)
from replay_prefetch import replay


HASH = "a" * 64
COMMIT = "a" * 40


def profile() -> dict:
    package_text = f"0:10:model.gguf:1024:{HASH}\n{HASH}\n".encode()
    package_hash = hashlib.sha256(package_text).hexdigest()
    byte_map = [{"layer": layer, "expert": expert, "payload_bytes": 128, "physical_bytes": 160}
        for layer in (1, 2) for expert in range(4)]
    return {"schema_version": "expert-prefetch-profile-v1", "profile_id": "test-profile",
        "tool": {"name": "test", "version": 1},
        "source": {"kind": "route_trace", "artifacts": [{"name": "trace.bin", "size": 64, "sha256": HASH}],
            "fold": {"index": 0, "training": ["structured-en-small", "technical-en-large", "narrative-en-large", "narrative-es-large"],
                "validation": "code-en-small", "test": "prose-en-small", "training_rows": 40, "validation_rows": 10, "test_rows": 10}},
        "target": {"package_sha256": package_hash, "files": [{"ordinal": 0, "name": "model.gguf", "size": 1024, "sha256": HASH}],
            "layer_count": 3, "routed_layers": [1, 2], "experts_per_layer": 4, "experts_per_token": 2,
            "tensor_layout_sha256": HASH, "expert_bytes": byte_map},
        "static_counts": [{"layer": 1, "expert": 2, "count": 9}, {"layer": 1, "expert": 1, "count": 9},
            {"layer": 1, "expert": 0, "count": 2}, {"layer": 2, "expert": 3, "count": 7}],
        "transitions": [{"source_layer": 1, "source_expert": 0, "target_layer": 2, "target_expert": 3, "count": 5},
            {"source_layer": 1, "source_expert": 1, "target_layer": 2, "target_expert": 2, "count": 4},
            {"source_layer": 1, "source_expert": 1, "target_layer": 2, "target_expert": 3, "count": 1}],
        "costs": [{"transport": "BUFFERED", "readiness": "DEVICE_READY", "lead_ns": 100, "demand_service_ns": 80,
            "speculative_service_ns": 20, "predictor_compute_ns": 10, "scheduler_demand_delay_ns": 5,
            "displacement_refill_ns": 5, "storage_bytes": 128, "h2d_bytes": 128, "break_even_bps": 3637,
            "utility_window_predictions": 8, "utility_min_observations": 4, "utility_min_timely_successes": 3},
            {"transport": "BUFFERED", "readiness": "HOST_READY", "lead_ns": 100, "demand_service_ns": 80,
            "speculative_service_ns": 20, "predictor_compute_ns": 10, "scheduler_demand_delay_ns": 5,
            "displacement_refill_ns": 5, "storage_bytes": 128, "h2d_bytes": 0, "break_even_bps": 3637,
            "utility_window_predictions": 8, "utility_min_observations": 4, "utility_min_timely_successes": 3}],
        "selection": {"matrix_version": 1, "tuning_digest": HASH, "fold_index": 0, "candidates_per_target": 2,
            "policy": "TEMPORAL_FREQUENCY", "temporal_window_tokens": 4, "transport": "BUFFERED", "readiness": "DEVICE_READY", "break_even_bps": 3637},
        "seed": [{"layer": 1, "expert": 1, "count": 9, "payload_bytes": 128, "physical_bytes": 160}]}


def limits(active: bool = True) -> dict:
    value = {"cold_capacity_bytes": 1280, "hot_capacity_slots": 8,
        "max_speculative_flights": 8, "max_speculative_storage_bytes_in_flight": 1280,
        "max_speculative_h2d_bytes_in_flight": 1024, "max_speculative_storage_bytes_per_token": 1280,
        "max_speculative_h2d_bytes_per_token": 1024, "max_speculative_cold_slots": 8,
        "max_speculative_hot_slots": 8}
    if not active:
        for name in list(value):
            if name.startswith("max_speculative"):
                value[name] = 0
    return value


def predictor_upper_bound() -> dict:
    policies = ["STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY",
        "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"]
    return {"basis": "maximum p95 over full-token topology-capped declared predictors", "upper_bound_p95_ns": 10,
        "measurements": [{"policy": policy, "temporal_window_tokens": 64 if policy == "TEMPORAL_FREQUENCY" else 0,
            "candidates_per_target": 4, "target_layers_per_call": 2, "repetitions": 1000,
            "p50_ns": 8, "p95_ns": 10, "p99_ns": 12} for policy in policies]}


def lead_measurements() -> dict:
    return {"basis": "minimum p50 of observed token-end-to-router and adjacent-router intervals",
        "decode_submissions": 5, "token_end_samples": 35, "cross_layer_samples": 30,
        "token_end_p50_ns": 100, "cross_layer_p50_ns": 100, "conservative_lead_p50_ns": 100,
        "provider_event_capacity": 256, "provider_events_dropped": 0}


class PrefetchProfileTests(unittest.TestCase):
    def test_folds_are_causal_and_cover_all_prompts(self) -> None:
        for index in range(6):
            fold = fold_membership(index)
            self.assertEqual(len(fold["training"]), 4)
            self.assertEqual(len(set(fold["training"] + [fold["validation"], fold["test"]])), 6)

    def test_profile_freeze_excludes_nonprofile_controls_and_rejections(self) -> None:
        common = {"artifact": "f16", "fold": 0, "transport": "BUFFERED", "readiness": "DEVICE_READY",
            "temporal_window_tokens": 0, "candidates_per_target": 0}
        offline = {"shortlist": [
            {**common, "policy": "DEMAND_BASELINE", "disposition": "demand_baseline_control"},
            {**common, "policy": "SERIAL_CONTROL", "disposition": "serial_control_only"},
            {**common, "policy": "BLOCKING_HOT", "disposition": "retained_for_online_seed_evaluation"},
            {**common, "policy": "TEMPORAL_FREQUENCY", "temporal_window_tokens": 4,
                "candidates_per_target": 2, "disposition": "rejected_below_break_even"},
        ]}
        self.assertEqual([item["policy"] for item in retained_tunings(offline)], ["BLOCKING_HOT"])

    def test_profile_freeze_digest_excludes_held_out_test(self) -> None:
        offline = {"matrix_sha256": HASH}
        tuning = {"policy": "TEMPORAL_FREQUENCY", "precision_bps": 5000,
            "held_out_test": {"precision_bps": 1000}}
        digest = frozen_tuning_digest(offline, tuning)
        changed_test = {**tuning, "held_out_test": {"precision_bps": 9000}}
        changed_validation = {**tuning, "precision_bps": 5001}
        self.assertEqual(digest, frozen_tuning_digest(offline, changed_test))
        self.assertNotEqual(digest, frozen_tuning_digest(offline, changed_validation))

    def test_break_even_uses_project_costs(self) -> None:
        result = break_even({"lead_ns": 100, "demand_service_ns": 80, "predictor_compute_ns": 10,
            "speculative_service_ns": 20, "scheduler_demand_delay_ns": 5, "displacement_refill_ns": 5})
        self.assertEqual(result, {"hidden_benefit_ns": 70, "waste_cost_ns": 40, "break_even_bps": 3637})
        with self.assertRaisesRegex(Phase10Error, "ineligible"):
            break_even({"lead_ns": 10, "demand_service_ns": 20, "predictor_compute_ns": 10,
                "speculative_service_ns": 1, "scheduler_demand_delay_ns": 1, "displacement_refill_ns": 1})

    def test_measurement_envelope_and_waste_nontransfer(self) -> None:
        result = derive({"schema_version": "phase10-transport-measurements-v1", "project_head": COMMIT, "nested_head": COMMIT,
            "host": "test", "profile_sha256": HASH, "profile_parse_ns": 10, "model_profile_load_ns": 20,
            "lead_measurements": lead_measurements(),
            "predictor_upper_bound": predictor_upper_bound(),
            "envelopes": [{"transport": "BUFFERED", "readiness": "DEVICE_READY", "supported": True,
                "lead_p50_ns": 100, "demand_service_p50_ns": 80, "speculative_service_p95_ns": 20,
                "predictor_compute_p95_ns": 10, "scheduler_demand_delay_p95_ns": 5, "displacement_refill_p95_ns": 5,
                "storage_bytes": 128, "h2d_bytes": 128, "utility_window_predictions": 8, "utility_min_observations": 4}]})
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["waste_external_threshold_transferred"])
        self.assertEqual(result["envelopes"][0]["break_even_bps"], 3637)

    def test_transport_matrix_requires_matching_identities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = derive({"schema_version": "phase10-transport-measurements-v1", "project_head": COMMIT,
                "nested_head": COMMIT, "host": "test", "profile_sha256": HASH, "profile_parse_ns": 10,
                "model_profile_load_ns": 20, "lead_measurements": lead_measurements(),
                "predictor_upper_bound": predictor_upper_bound(),
                "envelopes": [{"transport": "BUFFERED", "readiness": "DEVICE_READY",
                    "supported": True, "lead_p50_ns": 100, "demand_service_p50_ns": 80,
                    "speculative_service_p95_ns": 20, "predictor_compute_p95_ns": 10,
                    "scheduler_demand_delay_p95_ns": 5, "displacement_refill_p95_ns": 5,
                    "storage_bytes": 128, "h2d_bytes": 128, "utility_window_predictions": 8,
                    "utility_min_observations": 4}]})
            f16 = Path(directory) / "f16.json"
            mxfp4 = Path(directory) / "mxfp4.json"
            write_json(f16, first)
            write_json(mxfp4, first)
            self.assertEqual(combine(str(f16), str(mxfp4))["status"], "pass")
            first["nested_head"] = "b" * 40
            write_json(mxfp4, first)
            with self.assertRaisesRegex(Phase10Error, "identities differ"):
                combine(str(f16), str(mxfp4))

    def test_calibration_measurement_is_revision_and_profile_bound(self) -> None:
        document = {"schema_version": "phase10-transport-measurements-v1", "project_head": COMMIT,
            "nested_head": COMMIT, "profile_sha256": HASH}
        validate_measurement(document, COMMIT, COMMIT, HASH)
        document["profile_sha256"] = "b" * 64
        with self.assertRaisesRegex(Phase10Error, "different profile"):
            validate_measurement(document, COMMIT, COMMIT, HASH)

    def test_duplicate_keys_and_float_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.json"
            duplicate.write_text('{"a":1,"a":2}\n', encoding="utf-8")
            with self.assertRaisesRegex(Phase10Error, "duplicate"):
                load_json(duplicate)
            floating = Path(directory) / "float.json"
            floating.write_text('{"a":1.5}\n', encoding="utf-8")
            with self.assertRaisesRegex(Phase10Error, "float"):
                load_json(floating)

    def test_profile_validation_rejects_leakage_and_duplicates(self) -> None:
        document = profile()
        validate_profile(document)
        document["selection"].update({"policy": "BLOCKING_HOT", "candidates_per_target": 0,
            "temporal_window_tokens": 0})
        validate_profile(document)
        document["selection"]["policy"] = "STATIC_LAYER"
        with self.assertRaisesRegex(Phase10Error, "selected candidates"):
            validate_profile(document)
        document = profile()
        document["source"]["fold"]["training"][0] = document["source"]["fold"]["test"]
        with self.assertRaisesRegex(Phase10Error, "fold"):
            validate_profile(document)
        document = profile()
        document["transitions"].append(document["transitions"][0].copy())
        with self.assertRaisesRegex(Phase10Error, "duplicate"):
            validate_profile(document)
        document = profile()
        document["selection"]["break_even_bps"] -= 1
        with self.assertRaisesRegex(Phase10Error, "selected cost"):
            validate_profile(document)

    def test_predictors_are_deterministic_and_causal(self) -> None:
        document = profile()
        history = [[[0, 1], [2, 3]], [[1, 2], [0, 3]]]
        self.assertEqual(predictor_candidates(document, "STATIC_LAYER", history, 1, 2, 1, 7, 1), [(1, 9), (2, 9)])
        self.assertEqual(predictor_candidates(document, "PREVIOUS_TOKEN", history[-1:], 1, 2, 1, 7, 1), [(1, 1), (2, 1)])
        self.assertEqual(predictor_candidates(document, "TEMPORAL_FREQUENCY", history, 1, 2, 1, 7, 1), [(1, 258), (2, 130)])
        self.assertEqual(cross_candidates(document, 1, [0, 1], 2, 2), [(3, 6), (2, 4)])
        first = predictor_candidates(document, "RANDOM_BASELINE", history[-1:], 1, 2, 1234, 7, 1)
        second = predictor_candidates(document, "RANDOM_BASELINE", history[-1:], 1, 2, 1234, 7, 1)
        self.assertEqual(first, second)

    def test_disabled_replay_preserves_phase9_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            write_json(profile_path, profile())
            events = [{"token": 0, "layers": [{"layer": 1, "experts": [0, 1]}, {"layer": 2, "experts": [2, 3]}]}]
            request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path), "policy": "OFF",
                "transport": "BUFFERED", "readiness": "DEVICE_READY", "temporal_window_tokens": 0,
                "candidates_per_target": 0,
                "request_ordinal": 1, "events": events, "completion_order": [], "limits": limits(False),
                "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}
            output = replay(request)
            self.assertEqual(output["candidate_stream"], [])
            self.assertEqual(output["state_digest"], FNV_OFFSET)

    def test_hierarchy_replay_protects_demand_and_resolves_deadlines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            write_json(profile_path, profile())
            events = [
                {"token": 0, "layers": [{"layer": 1, "experts": [0, 1]}, {"layer": 2, "experts": [2, 3]}]},
                {"token": 1, "layers": [{"layer": 1, "experts": [1, 2]}, {"layer": 2, "experts": [0, 3]}]},
            ]
            request = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
                "policy": "STATIC_LAYER", "transport": "BUFFERED", "readiness": "DEVICE_READY",
                "temporal_window_tokens": 0,
                "candidates_per_target": 2, "request_ordinal": 1, "events": events,
                "completion_order": [], "limits": limits(), "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}
            output = replay(request)
            self.assertEqual(output["summary"]["predictions"], 6)
            self.assertGreater(output["summary"]["rejected"], 0)
            self.assertGreater(output["summary"]["timely_useful"], 0)
            self.assertTrue(all(item["origin"] == "DEMAND" for item in output["resident"]))

    def test_native_hierarchy_replay_agrees(self) -> None:
        native = os.environ.get("PHASE10_NATIVE_REPLAY")
        if not native:
            self.skipTest("PHASE10_NATIVE_REPLAY is not set")
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            request_path = Path(directory) / "request.json"
            write_json(profile_path, profile())
            base = {"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
                "transport": "BUFFERED",
                "request_ordinal": 1, "events": [
                    {"token": 0, "layers": [{"layer": 1, "experts": [0, 1]}, {"layer": 2, "experts": [2, 3]}]},
                    {"token": 1, "layers": [{"layer": 1, "experts": [1, 2]}, {"layer": 2, "experts": [0, 3]}]},
                ], "completion_order": []}
            cases = [
                ("static-device", {"policy": "STATIC_LAYER", "readiness": "DEVICE_READY", "candidates_per_target": 2,
                    "limits": limits(), "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}),
                ("static-host", {"policy": "STATIC_LAYER", "readiness": "HOST_READY", "candidates_per_target": 2,
                    "limits": limits(), "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}),
                ("baseline", {"policy": "OFF", "readiness": "DEVICE_READY", "candidates_per_target": 0,
                    "limits": limits(False), "seed_mode": "OFF", "demand_mode": "ISSUE_AHEAD"}),
                ("serial", {"policy": "OFF", "readiness": "DEVICE_READY", "candidates_per_target": 0,
                    "limits": limits(False), "seed_mode": "OFF", "demand_mode": "SERIAL"}),
                ("seed", {"policy": "OFF", "readiness": "DEVICE_READY", "candidates_per_target": 0,
                    "limits": limits(False), "seed_mode": "BLOCKING_HOT", "demand_mode": "ISSUE_AHEAD"}),
            ]
            for name, values in cases:
                request = {**base, **values, "temporal_window_tokens": 0}
                write_json(request_path, request)
                completed = subprocess.run([native, str(request_path)], check=False, capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, f"{name}: {completed.stderr}")
                native_output = json.loads(completed.stdout)
                python_output = replay(request)
                self.assertEqual(set(native_output), set(python_output))
                for key in sorted(native_output):
                    with self.subTest(case=name, field=key):
                        self.assertEqual(native_output[key], python_output[key])
            late_profile = profile()
            late_cost = late_profile["costs"][0]
            late_cost.update({"speculative_service_ns": 100, "break_even_bps": 6316,
                "utility_min_timely_successes": 6})
            late_profile["selection"]["break_even_bps"] = 6316
            write_json(profile_path, late_profile)
            late_request = {**base, "policy": "STATIC_LAYER", "readiness": "DEVICE_READY",
                "candidates_per_target": 2, "limits": limits(), "seed_mode": "OFF",
                "demand_mode": "ISSUE_AHEAD", "temporal_window_tokens": 0,
                "events": [
                    {"token": 0, "layers": [{"layer": 1, "experts": [0, 3]},
                        {"layer": 2, "experts": [0, 1]}]},
                    {"token": 1, "layers": [{"layer": 1, "experts": [1, 3]},
                        {"layer": 2, "experts": [0, 1]}]},
                ]}
            write_json(request_path, late_request)
            completed = subprocess.run([native, str(request_path)], check=False, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            native_output = json.loads(completed.stdout)
            python_output = replay(late_request)
            self.assertEqual(native_output, python_output)
            self.assertGreater(native_output["summary"]["late_joined"], 0)
            self.assertGreater(native_output["summary"]["cancelled_drained"], 0)

    def test_offline_scoring_uses_causal_deadlines_and_physical_budget(self) -> None:
        events = [
            {"token": 0, "layers": [{"layer": 1, "experts": [0, 1]}, {"layer": 2, "experts": [2, 3]}]},
            {"token": 1, "layers": [{"layer": 1, "experts": [1, 2]}, {"layer": 2, "experts": [0, 3]}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            profile_path = Path(directory) / "profile.json"
            write_json(profile_path, profile())
            output = replay({"schema_version": "phase10-prefetch-replay-v1", "profile_path": str(profile_path),
                "policy": "STATIC_LAYER", "transport": "BUFFERED", "readiness": "DEVICE_READY",
                "temporal_window_tokens": 0,
                "candidates_per_target": 2, "request_ordinal": 1, "events": events,
                "completion_order": [], "limits": limits(), "seed_mode": "OFF",
                "demand_mode": "ISSUE_AHEAD"})
            metrics = score_replay(output, 0, events)
            self.assertEqual(metrics["actual_demands"], 8)
            self.assertEqual(metrics["predictions"], output["summary"]["accepted"])
            self.assertEqual(metrics["timely_successes"], output["summary"]["timely_useful"])
            self.assertLessEqual(metrics["predicted_physical_bytes"], 2*limits()["max_speculative_storage_bytes_per_token"])


if __name__ == "__main__":
    unittest.main()

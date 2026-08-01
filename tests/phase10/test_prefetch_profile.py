from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase10"))

from measure_transport_break_even import derive
from capture_offline_replay import score_candidates
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
            "utility_window_predictions": 8, "utility_min_observations": 4, "utility_min_timely_successes": 3}],
        "selection": {"matrix_version": 1, "tuning_digest": HASH, "fold_index": 0, "candidates_per_target": 2,
            "policy": "TEMPORAL_FREQUENCY", "temporal_window_tokens": 4, "transport": "BUFFERED", "readiness": "DEVICE_READY", "break_even_bps": 3637},
        "seed": [{"layer": 1, "expert": 1, "count": 9, "payload_bytes": 128, "physical_bytes": 160}]}


class PrefetchProfileTests(unittest.TestCase):
    def test_folds_are_causal_and_cover_all_prompts(self) -> None:
        for index in range(6):
            fold = fold_membership(index)
            self.assertEqual(len(fold["training"]), 4)
            self.assertEqual(len(set(fold["training"] + [fold["validation"], fold["test"]])), 6)

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
            "envelopes": [{"transport": "BUFFERED", "readiness": "DEVICE_READY", "supported": True,
                "lead_p50_ns": 100, "demand_service_p50_ns": 80, "speculative_service_p95_ns": 20,
                "predictor_compute_p95_ns": 10, "scheduler_demand_delay_p95_ns": 5, "displacement_refill_p95_ns": 5,
                "storage_bytes": 128, "h2d_bytes": 128, "utility_window_predictions": 8, "utility_min_observations": 4}]})
        self.assertEqual(result["status"], "pass")
        self.assertFalse(result["waste_external_threshold_transferred"])
        self.assertEqual(result["envelopes"][0]["break_even_bps"], 3637)

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
                "readiness": "DEVICE_READY", "temporal_window_tokens": 0, "candidates_per_target": 2,
                "request_ordinal": 1, "events": events, "completion_order": []}
            output = replay(request)
            self.assertEqual(output["candidate_stream"], [])
            self.assertEqual(output["state_digest"], FNV_OFFSET)

    def test_offline_scoring_uses_causal_deadlines_and_physical_budget(self) -> None:
        document = profile()
        document["_events"] = [
            {"token": 0, "layers": [{"layer": 1, "experts": [0, 1]}, {"layer": 2, "experts": [2, 3]}]},
            {"token": 1, "layers": [{"layer": 1, "experts": [1, 2]}, {"layer": 2, "experts": [0, 3]}]},
        ]
        output = {"policy": "PREVIOUS_TOKEN", "candidate_stream": [
            {"trigger_token": 0, "trigger": "TOKEN_END", "source_layer": -1,
                "target_layer": 1, "expert": 1, "rank": 0, "score": 1},
            {"trigger_token": 0, "trigger": "TOKEN_END", "source_layer": -1,
                "target_layer": 2, "expert": 2, "rank": 0, "score": 1},
        ]}
        self.assertEqual(score_candidates(document, output, 320), {
            "predictions": 2, "timely_successes": 1, "actual_demands": 4,
            "precision_bps": 5000, "recall_bps": 2500, "budget_rejections": 0,
            "predicted_physical_bytes": 320, "wasted_physical_bytes": 160})


if __name__ == "__main__":
    unittest.main()

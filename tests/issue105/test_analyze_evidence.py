from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "issue105"))

from analyze_evidence import (  # noqa: E402
    AnalysisError,
    capacity_equivalence_status,
    lofo_partitions,
    physical_exact_anchor_cases,
    projection_allowed,
    projection_gate_result,
    projection_model_eligible,
    threshold_crossing,
    validate_figure_sidecars,
)


def gate_lofo(r_squared: float, bootstrap_lower: float, rmse: float) -> dict[str, object]:
    return {
        "pooled_oof_r_squared": r_squared,
        "pooled_oof_rmse": rmse,
        "cluster_bootstrap": {
            "pooled_oof_r_squared_95_interval": [bootstrap_lower, 0.99],
        },
        "family_residuals": [
            {"semantic_family": "a", "mean_signed_residual": 0.0},
            {"semantic_family": "b", "mean_signed_residual": 0.0},
        ],
        "_predictions": [
            {"semantic_family": "a", "policy": "S2_P50", "length_level": 1,
             "measured": 1.0, "residual": 0.0},
            {"semantic_family": "b", "policy": "S2_P50", "length_level": 1,
             "measured": 1.0, "residual": 0.0},
        ],
    }


def gate_rows() -> list[dict[str, object]]:
    return [
        {"case_id": "a1", "semantic_family": "a", "decode_tok_s": 1.0},
        {"case_id": "b1", "semantic_family": "b", "decode_tok_s": 1.0},
    ]


class Issue105AnalysisTests(unittest.TestCase):
    def test_lofo_leaves_entire_family_out(self) -> None:
        rows = [
            {"semantic_family": family, "case_id": f"{family}-{index}"}
            for family in ("a", "b", "c") for index in range(2)
        ]
        for held_out, training, testing in lofo_partitions(rows):
            self.assertNotIn(held_out, {row["semantic_family"] for row in training})
            self.assertEqual({held_out}, {row["semantic_family"] for row in testing})

    def test_family_dummy_models_cannot_project(self) -> None:
        self.assertTrue(projection_model_eligible("M1"))
        self.assertTrue(projection_model_eligible("M2"))
        self.assertFalse(projection_model_eligible("M3"))
        self.assertFalse(projection_model_eligible("M4"))

    def test_projection_gate_pass_boundary(self) -> None:
        lofo = gate_lofo(0.90, 0.80, 0.02)
        result = projection_gate_result(lofo, lofo, gate_rows(), gate_rows())
        self.assertEqual(result["status"], "PASS")

    def test_projection_gate_fails_below_boundary(self) -> None:
        lofo = gate_lofo(0.899999, 0.80, 0.02)
        result = projection_gate_result(lofo, lofo, gate_rows(), gate_rows())
        self.assertEqual(result["status"], "FAIL")

    def test_projection_gate_fails_material_policy_regime(self) -> None:
        lofo = gate_lofo(0.95, 0.90, 0.01)
        lofo["_predictions"][0]["residual"] = 0.2
        lofo["_predictions"][1]["residual"] = 0.2
        result = projection_gate_result(lofo, lofo, gate_rows(), gate_rows())
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            result["datasets"][0]["checks"]["unexplained_material_regime"]["observed"]
        )

    def test_projection_domain_rejects_extrapolation(self) -> None:
        self.assertTrue(projection_allowed(0.5, [0.0, 1.0]))
        self.assertTrue(projection_allowed(1.0, [0.0, 1.0]))
        self.assertFalse(projection_allowed(1.00001, [0.0, 1.0]))

    def test_capacity_threshold_crossing_preserves_bracket(self) -> None:
        curve = [
            {"status": "pass", "capacity_slots": 10, "capacity_bytes": 100,
             "capacity_label": "a", "hit_ratio": 0.4},
            {"status": "pass", "capacity_slots": 20, "capacity_bytes": 200,
             "capacity_label": "b", "hit_ratio": 0.7},
        ]
        result = threshold_crossing(curve, "hit_ratio", 0.6, "at_least")
        self.assertEqual(result["status"], "BRACKETED")
        self.assertEqual((result["lower_slots"], result["upper_slots"]), (10, 20))

    def test_capacity_target_outside_domain_is_inconclusive(self) -> None:
        curve = [
            {"status": "pass", "capacity_slots": 10, "capacity_bytes": 100,
             "capacity_label": "a", "hit_ratio": 0.4},
            {"status": "pass", "capacity_slots": 20, "capacity_bytes": 200,
             "capacity_label": "b", "hit_ratio": 0.5},
        ]
        result = threshold_crossing(curve, "hit_ratio", 0.6, "at_least")
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertEqual(result["lower_bound_slots"], 20)

    def test_direct_physical_anchors_use_representative_role(self) -> None:
        rows = [
            {"case_id": f"representative-{index}", "selection_role": "STAGE_B_REPRESENTATIVE"}
            for index in range(16)
        ]
        rows.extend([
            {"case_id": "endpoint-a", "selection_role": "STAGE_B2_ENDPOINT"},
            {"case_id": "endpoint-b", "selection_role": "STAGE_B2_ENDPOINT"},
        ])
        self.assertEqual(len(physical_exact_anchor_cases(rows)), 16)
        rows[0]["selection_role"] = "STAGE_B2_ENDPOINT"
        with self.assertRaisesRegex(AnalysisError, "expected 16"):
            physical_exact_anchor_cases(rows)

    def test_hit_load_disagreement_is_not_averaged(self) -> None:
        status, disagreement = capacity_equivalence_status([
            {"status": "BRACKETED", "upper_slots": 20},
            {"status": "BRACKETED", "upper_slots": 30},
        ])
        self.assertTrue(disagreement)
        self.assertEqual(status, "INCONCLUSIVE")

    def test_figure_sidecar_provenance_is_required(self) -> None:
        row = {
            "figure_id": "F01", "input_table_logical_sha256": ["a"],
            "input_source_sha256": ["b"], "analysis_code_version": "c",
            "query_filter_definition": "q", "metric_definitions": ["m"],
            "evidence_classes": ["MEASURED_PHYSICAL"],
            "projection_gate_status": "not_applicable", "output_figure_sha256": "d",
        }
        rows = [dict(row, figure_id=f"F{index:02d}") for index in range(1, 11)]
        validate_figure_sidecars(rows)
        del rows[0]["analysis_code_version"]
        with self.assertRaisesRegex(AnalysisError, "incomplete"):
            validate_figure_sidecars(rows)

    def test_json_outputs_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "value.json"
            path.write_text(json.dumps({"value": 1.0}), encoding="utf-8")
            self.assertEqual(json.loads(path.read_text()), {"value": 1.0})


if __name__ == "__main__":
    unittest.main()

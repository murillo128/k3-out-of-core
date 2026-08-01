from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import host_safe_ceiling, legal_budget_grid, paired_interval  # noqa: E402
from measure_working_sets import event_working_sets  # noqa: E402


class Phase9EvidenceTests(unittest.TestCase):
    def test_legal_grid_deduplicates_and_marks_headroom(self):
        grid = legal_budget_grid(400, 100, 300, 500)
        self.assertEqual([row["effective_bytes"] for row in grid], sorted(set(row["effective_bytes"] for row in grid)))
        self.assertTrue(any("W" in row["labels"] and row["effective_bytes"] == 400 for row in grid))
        self.assertTrue(any(row["disposition"] == "unavailable-by-headroom" for row in grid))

    def test_host_reserve_rule(self):
        result = host_safe_ceiling(64*1024**3, 60*1024**3, 4*1024**3, None)
        self.assertEqual(result["reserve_bytes"], 8*1024**3)
        self.assertEqual(result["safe_ceiling_bytes"], 48*1024**3)

    def test_paired_interval_is_over_run_level_differences(self):
        result = paired_interval([90.0]*10, [100.0]*10)
        self.assertEqual(result["mean_difference"], -10.0)
        self.assertEqual(result["ci95_low"], -10.0)
        self.assertEqual(result["ci95_high"], -10.0)

    def test_working_set_uses_canonical_demand_keys(self):
        def event(sequence, phase, layer, expert, ubatch):
            return {"type": "DEMAND", "event_sequence": sequence, "phase": phase, "layer": layer,
                    "expert": expert, "ubatch_ordinal": ubatch, "request_ordinal": 1,
                    "physical_slot_footprint_bytes": 64, "occurrence_count": 1}
        capture = {"hot": {"events": []}, "cold": {"events": [
            event(1, "PREFILL", 0, 1, 1), event(2, "DECODE", 0, 1, 2),
            event(3, "DECODE", 1, 2, 2), event(4, "DECODE", 0, 1, 3),
        ]}, "routes": []}
        result = event_working_sets(capture)
        self.assertEqual(result["one_expert_footprint_bytes"], 64)
        self.assertEqual(result["checkpoint_working_set_bytes"]["decode"]["max"], 128)
        self.assertEqual(result["token_working_set_bytes"]["decode"]["max"], 128)
        self.assertEqual(result["protected_decode_set_bytes"], 64)


if __name__ == "__main__":
    unittest.main()

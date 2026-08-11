from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase13"))

try:
    import numpy as np
    from analyze_quality_trace import (
        MAGIC,
        QualityTraceError,
        compare_routes,
        compare_traces,
    )
    HAS_NUMPY = True
except ModuleNotFoundError:
    HAS_NUMPY = False


def write_trace(path: Path, records: list[tuple[int, int, int, int, list[float]]]) -> None:
    metadata = json.dumps({
        "schema_version": "phase13-quality-trace-v1",
        "prompt_ids": [10, 11],
    }).encode()
    with path.open("wb") as destination:
        destination.write(MAGIC)
        destination.write(struct.pack("<I", len(metadata)))
        destination.write(metadata)
        for record_type, step, layer, target, values in records:
            destination.write(struct.pack("<B3xIiiIQ", record_type, step, layer, target, 1, len(values)))
            destination.write(np.asarray(values, dtype="<f4").tobytes())


def route(selected: int) -> dict[str, object]:
    return {
        "request_ordinal": 1,
        "ubatch_ordinal": 1,
        "phase": "DECODE",
        "layer": 1,
        "n_tokens": 1,
        "n_expert_used": 1,
        "n_candidates": 2,
        "positions": [2],
        "selected_experts": [selected],
        "weights": [1.0],
        "candidate_experts": [0, 1],
        "candidate_selection_scores": [1.0, 0.9],
        "candidate_probabilities": [0.6, 0.4],
    }


def capture(selected: int) -> dict[str, object]:
    return {
        "schema_version": "phase13-exact-topm-capture-v1",
        "prompt_ids": [10, 11],
        "generated_ids": [1],
        "routes": [route(selected)],
    }


@unittest.skipUnless(HAS_NUMPY, "quality analyzer requires numpy")
class QualityTraceTests(unittest.TestCase):
    def test_identity_and_changed_predictive_metrics(self):
        exact_records = [
            (1, 1, 1, -1, [1.0, 0.0]),
            (2, 1, 1, -1, [1.0, 1.0]),
            (3, 0, -1, 1, [0.0, 2.0, 1.0]),
        ]
        changed_records = [
            (1, 1, 1, -1, [0.8, 0.2]),
            (2, 1, 1, -1, [1.0, 1.1]),
            (3, 0, -1, 1, [0.0, 1.5, 1.2]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            exact_path = Path(directory) / "exact.p13q"
            changed_path = Path(directory) / "changed.p13q"
            write_trace(exact_path, exact_records)
            write_trace(changed_path, changed_records)
            identity, _ = compare_traces(exact_path, exact_path, 2)
            self.assertEqual(identity["hidden_state"]["relative_l2"]["max"], 0.0)
            self.assertEqual(identity["predictive"]["top1_agreement_fraction"], 1.0)
            comparison, moe = compare_traces(exact_path, changed_path, 2)
            self.assertGreater(comparison["moe_output"]["relative_l2"]["mean"], 0.0)
            self.assertGreater(comparison["hidden_state"]["relative_l2"]["mean"], 0.0)
            self.assertGreater(comparison["predictive"]["kl_exact_to_changed"]["mean"], 0.0)
            self.assertIn((1, 1), moe)

    def test_route_attribution_finds_first_intentional_swap(self):
        with tempfile.TemporaryDirectory() as directory:
            exact_path = Path(directory) / "exact.json"
            changed_path = Path(directory) / "changed.json"
            exact_path.write_text(json.dumps(capture(0)))
            changed_path.write_text(json.dumps(capture(1)))
            comparison, first = compare_routes(exact_path, changed_path)
            self.assertEqual(first, (1, 1))
            self.assertEqual(comparison["intentional_swaps"], 1)
            self.assertAlmostEqual(comparison["cumulative_regret"], 0.1)
            self.assertEqual(comparison["induced_exact_topk_divergent_decisions"], 0)

    def test_mismatched_trace_sequence_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            exact_path = Path(directory) / "exact.p13q"
            changed_path = Path(directory) / "changed.p13q"
            write_trace(exact_path, [(1, 1, 1, -1, [1.0])])
            write_trace(changed_path, [(2, 1, 1, -1, [1.0])])
            with self.assertRaises(QualityTraceError):
                compare_traces(exact_path, changed_path, 5)


if __name__ == "__main__":
    unittest.main()

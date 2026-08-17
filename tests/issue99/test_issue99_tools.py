from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "issue99"))

from analyze_campaign import added_model_signal, breakpoint_test  # noqa: E402
from analyze_pair import (  # noqa: E402
    MAGIC, PairError, predictive_metrics, pair_routes, paired_trace_metrics,
)
from analyze_pair import Record as TraceRecord  # noqa: E402
from protocol import (  # noqa: E402
    BROAD_CASES, BRIDGE_CASES, LOW_BRIDGE_CACHE_BYTES, expected_cell_count,
    reference_identity,
)


HEADER = struct.Struct("<B3xIiiIQ")


def write_trace(path: Path, case_id: str, records: list[tuple[int, int, int, int, list[float]]]) -> None:
    metadata = json.dumps({
        "schema_version": "phase13-quality-trace-v1",
        "issue99_trace_contract": "issue99-ephemeral-paired-tensor-trace-v1",
        "case_id": case_id,
    }).encode()
    with path.open("wb") as destination:
        destination.write(MAGIC)
        destination.write(struct.pack("<I", len(metadata)))
        destination.write(metadata)
        for kind, position, layer, target, values in records:
            destination.write(HEADER.pack(kind, position, layer, target, 1, len(values)))
            destination.write(np.asarray(values, dtype="<f4").tobytes())


def route(position: int, layer: int, swap: bool) -> dict[str, object]:
    candidates = list(range(32))
    selected = list(range(16))
    if swap and layer == 1:
        selected[0] = 16
    return {
        "record_type": "route", "sequence_position": position, "layer": layer,
        "selected_experts": selected, "selected_weights": [1.0 / 16] * 16,
        "candidate_experts": candidates,
        "candidate_selection_scores": [1.0 - rank * 0.001 for rank in range(32)],
        "candidate_probabilities": [0.04 - rank * 0.0005 for rank in range(32)],
    }


def write_routes(path: Path, swap: bool) -> None:
    metadata = {
        "record_type": "metadata",
        "metadata": {"case_id": "case", "capacity_bytes": LOW_BRIDGE_CACHE_BYTES,
                     "candidate_count": 32, "selected_count": 16},
    }
    with path.open("w") as destination:
        destination.write(json.dumps(metadata) + "\n")
        for layer in range(1, 93):
            destination.write(json.dumps(route(1, layer, swap)) + "\n")


class Issue99ToolTests(unittest.TestCase):
    def test_frozen_cohorts_and_cell_budget(self):
        self.assertEqual(len(BROAD_CASES), 16)
        self.assertEqual(len(BRIDGE_CASES), 3)
        self.assertEqual(expected_cell_count(True), 72)
        self.assertEqual(expected_cell_count(False), 63)

    def test_reference_identity_is_order_and_horizon_sensitive(self):
        first = reference_identity("case", 512, 7, [1, 2, 3])
        self.assertEqual(first, reference_identity("case", 512, 7, [1, 2, 3]))
        self.assertNotEqual(first, reference_identity("case", 512, 7, [1, 3, 2]))
        self.assertNotEqual(first, reference_identity("case", 1024, 7, [1, 2, 3]))

    def test_predictive_metric_enforces_fixed_reference(self):
        exact = TraceRecord("logits", 1, -1, 1, 1, np.asarray([0.0, 2.0, 1.0], dtype=np.float32))
        changed = TraceRecord("logits", 1, -1, 1, 1, np.asarray([0.0, 1.5, 1.2], dtype=np.float32))
        value = predictive_metrics(exact, changed, direct=True)
        self.assertGreater(value["delta_reference_nll"], 0)
        divergent = TraceRecord("logits", 1, -1, 2, 1, changed.values)
        with self.assertRaises(PairError):
            predictive_metrics(exact, divergent, direct=True)
        free = predictive_metrics(exact, divergent, direct=False)
        self.assertIsNone(free["delta_reference_nll"])
        self.assertIsNotNone(free["trajectory_exact_token_delta_nll"])

    def test_trace_pair_scalarizes_every_layer_and_token(self):
        records = []
        changed = []
        for layer in range(1, 93):
            records.extend(((1, 1, layer, -1, [1.0, 0.0]), (2, 1, layer, -1, [1.0, 1.0])))
            changed.extend(((1, 1, layer, -1, [0.9, 0.1]), (2, 1, layer, -1, [1.0, 1.1])))
        records.append((3, 1, -1, 1, [0.0, 2.0, 1.0]))
        changed.append((3, 1, -1, 1, [0.0, 1.5, 1.2]))
        with tempfile.TemporaryDirectory() as directory:
            exact_path = Path(directory) / "exact.p13q"
            changed_path = Path(directory) / "changed.p13q"
            write_trace(exact_path, "case", records)
            write_trace(changed_path, "case", changed)
            token, layers = paired_trace_metrics(exact_path, changed_path, direct=True)
            self.assertEqual(len(layers), 92)
            self.assertEqual(list(token), [1])
            self.assertGreater(token[1]["hidden_relative_l2_mean"], 0)
            identity, _ = paired_trace_metrics(exact_path, exact_path, direct=True)
            self.assertEqual(identity[1]["moe_relative_l2_max"], 0)

    def test_route_pair_recomputes_signed_and_corrected_regret(self):
        with tempfile.TemporaryDirectory() as directory:
            exact_path = Path(directory) / "exact.jsonl"
            changed_path = Path(directory) / "changed.jsonl"
            write_routes(exact_path, False)
            write_routes(changed_path, True)
            core = {
                "1.0": {layer: ({0} if layer == 1 else set()) for layer in range(1, 93)},
                "0.8": {layer: ({0, 16} if layer == 1 else set()) for layer in range(1, 93)},
            }
            identity = {"case_id": "case", "cache_regime": "96-gib-bridge",
                        "changed_intervention": "CAPACITY_FIXED_CONTEXT", "policy": "KNEE"}
            routes, events, token = pair_routes(exact_path, changed_path, identity, core)
            self.assertEqual(len(routes), 92)
            self.assertEqual(len(events), 1)
            self.assertAlmostEqual(events[0]["corrected_regret"], 0.016)
            self.assertAlmostEqual(events[0]["raw_probability_regret_signed"], 0.008)
            self.assertEqual(events[0]["transition_gamma_1_0"], "core_to_peripheral")
            self.assertEqual(events[0]["transition_gamma_0_8"], "core_to_core")
            self.assertEqual(token[1]["intentional_swaps"], 1)

    def test_added_signal_and_bounded_breakpoint_use_prompt_clusters(self):
        rows = []
        for prompt in range(10):
            for checkpoint in (64, 128, 256, 512):
                x = checkpoint / 64
                rows.append({"case_id": f"p{prompt}", "base": x, "added": prompt + x,
                             "target": prompt + x, "policy": "S2_P50", "checkpoint": checkpoint,
                             "cumulative_mean_delta_nll": x + (max(0, checkpoint - 128) / 64)})
        frame = __import__("pandas").DataFrame(rows)
        signal = added_model_signal(frame, ["base"], ["added"], "target", 123)
        self.assertEqual(signal["valid_prompts"], 10)
        breakpoint = breakpoint_test(frame)
        self.assertEqual(breakpoint["candidates"], [128, 256])
        self.assertIn(breakpoint["classification"], ("supported", "weak", "no"))


if __name__ == "__main__":
    unittest.main()

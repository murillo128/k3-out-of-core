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
    MAGIC, PairError, horizon_evidence, predictive_metrics, pair_routes, paired_trace_metrics,
)
from analyze_pair import Record as TraceRecord  # noqa: E402
from protocol import (  # noqa: E402
    BROAD_CASES, BRIDGE_CASES, LOW_BRIDGE_CACHE_BYTES, expected_cell_count,
    reference_identity,
)
from run_campaign import (  # noqa: E402
    CampaignError, reference_sequence_arguments, reference_sequence_path,
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


def write_routes(path: Path, swap: bool, horizon: int = 1) -> None:
    metadata = {
        "record_type": "metadata",
        "metadata": {"case_id": "case", "capacity_bytes": LOW_BRIDGE_CACHE_BYTES,
                     "candidate_count": 32, "selected_count": 16},
    }
    with path.open("w") as destination:
        destination.write(json.dumps(metadata) + "\n")
        for position in range(1, horizon + 1):
            for layer in range(1, 93):
                destination.write(json.dumps(route(position, layer, swap)) + "\n")


def trace_records(horizon: int, accepted_token: int = 1) -> list[tuple[int, int, int, int, list[float]]]:
    records = []
    for position in range(1, horizon + 1):
        for layer in range(1, 93):
            records.extend(((1, position, layer, -1, [1.0, 0.0]),
                            (2, position, layer, -1, [1.0, 1.0])))
        records.append((3, position, -1, accepted_token, [0.0, 2.0, 1.0]))
    return records


class Issue99ToolTests(unittest.TestCase):
    def test_command_construction_respects_reference_sequence_contract(self):
        cases = (
            ({"cohort": "bridge", "case_id": "case", "policy": "EXACT",
              "intervention": "FREE_TRAJECTORY", "cache_regime": "high-cache"}, None),
            ({"cohort": "broad", "case_id": "case", "policy": "KNEE",
              "intervention": "DIRECT_FIXED_CONTEXT", "cache_regime": "high-cache"},
             "case-high-512.json"),
            ({"cohort": "bridge", "case_id": "case", "policy": "KNEE",
              "intervention": "FREE_TRAJECTORY", "cache_regime": "high-cache"}, None),
            ({"cohort": "low-bridge", "case_id": "case", "policy": "EXACT",
              "intervention": "CAPACITY_FIXED_CONTEXT", "cache_regime": "96-gib-bridge"},
             "case-low-input-512.json"),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for cell, expected_name in cases:
                reference = reference_sequence_path(root, cell)
                arguments = reference_sequence_arguments(cell, reference)
                if expected_name is None:
                    self.assertIsNone(reference)
                    self.assertEqual(arguments, [])
                else:
                    self.assertEqual(reference.name, expected_name)
                    self.assertEqual(arguments, ["--reference-sequence", str(reference)])
            with self.assertRaises(CampaignError):
                reference_sequence_arguments(cases[2][0], root / "forbidden.json")
            with self.assertRaises(CampaignError):
                reference_sequence_arguments(cases[1][0], None)

    def test_quality_helper_opens_one_unified_route_transaction_per_token(self):
        source = (ROOT / "scripts" / "issue99" / "quality_probe.cpp").read_text()
        self.assertEqual(source.count("llama_cache_aware_routing_begin("), 1)
        self.assertEqual(source.count("llama_route_observer_begin("), 1)
        self.assertIn("begin_route_transaction(context.get(), position, changed_routing)", source)

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
            token, layers, coverage = paired_trace_metrics(exact_path, changed_path, direct=True)
            self.assertEqual(len(layers), 92)
            self.assertEqual(list(token), [1])
            self.assertEqual(coverage, {"exact": 1, "changed": 1, "common": 1})
            self.assertGreater(token[1]["hidden_relative_l2_mean"], 0)
            identity, _, _ = paired_trace_metrics(exact_path, exact_path, direct=True)
            self.assertEqual(identity[1]["moe_relative_l2_max"], 0)

    def test_free_trajectory_preserves_eog_shortened_common_prefix_in_both_orders(self):
        core = {
            gamma: {layer: set() for layer in range(1, 93)}
            for gamma in ("1.0", "0.8")
        }
        identity = {"case_id": "case", "cache_regime": "high-cache",
                    "changed_intervention": "FREE_TRAJECTORY", "policy": "KNEE"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for exact_horizon, changed_horizon in ((2, 1), (1, 2)):
                exact_trace = root / f"exact-{exact_horizon}-{changed_horizon}.p13q"
                changed_trace = root / f"changed-{exact_horizon}-{changed_horizon}.p13q"
                exact_routes = root / f"exact-{exact_horizon}-{changed_horizon}.jsonl"
                changed_routes = root / f"changed-{exact_horizon}-{changed_horizon}.jsonl"
                write_trace(exact_trace, "case", trace_records(exact_horizon))
                write_trace(changed_trace, "case", trace_records(changed_horizon, accepted_token=2))
                write_routes(exact_routes, False, exact_horizon)
                write_routes(changed_routes, True, changed_horizon)
                tokens, layers, trace_coverage = paired_trace_metrics(
                    exact_trace, changed_trace, direct=False)
                routes, _, route_tokens, route_coverage = pair_routes(
                    exact_routes, changed_routes, identity, core, direct=False)
                expected = {"exact": exact_horizon, "changed": changed_horizon, "common": 1}
                self.assertEqual(trace_coverage, expected)
                self.assertEqual(route_coverage, expected)
                self.assertEqual(list(tokens), [1])
                self.assertEqual(list(route_tokens), [1])
                self.assertEqual(len(layers), 92)
                self.assertEqual(len(routes), 92)
                summary = horizon_evidence(
                    {"generation_phase": {"eog_position": exact_horizon}},
                    {"generation_phase": {"eog_position": changed_horizon}},
                    exact_horizon, changed_horizon, 1024)
                self.assertEqual(summary["exact_achieved_horizon"], exact_horizon)
                self.assertEqual(summary["changed_achieved_horizon"], changed_horizon)
                self.assertEqual(summary["paired_achieved_horizon"], 1)
                self.assertEqual(summary["unavailable_tail_checkpoints"]["paired"],
                                 [16, 32, 64, 128, 256, 512, 1024])
                with self.assertRaises(PairError):
                    paired_trace_metrics(exact_trace, changed_trace, direct=True)
                with self.assertRaises(PairError):
                    pair_routes(exact_routes, changed_routes, identity, core, direct=True)

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
            routes, events, token, coverage = pair_routes(exact_path, changed_path, identity, core)
            self.assertEqual(len(routes), 92)
            self.assertEqual(len(events), 1)
            self.assertEqual(coverage, {"exact": 1, "changed": 1, "common": 1})
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

#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
sys.path.insert(0, str(ROOT / "scripts/phase9"))

from analyze_cache_locality import Event, replay_capacity, reuse_statistics  # noqa: E402
from analyze_colibri_endpoint_campaign import mean_ci_95  # noqa: E402
from analyze_colibri_endpoint_trace import build_attribution  # noqa: E402
from cache_policy_simulator import replay as phase9_replay  # noqa: E402
from common import Scale  # noqa: E402
from capture_real_routing import normalize_route  # noqa: E402
from corpus import generate  # noqa: E402
from plan import build_plan, encode_plan  # noqa: E402
from run_colibri_endpoint import (  # noqa: E402
    EXPERT_BYTES,
    ROUTED_LAYERS,
    derive_max_safe_slots,
    parse_endpoint,
    slots_for_capacity_bytes,
)


FIXTURE = Scale(layers=2, experts=32, selected=16, projection_bytes=4096, tokens=4)


class Phase12NvmePlanTests(unittest.TestCase):
    def test_orders_preserve_logical_identity_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-plan-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            plans = {
                order: build_plan(corpus, "A", "LOGICAL_SHUFFLE", 1, order)
                for order in ("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8")
            }
            identities = {
                order: sorted((item.ordinal, item.layer, item.expert, item.sha256) for item in operations)
                for order, operations in plans.items()
            }
            self.assertEqual(identities["LOGICAL_SELECTED"], identities["PHYSICAL_OFFSET"])
            self.assertEqual(identities["LOGICAL_SELECTED"], identities["LOCALITY_WINDOW_8"])
            self.assertNotEqual(
                [item.ordinal for item in plans["LOGICAL_SELECTED"]],
                [item.ordinal for item in plans["PHYSICAL_OFFSET"]],
            )
            self.assertEqual(len(encode_plan(plans["LOGICAL_SELECTED"]).splitlines()), 33)

    def test_layouts_are_plan_checksum_equivalent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-layout-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            layout_a = build_plan(corpus, "A", "HALF_HOT", 3, "LOGICAL_SELECTED")
            layout_b = build_plan(corpus, "B", "HALF_HOT", 3, "LOGICAL_SELECTED")
            self.assertEqual(
                [(item.ordinal, item.layer, item.expert, item.length, item.sha256) for item in layout_a],
                [(item.ordinal, item.layer, item.expert, item.length, item.sha256) for item in layout_b],
            )

    def test_malformed_layout_b_index_fails_plan_construction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-index-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            path = corpus / "layout-b/contiguous-experts.bin"
            with path.open("r+b") as stream:
                header = json.loads(stream.read(4096).rstrip(b"\0"))
                stream.seek(header["index_offset"])
                byte = stream.read(1)
                stream.seek(header["index_offset"])
                stream.write(bytes((byte[0] ^ 1,)))
            with self.assertRaisesRegex(ValueError, "Layout B index checksum mismatch"):
                build_plan(corpus, "B", "COLD_SPREAD", 0, "LOGICAL_SELECTED")

    def test_colibri_route_normalization_recovers_complete_decode_forwards(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-route-") as temporary:
            raw = Path(temporary) / "route.txt"
            normalized = Path(temporary) / "normalized.tsv"
            lines = []
            # One two-row prefill cycle followed by two one-row decode cycles.
            for rows in (2, 1, 1):
                for layer in range(1, 93):
                    for row in range(rows):
                        experts = [f"{(layer * 17 + row * 29 + rank) % 896}:0.0625" for rank in range(16)]
                        # The pinned Kimi engine currently leaves the legacy call field at zero;
                        # cycle recovery intentionally relies on verified layer resets instead.
                        lines.append(" ".join(["0", str(row), str(layer), *experts]))
            raw.write_text("\n".join(lines) + "\n")
            result = normalize_route(raw, normalized, request_id=7, prompt_tokens=2, chunk=32)
            self.assertEqual(result["prefill_cycles"], 1)
            self.assertEqual(result["complete_decode_forwards"], 2)
            self.assertEqual(result["normalized_demands"], 4 * 92 * 16)
            records = normalized.read_text().splitlines()
            self.assertEqual(records[1].split("\t")[:4], ["7", "PREFILL", "0", "1"])
            self.assertEqual(records[17].split("\t")[:4], ["7", "PREFILL", "0", "2"])
            self.assertEqual(records[1 + 92 * 16].split("\t")[:4], ["7", "PREFILL", "1", "1"])

    def test_global_lru_replay_matches_phase9_canonical_semantics(self) -> None:
        expert_sequence = [1, 2, 1, 3, 2]
        events = [Event(0, "DECODE", token, 0, 0, expert) for token, expert in enumerate(expert_sequence)]
        result = replay_capacity(
            events, slots=2, cold_decode_tokens=1, expected_requests_per_decode_token=1,
        )
        self.assertEqual(result["windows"]["decode"]["hits"], 1)
        self.assertEqual(result["windows"]["decode"]["misses"], 4)
        self.assertEqual(result["admissions"], 4)
        self.assertEqual(result["evictions"], 2)

        config = {
            "schema_version": "cache-policy-config-v1",
            "policy": "LRU",
            "scope": "GLOBAL",
            "slru_protected_ratio_bps": 0,
            "admission": "ALWAYS",
            "admission_window_events": 0,
            "lfu_aging_interval_events": 0,
        }
        phase9_input = {
            "schema_version": "cache-policy-replay-input-v1",
            "topology": {
                "routed_layers": [0],
                "experts_per_layer": 4,
                "physical_slot_footprint_bytes": 128,
            },
            "hot": {"slots": 1, "config": config},
            "cold": {"slots": 2, "config": config},
            "requests": [{
                "request_ordinal": 1,
                "checkpoints": [
                    {
                        "checkpoint_ordinal": index + 1,
                        "ubatch_ordinal": 0,
                        "phase": "DECODE",
                        "demands": [{
                            "layer": 0,
                            "expert": expert,
                            "occurrence_count": 1,
                            "logical_payload_bytes": 128,
                            "hot_admission": "MANDATORY_CURRENT_OUTPUT",
                        }],
                    }
                    for index, expert in enumerate(expert_sequence)
                ],
                "outcome": "SUCCESS",
            }],
        }
        canonical = phase9_replay(phase9_input, capture_events=False)
        self.assertEqual(canonical["summary"]["backing_store_hits"], result["windows"]["decode"]["misses"])

    def test_reuse_distance_predicts_lru_capacity_hits(self) -> None:
        events = [
            Event(0, "DECODE", token, 0, 0, expert)
            for token, expert in enumerate([1, 2, 1, 3, 2])
        ]
        reuse = reuse_statistics(events, {0: 0, 1: 1, 2: 2})
        self.assertEqual(reuse["decode"]["first_references"], 3)
        self.assertEqual(reuse["decode"]["reuses"], 2)
        self.assertEqual(reuse["decode"]["theoretical_lru_hits_by_capacity_gib"], {"0": 0, "1": 0, "2": 1})

    def test_colibri_endpoint_capacity_rounds_to_whole_per_layer_slots(self) -> None:
        per_slot = ROUTED_LAYERS * EXPERT_BYTES
        self.assertEqual(slots_for_capacity_bytes(8 * (1 << 30)), 5)
        self.assertEqual(slots_for_capacity_bytes(96 * (1 << 30)), 63)
        ceiling = 161_639_786_086
        non_cache = 39_041_900_544
        self.assertEqual(derive_max_safe_slots(ceiling, non_cache), 75)
        self.assertLessEqual(non_cache + 75 * per_slot, ceiling)
        self.assertGreater(non_cache + 76 * per_slot, ceiling)

    def test_colibri_endpoint_parser_preserves_nested_scopes_and_exact_counters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-endpoint-") as temporary:
            trace = Path(temporary) / "endpoint.tsv"
            trace.write_text(
                "ts_ns\ttid\tevent\tname\tphase\tforward\tlayer\tv0\tv1\tv2\tv3\tv4\tv5\tv6\tv7\tv8\tv9\n"
                "1\t7\tB\tforward\tdecode\t0\t-1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\n"
                "2\t7\tB\trouter\tdecode\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\n"
                "3\t7\tE\trouter\tdecode\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\n"
                "4\t7\tS\tlayer\tdecode\t0\t1\t2\t14\t245661696\t5\t5\t0\t14\t0\t1\t2\n"
                "5\t7\tE\tforward\tdecode\t0\t-1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\n"
                "6\t7\tS\tforward\tdecode\t0\t-1\t2\t14\t245661696\t5\t5\t0\t14\t0\t1\t2\n"
                "7\t7\tT\ttoken\tsampling\t0\t-1\t0\t42\t0\t0\t0\t0\t0\t0\t0\t0\n"
                "8\t7\tS\trun\tcomplete\t1\t-1\t2\t14\t245661696\t5\t5\t0\t14\t0\t1\t2\n"
            )
            parsed = parse_endpoint(trace)
            self.assertEqual(parsed["tokens"], [42])
            self.assertEqual(len(parsed["decode_forwards"]), 1)
            self.assertEqual(parsed["decode_forwards"][0]["duration_seconds"], 4e-9)
            self.assertEqual(parsed["layer_stats"][0]["v1"], 14)
            self.assertEqual(parsed["run_stats"]["v7"], 0)

    def test_colibri_endpoint_paired_interval_uses_three_predeclared_pairs(self) -> None:
        interval = mean_ci_95([0.90, 0.91, 0.92])
        self.assertEqual(interval["degrees_of_freedom"], 2)
        self.assertAlmostEqual(interval["mean"], 0.91)
        self.assertLess(interval["lower"], interval["mean"])
        self.assertGreater(interval["upper"], interval["mean"])
        with self.assertRaisesRegex(ValueError, "exactly three"):
            mean_ci_95([0.90, 0.91])

    def test_colibri_endpoint_attribution_is_non_overlapping_and_closes_wall_time(self) -> None:
        categories, attributed, residual = build_attribution(
            {"nvme_wait": 60, "expert_compute": 20, "attention": 10}, 100,
        )
        self.assertEqual(attributed, 90)
        self.assertEqual(residual, 10)
        self.assertEqual(sum(item["duration_ns"] for item in categories.values()), 100)
        self.assertEqual(categories["nvme_wait"]["fraction"], 0.6)


if __name__ == "__main__":
    unittest.main(verbosity=2)

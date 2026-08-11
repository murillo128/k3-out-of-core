from __future__ import annotations

import copy
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase13"))

from cache_aware_replay import (  # noqa: E402
    DEFAULT_MAX_SWAPS,
    GIB,
    ReplayError,
    replay_point,
    run_replay,
    select_cache_aware,
    validate_capture,
)
from analyze_route_streams import compare as compare_route_streams  # noqa: E402
from run_cache_aware_gate import load_case, validate_decision_capture  # noqa: E402


def routing_inputs():
    baseline = list(range(16))
    candidates = baseline + [16, 17, 18, 19]
    scores = [1.0 - rank*0.01 for rank in range(16)] + [0.849, 0.848, 0.847, 0.846]
    tiers = ["HOT"]*15 + ["BACKING", "HOT", "COLD", "HOT", "BACKING"]
    return baseline, candidates, scores, tiers


def route(phase: str, selected: list[int], candidates: list[int], scores: list[float], ubatch: int):
    return {
        "request_ordinal": 1,
        "ubatch_ordinal": ubatch,
        "phase": phase,
        "layer": 1,
        "n_tokens": 1,
        "n_expert_used": len(selected),
        "n_candidates": len(candidates),
        "positions": [ubatch],
        "selected_experts": selected,
        "weights": [1.0/len(selected)]*len(selected),
        "candidate_experts": candidates,
        "candidate_selection_scores": scores,
        "candidate_probabilities": [0.5 - rank*0.001 for rank in range(len(candidates))],
    }


def capture():
    prefill_selected = list(range(15)) + [16]
    prefill_candidates = prefill_selected + [15]
    decode_selected = list(range(16))
    decode_candidates = decode_selected + [16]
    scores = [1.0 - rank*0.01 for rank in range(16)] + [0.849]
    return {
        "schema_version": "phase13-exact-topm-capture-v1",
        "status": "pass",
        "model_path": "/evidence/kimi-k3.gguf",
        "candidate_count": 17,
        "prompt_ids": [1],
        "execution": {
            "backend": "CPU", "n_gpu_layers": 0, "weight_repacking": False, "n_ctx": 8,
            "n_batch": 4, "n_ubatch": 1, "threads": 2,
        },
        "sampling": {"temperature": 0.0, "selection": "argmax"},
        "generated_ids": [2],
        "observer_stats": {
            "ubatches": 2,
            "layers": 2,
            "copy_bytes": 1,
            "explicit_synchronizations": 2,
            "failures": 0,
        },
        "routes": [
            route("PREFILL", prefill_selected, prefill_candidates, scores, 0),
            route("DECODE", decode_selected, decode_candidates, scores, 1),
        ],
    }


class CacheAwarePolicyTests(unittest.TestCase):
    def test_exact_controls(self):
        baseline, candidates, scores, tiers = routing_inputs()
        for candidate_count, max_swaps, regret in (
                (20, 0, 1.0), (16, 16, 1.0), (20, 16, 0.0)):
            final, swaps = select_cache_aware(
                baseline, candidates, scores, tiers, candidate_count, max_swaps, regret)
            self.assertEqual(final, baseline)
            self.assertEqual(swaps, [])

        tie_scores = list(scores)
        tie_scores[16] = tie_scores[15]
        final, swaps = select_cache_aware(
            baseline, candidates, tie_scores, tiers, 20, 1, 0.0)
        self.assertEqual(final, baseline)
        self.assertEqual(swaps, [])

    def test_near_tie_and_outside_bound(self):
        baseline, candidates, scores, tiers = routing_inputs()
        final, swaps = select_cache_aware(baseline, candidates, scores, tiers, 20, 1, 0.002)
        self.assertEqual(len(swaps), 1)
        self.assertEqual(swaps[0].selected_rank, 15)
        self.assertEqual(swaps[0].candidate_expert, 16)
        self.assertEqual(final[:15], baseline[:15])
        self.assertEqual(final[15], 16)

        final, swaps = select_cache_aware(baseline, candidates, scores, tiers, 20, 1, 0.0005)
        self.assertEqual(final, baseline)
        self.assertEqual(swaps, [])

    def test_tier_preference_precedes_regret(self):
        baseline, candidates, scores, tiers = routing_inputs()
        tiers[16] = "COLD"
        tiers[17] = "HOT"
        scores[16] = 0.8499
        scores[17] = 0.8490
        final, swaps = select_cache_aware(baseline, candidates, scores, tiers, 18, 1, 0.01)
        self.assertEqual(final[15], 17)
        self.assertEqual(swaps[0].tier_improvement, 2)

    def test_multiple_swaps_are_deterministic_unique_and_rank_preserving(self):
        baseline, candidates, scores, tiers = routing_inputs()
        tiers[14] = "BACKING"
        first = select_cache_aware(baseline, candidates, scores, tiers, 20, 2, 0.02)
        second = select_cache_aware(baseline, candidates, scores, tiers, 20, 2, 0.02)
        self.assertEqual(first, second)
        final, swaps = first
        self.assertEqual(len(swaps), 2)
        self.assertEqual(len(final), 16)
        self.assertEqual(len(set(final)), 16)
        changed = {swap.selected_rank for swap in swaps}
        for rank, expert in enumerate(baseline):
            if rank not in changed:
                self.assertEqual(final[rank], expert)

    def test_maximum_swap_bound_can_replace_all_sixteen_slots(self):
        baseline = list(range(16))
        candidates = baseline + list(range(16, 32))
        scores = [1.0 - rank*0.001 for rank in range(32)]
        tiers = ["BACKING"]*16 + ["HOT"]*16
        final, swaps = select_cache_aware(
            baseline, candidates, scores, tiers, 32, 16, 0.1)
        self.assertEqual(final, list(reversed(range(16, 32))))
        self.assertEqual(len(swaps), 16)
        self.assertEqual({swap.selected_rank for swap in swaps}, set(range(16)))
        self.assertEqual({swap.candidate_rank for swap in swaps}, set(range(16, 32)))

    def test_invalid_config_and_candidates_fail(self):
        baseline, candidates, scores, tiers = routing_inputs()
        for regret in (-1.0, math.nan, math.inf):
            with self.assertRaises(ReplayError):
                select_cache_aware(baseline, candidates, scores, tiers, 20, 1, regret)
        duplicate = list(candidates)
        duplicate[16] = duplicate[15]
        with self.assertRaises(ReplayError):
            select_cache_aware(baseline, duplicate, scores, tiers, 20, 1, 0.1)
        invalid_scores = list(scores)
        invalid_scores[16] = math.nan
        with self.assertRaises(ReplayError):
            select_cache_aware(baseline, candidates, invalid_scores, tiers, 20, 1, 0.1)


class OfflineReplayTests(unittest.TestCase):
    def test_capture_validation_rejects_nonfinite_probability(self):
        value = capture()
        validate_capture(value)
        invalid = copy.deepcopy(value)
        invalid["routes"][0]["candidate_probabilities"][0] = math.inf
        with self.assertRaises(ReplayError):
            validate_capture(invalid)
        unordered = copy.deepcopy(value)
        unordered["routes"][0]["candidate_selection_scores"][16] = 2.0
        with self.assertRaises(ReplayError):
            validate_capture(unordered)
        missing_position = copy.deepcopy(value)
        missing_position["routes"][0]["positions"] = []
        with self.assertRaises(ReplayError):
            validate_capture(missing_position)
        negative_probability = copy.deepcopy(value)
        negative_probability["routes"][0]["candidate_probabilities"][0] = -0.1
        with self.assertRaises(ReplayError):
            validate_capture(negative_probability)

    def test_capture_validation_accepts_only_bounded_changed_membership(self):
        value = capture()
        value["cache_aware_routing"] = {
            "enabled": True,
            "candidate_count": 17,
            "max_swaps": 1,
            "max_score_regret": 0.002,
            "prefill_rerouting": False,
        }
        value["routes"][1]["selected_experts"][15] = 16
        validate_capture(value)

        prefill_changed = copy.deepcopy(value)
        prefill_changed["routes"][0]["selected_experts"][15] = 15
        with self.assertRaises(ReplayError):
            validate_capture(prefill_changed)
        outside_bound = copy.deepcopy(value)
        outside_bound["cache_aware_routing"]["max_score_regret"] = 0.0005
        with self.assertRaises(ReplayError):
            validate_capture(outside_bound)

    def test_decision_capture_requires_complete_full_k3_accounting(self):
        case = {
            "expected_prompt_tokens": 1,
            "max_generate": 2,
            "candidate_count": 17,
            "n_ubatch": 4,
            "n_ctx": 8,
            "n_batch": 4,
            "threads": 2,
        }
        value = capture()
        value["execution"] = {
            "backend": "CPU", "n_gpu_layers": 0, "weight_repacking": False, "n_ctx": 8,
            "n_batch": 4, "n_ubatch": 4, "threads": 2,
        }
        value["sampling"] = {"temperature": 0.0, "selection": "argmax"}
        value["routes"] = []
        for phase, ubatch in (("PREFILL", 0), ("DECODE", 1)):
            selected = list(range(16))
            candidates = selected + [16]
            scores = [1.0 - rank*0.01 for rank in range(16)] + [0.849]
            for layer in range(1, 93):
                record = route(phase, selected, candidates, scores, ubatch)
                record["layer"] = layer
                value["routes"].append(record)
        value["observer_stats"] = {
            "ubatches": 2,
            "layers": len(value["routes"]),
            "copy_bytes": len(value["routes"])*(16*8 + 17*12),
            "explicit_synchronizations": 2,
            "failures": 0,
        }
        validate_decision_capture(validate_capture(value), case)
        value["sampling"]["teacher_forced"] = False
        validate_decision_capture(validate_capture(value), case)
        value["sampling"]["teacher_forced"] = True
        with self.assertRaises(ReplayError):
            validate_decision_capture(validate_capture(value), case)
        value["sampling"]["teacher_forced"] = False
        invalid = copy.deepcopy(value)
        invalid["observer_stats"]["copy_bytes"] -= 1
        with self.assertRaises(ReplayError):
            validate_decision_capture(validate_capture(invalid), case)

    def test_exact_point_and_one_load_opportunity(self):
        value = capture()
        exact = replay_point(value, 128, 0, 16, 17, 0, 1.0, {"DECODE"})
        self.assertEqual(exact["provider_loads_avoided"], 0)
        self.assertEqual(exact["swaps"], 0)

        changed = replay_point(value, 128, 0, 16, 17, 1, 0.002, {"DECODE"})
        self.assertEqual(changed["swaps"], 1)
        self.assertEqual(changed["provider_loads_avoided"], 1)
        self.assertEqual(changed["backing_store_bytes_avoided"], 128)
        self.assertEqual(changed["candidate_boundary_swaps"], 1)
        self.assertEqual(changed["warmup_route_decisions"], 1)
        self.assertEqual(changed["route_decisions"], 1)
        self.assertEqual(changed["baseline"]["misses"], 1)
        self.assertEqual(changed["baseline"]["miss_ratio"], 1/16)
        self.assertEqual(changed["cache_aware"]["hit_ratio"], 1.0)
        self.assertEqual(changed["baseline"]["backing_store_bytes_per_token"], 128)

    def test_real_route_comparison_uses_actual_generated_streams(self):
        exact = capture()
        changed = copy.deepcopy(exact)
        changed["generated_ids"] = [3]
        changed["cache_aware_routing"] = {
            "enabled": True,
            "candidate_count": 17,
            "capacity_slots": 16,
            "max_swaps": 1,
            "max_score_regret": 0.002,
            "prefill_rerouting": False,
        }
        changed["routes"][1]["selected_experts"][15] = 16
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            exact_path = temporary / "exact.json"
            changed_path = temporary / "changed.json"
            exact_path.write_text(json.dumps(exact))
            changed_path.write_text(json.dumps(changed))
            result = compare_route_streams(exact_path, changed_path, 16, 128)
        self.assertEqual(result["routing"]["common_generated_prefix_tokens"], 0)
        self.assertEqual(result["routing"]["intentional_decode_decisions"], 1)
        self.assertEqual(result["decode_comparison"]["backing_loads_avoided"], 1)
        self.assertEqual(result["decode_comparison"]["backing_store_bytes_avoided"], 128)
        self.assertEqual(
            result["decode_comparison"]["backing_store_bytes_avoided_per_routed_decode_token"],
            128)
        schema = json.loads((
            ROOT / "schemas/phase13/real-route-comparison-v1.schema.json").read_text())
        jsonschema.Draft7Validator(schema).validate(result)

    def test_prefill_warms_but_does_not_dilute_decode_gate(self):
        value = capture()
        value["routes"] = value["routes"][:1]*100 + value["routes"][1:]
        for ubatch, record in enumerate(value["routes"]):
            record = copy.deepcopy(record)
            record["ubatch_ordinal"] = ubatch
            value["routes"][ubatch] = record
        changed = replay_point(value, 128, 0, 16, 17, 1, 0.002, {"DECODE"})
        self.assertEqual(changed["warmup_route_decisions"], 100)
        self.assertEqual(changed["route_decisions"], 1)
        self.assertEqual(changed["provider_loads_avoided"], 1)
        self.assertEqual(changed["backing_store_byte_reduction_fraction"], 1.0)

    def test_retained_default_sweep_includes_required_upper_bounds(self):
        self.assertEqual(
            [int(value) for value in DEFAULT_MAX_SWAPS.split(",")],
            [0, 1, 2, 4, 8, 16])

    def test_observed_gap_gate_is_positive_and_repeatable(self):
        kwargs = dict(
            capture=capture(), bundle_bytes=GIB, capacities_gib=[16], hot_capacity_gib=0,
            candidate_counts=[16, 17], max_swaps_values=[0, 1, 2],
            reroute_phases={"DECODE"}, material_reduction=0.05)
        first = run_replay(**kwargs)
        second = run_replay(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["disposition"], "positive-frontier")
        self.assertGreater(first["gate"]["qualifying_points"], 0)
        self.assertEqual(first["configuration"]["threshold_source"],
                         "observed_topk_boundary_gap_quantiles")
        outcomes = [(point["candidate_count"], point["per_swap_regret"]["cumulative"],
                     point["backing_store_bytes_avoided"], point["swaps"],
                     point["changed_route_decisions"]) for point in first["frontier"]]
        self.assertEqual(len(outcomes), len(set(outcomes)))

    def test_replay_rejects_nonfinite_or_silently_clamped_capacity(self):
        kwargs = dict(
            capture=capture(), bundle_bytes=GIB, capacities_gib=[16],
            candidate_counts=[16, 17], max_swaps_values=[0, 1],
            reroute_phases={"DECODE"}, material_reduction=0.05)
        for capacities, hot in (([math.nan], 0), ([16], math.nan), ([16], 17)):
            with self.assertRaises(ReplayError):
                run_replay(capacities_gib=capacities, hot_capacity_gib=hot, **{
                    name: value for name, value in kwargs.items() if name != "capacities_gib"})

    def test_committed_schemas_accept_capture_and_frontier(self):
        capture_schema = json.loads((
            ROOT / "schemas/phase13/exact-topm-capture-v1.schema.json").read_text())
        frontier_schema = json.loads((
            ROOT / "schemas/phase13/offline-routing-frontier-v1.schema.json").read_text())
        value = capture()
        jsonschema.Draft7Validator(capture_schema).validate(value)
        frontier = run_replay(
            capture=value, bundle_bytes=GIB, capacities_gib=[16], hot_capacity_gib=0,
            candidate_counts=[16, 17], max_swaps_values=[0, 1],
            reroute_phases={"DECODE"}, material_reduction=0.05)
        jsonschema.Draft7Validator(frontier_schema).validate(frontier)

    def test_committed_corpus_is_valid_and_pins_issue73_workload(self):
        corpus_schema = json.loads((
            ROOT / "schemas/phase13/routing-corpus-v1.schema.json").read_text())
        corpus = json.loads((
            ROOT / "corpus/phase13/issue73-decision-v1.json").read_text())
        jsonschema.Draft7Validator(corpus_schema).validate(corpus)
        self.assertEqual(len(corpus["cases"]), 1)
        case = corpus["cases"][0]
        self.assertTrue(case["decision_driving"])
        self.assertEqual(case["expected_prompt_tokens"], 100)
        self.assertEqual(case["max_generate"], 24)
        self.assertEqual(case["candidate_count"], 32)
        self.assertEqual(case["n_ubatch"], 4)
        self.assertEqual(len(case["expected_generated_ids"]), case["max_generate"])
        source = json.loads((ROOT / case["source"]).read_text())
        self.assertEqual(case["source_case"], "CPU_CONTROL")
        source = source["cases"][case["source_case"]]
        source_prompts = []
        source_generated = []
        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get("generated_ids"), list):
                    source_generated.append(value["generated_ids"])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for index, child in enumerate(value[:-1]):
                    if child == "--prompt":
                        source_prompts.append(value[index + 1])
                for child in value:
                    visit(child)
        visit(source)
        self.assertIn(case["prompt"], source_prompts)
        self.assertIn(case["expected_generated_ids"], source_generated)
        loaded, digest = load_case(
            ROOT / "corpus/phase13/issue73-decision-v1.json", "issue73-decision-prompt")
        self.assertEqual(loaded, case)
        self.assertEqual(digest, "34c6a2f4a5fe551c79a23b72898abb12796c33f03997c421d75e406d7a7aa5bd")

    def test_quality_corpus_is_valid_bounded_and_heterogeneous(self):
        corpus_schema = json.loads((
            ROOT / "schemas/phase13/quality-corpus-v1.schema.json").read_text())
        corpus = json.loads((ROOT / "corpus/phase13/quality-v1.json").read_text())
        jsonschema.Draft7Validator(corpus_schema).validate(corpus)
        categories = {case["category"] for case in corpus["cases"]}
        self.assertEqual(categories, {"measurement", "reasoning", "code", "multilingual"})
        self.assertEqual(sum(case["decision_driving"] for case in corpus["cases"]), 1)
        self.assertLessEqual(sum(case["max_generate"] for case in corpus["cases"]), 42)


if __name__ == "__main__":
    unittest.main()

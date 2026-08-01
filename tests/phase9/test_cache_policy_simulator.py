from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from cache_policy_simulator import (  # noqa: E402
    Key,
    Policy,
    ReplayError,
    canonical_json,
    replay,
    validate_config,
    verify_online_capture,
    waste_hierarchy,
    waste_victims,
)


def config(policy="LRU", scope="GLOBAL", admission="ALWAYS", ratio=0, window=0, aging=0):
    return {
        "schema_version": "cache-policy-config-v1",
        "policy": policy,
        "scope": scope,
        "slru_protected_ratio_bps": ratio,
        "admission": admission,
        "admission_window_events": window,
        "lfu_aging_interval_events": aging,
    }


def fixture() -> dict:
    return json.loads((ROOT / "tests/fixtures/phase9/cache-policy-replay-input-v1.json").read_text())


class CachePolicySimulatorTests(unittest.TestCase):
    def test_config_validation_matrix(self):
        legal = [
            (config(), "HOT"),
            (config("LFRU", "PER_LAYER"), "COLD"),
            (config("SLRU", ratio=5000), "COLD"),
            (config("SLRU", admission="FREQUENCY_WINDOW", ratio=7500, window=1024), "HOT"),
            (config("LFU_AGING", aging=4096), "HOT"),
        ]
        for value, tier in legal:
            self.assertGreater(validate_config(value, tier)["digest"], 0)
        illegal = [
            (config("LRU", ratio=1), "HOT"),
            (config("SLRU", ratio=999), "HOT"),
            (config("SLRU", admission="FREQUENCY_WINDOW", ratio=7500, window=63), "HOT"),
            (config("SLRU", admission="FREQUENCY_WINDOW", ratio=7500, window=64), "COLD"),
            (config("LFU_AGING", aging=65), "HOT"),
        ]
        for value, tier in illegal:
            with self.assertRaises(ReplayError):
                validate_config(value, tier)

    def test_hand_computed_lru_fixture_and_repeatability(self):
        value = fixture()
        first = replay(value)
        second = replay(copy.deepcopy(value))
        self.assertEqual(canonical_json(first), canonical_json(second))
        self.assertEqual(first["summary"], {
            "logical_requests": 2,
            "hot_hits": 0,
            "cold_hits": 0,
            "backing_store_hits": 2,
            "hot_bytes": 0,
            "cold_bytes": 0,
            "backing_store_bytes": 128,
        })
        self.assertEqual(first["tiers"]["hot"]["final_digest"], 9019775689849355000)
        self.assertEqual(first["tiers"]["cold"]["resident"], [
            {"slot": 0, "layer": 0, "expert": 0, "generation": 1},
            {"slot": 1, "layer": 0, "expert": 1, "generation": 1},
        ])

    def test_hand_computed_candidate_policy_decisions(self):
        topology = {
            "routed_layers": [0],
            "experts_per_layer": 8,
            "physical_slot_footprint_bytes": 128,
        }

        def loaded_policy(value, slots=2):
            policy = Policy(value, "HOT", topology, slots, 256, True)
            policy.request_begin()
            for slot in range(slots):
                policy.demand(Key(0, slot), 1, 64, 128)
                policy.load_begin(slot, 1, Key(0, slot), 64)
                policy.load_complete(slot, 1)
            return policy

        lfru = loaded_policy(config("LFRU"))
        for _ in range(3):
            lfru.demand(Key(0, 0), 1, 64, 128)
            lfru.hit(0)
        lfru.demand(Key(0, 2), 1, 64, 128)
        self.assertEqual(lfru.select(Key(0, 2), 64), (1, False))

        slru = loaded_policy(config("SLRU", ratio=5000))
        slru.set_phase("DECODE")
        slru.demand(Key(0, 0), 1, 64, 128)
        slru.hit(0)
        slru.demand(Key(0, 2), 1, 64, 128)
        self.assertEqual(slru.select(Key(0, 2), 64), (1, False))

        lfu = loaded_policy(config("LFU_AGING", aging=64))
        for _ in range(3):
            lfu.demand(Key(0, 0), 1, 64, 128)
            lfu.hit(0)
        lfu.demand(Key(0, 2), 1, 64, 128)
        self.assertEqual(lfu.select(Key(0, 2), 64), (1, False))

        gated = loaded_policy(config(
            "SLRU", admission="FREQUENCY_WINDOW", ratio=5000, window=64), slots=1)
        gated.demand(Key(0, 1), 1, 64, 128)
        self.assertEqual(gated.optional_select(Key(0, 1), 64, "OPTIONAL_BACKGROUND"), (None, False))
        self.assertTrue(gated.slots[0].resident)
        gated.demand(Key(0, 1), 1, 64, 128)
        self.assertEqual(gated.optional_select(Key(0, 1), 64, "OPTIONAL_BACKGROUND"), (0, False))

    def test_hand_computed_shuffled_terminal_order(self):
        topology = {
            "routed_layers": [0],
            "experts_per_layer": 4,
            "physical_slot_footprint_bytes": 128,
        }

        def run(shuffled):
            policy = Policy(config(), "HOT", topology, 2, 64, True)
            policy.request_begin()
            for slot in range(2):
                policy.demand(Key(0, slot), 1, 64, 128)
                policy.load_begin(slot, 1, Key(0, slot), 64)
            if shuffled:
                before = len(policy.events)
                policy.load_complete(1, 1)
                self.assertEqual(len(policy.events), before)
                policy.load_complete(0, 1)
            else:
                policy.load_complete(0, 1)
                policy.load_complete(1, 1)
            return canonical_json(policy.events), policy.state_digest()

        self.assertEqual(run(False), run(True))

    def test_strict_canonical_rejections(self):
        mutations = []
        value = fixture()
        extra = copy.deepcopy(value)
        extra["unexpected"] = True
        mutations.append(extra)
        duplicate = copy.deepcopy(value)
        duplicate["requests"][0]["checkpoints"][0]["demands"].append(
            copy.deepcopy(duplicate["requests"][0]["checkpoints"][0]["demands"][0])
        )
        mutations.append(duplicate)
        reversed_keys = copy.deepcopy(value)
        reversed_keys["requests"][0]["checkpoints"][0]["demands"].reverse()
        mutations.append(reversed_keys)
        skipped = copy.deepcopy(value)
        skipped["requests"][0]["checkpoints"][0]["checkpoint_ordinal"] = 2
        mutations.append(skipped)
        identity = copy.deepcopy(value)
        identity["requests"][0]["checkpoints"][0]["demands"][0]["layer"] = 99
        mutations.append(identity)
        for mutation in mutations:
            with self.assertRaises(ReplayError):
                replay(mutation)

    def test_schema_accepts_fixture_and_output(self):
        schema_path = ROOT / "schemas/phase9/cache-policy-replay-v1.schema.json"
        schema = json.loads(schema_path.read_text())
        config_schema = json.loads((ROOT / "schemas/phase9/cache-policy-config-v1.schema.json").read_text())
        resolver = jsonschema.RefResolver(
            schema_path.as_uri(), schema,
            store={
                (ROOT / "schemas/phase9/cache-policy-config-v1.schema.json").as_uri():
                    config_schema,
                config_schema["$id"]: config_schema,
            },
        )
        jsonschema.validate(fixture(), schema, resolver=resolver)
        jsonschema.validate(replay(fixture()), schema, resolver=resolver)

    def test_waste_pinned_sampling_is_repeatable(self):
        sequence = [Key(0, value) for value in (0, 1, 2, 3, 0, 4, 1, 5, 0, 6, 1, 7)]
        for policy in ("waste_sampled_lru", "waste_sampled_lfru"):
            first = waste_victims(sequence, 3, policy)
            self.assertEqual(first, waste_victims(sequence, 3, policy))
            self.assertEqual(first["hits"] + first["misses"], len(sequence))
            hierarchy = waste_hierarchy([(key, 64, "DECODE") for key in sequence], 2, 4, policy)
            self.assertEqual(hierarchy, waste_hierarchy([(key, 64, "DECODE") for key in sequence], 2, 4, policy))
            self.assertEqual(hierarchy["summary"]["logical_requests"], len(sequence))

    def test_native_python_metamorphic_matrix(self):
        native = ROOT / "llama.cpp/build-cpu/bin/phase9-cache-replay"
        if not native.exists():
            self.skipTest("native phase9-cache-replay target is not built")
        configurations = [
            (config(), config()),
            (config("LRU", "PER_LAYER"), config("LRU", "PER_LAYER")),
            (config("LFRU"), config("LFRU")),
            (config("LFRU", "PER_LAYER"), config("LFRU", "PER_LAYER")),
            (config("SLRU", ratio=5000), config("SLRU", ratio=5000)),
            (config("SLRU", "PER_LAYER", ratio=8750), config("SLRU", "PER_LAYER", ratio=8750)),
            (config("SLRU", admission="FREQUENCY_WINDOW", ratio=7500, window=64), config("SLRU", ratio=7500)),
            (config("LFU_AGING", aging=64), config("LFU_AGING", aging=64)),
            (config("LFU_AGING", "PER_LAYER", aging=64), config("LFU_AGING", "PER_LAYER", aging=64)),
        ]
        generator = random.Random(7741)
        requests = []
        for request_ordinal in range(1, 3):
            checkpoints = []
            for checkpoint_ordinal in range(1, 25):
                keys = sorted(set((generator.randrange(3), generator.randrange(6)) for _ in range(generator.randrange(1, 5))))
                demands = [{
                    "layer": layer,
                    "expert": expert,
                    "occurrence_count": generator.randrange(1, 5),
                    "logical_payload_bytes": 80 + layer * 10 + expert,
                    "hot_admission": generator.choice([
                        "MANDATORY_CURRENT_OUTPUT", "OPTIONAL_CPU_SERVED", "OPTIONAL_BACKGROUND",
                    ]),
                } for layer, expert in keys]
                checkpoints.append({
                    "checkpoint_ordinal": checkpoint_ordinal,
                    "ubatch_ordinal": (checkpoint_ordinal - 1) // 3,
                    "phase": "PREFILL" if checkpoint_ordinal < 7 else "DECODE",
                    "demands": demands,
                })
            requests.append({"request_ordinal": request_ordinal, "checkpoints": checkpoints, "outcome": "SUCCESS"})
        for hot, cold in configurations:
            value = {
                "schema_version": "cache-policy-replay-input-v1",
                "topology": {"routed_layers": [0, 1, 2], "experts_per_layer": 6, "physical_slot_footprint_bytes": 128},
                "hot": {"slots": 9, "config": hot},
                "cold": {"slots": 15, "config": cold},
                "requests": requests,
            }
            expected = canonical_json(replay(value))
            with tempfile.TemporaryDirectory() as directory:
                input_path = Path(directory) / "input.json"
                output_path = Path(directory) / "output.json"
                input_path.write_text(canonical_json(value))
                completed = subprocess.run(
                    [str(native), "--input", str(input_path), "--output", str(output_path)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(output_path.read_text(), expected)

    def test_online_event_stream_replays_in_python_and_native(self):
        value = fixture()
        output = replay(value)
        capture = {
            "schema_version": "phase9-online-policy-capture-v1",
            "status": "pass",
            "mode": "cold",
            "background": False,
            "prompt_ids": [1],
            "generated_ids": [2],
            "logits_fnv64": [3],
            "topology": {
                "routed_layers": value["topology"]["routed_layers"],
                "experts_per_layer": value["topology"]["experts_per_layer"],
                "hot_physical_slot_footprint_bytes": value["topology"]["physical_slot_footprint_bytes"],
                "cold_physical_slot_footprint_bytes": value["topology"]["physical_slot_footprint_bytes"],
            },
            "capacities": {
                "hot_effective_slots": value["hot"]["slots"],
                "cold_effective_slots": value["cold"]["slots"],
            },
        }
        for tier in ("hot", "cold"):
            config_value = copy.deepcopy(value[tier]["config"])
            config_value["digest"] = output["tiers"][tier]["config_digest"]
            capture[tier] = {
                "config": config_value,
                "events": output["tiers"][tier]["events"],
                "diagnostics": {
                    "events": output["tiers"][tier]["counters"]["events"],
                    "state_digest": output["tiers"][tier]["final_digest"],
                },
            }
        verified = verify_online_capture(capture)
        self.assertEqual(verified["status"], "pass")
        partial = copy.deepcopy(capture)
        partial["hot"]["events"].pop()
        partial["hot"]["diagnostics"]["events"] = len(partial["hot"]["events"])
        partial["hot"]["diagnostics"]["state_digest"] = partial["hot"]["events"][-1]["state_digest"]
        with self.assertRaises(ReplayError):
            verify_online_capture(partial)
        native = ROOT / "llama.cpp/build-cpu/bin/phase9-cache-replay"
        if not native.exists():
            self.skipTest("native phase9-cache-replay target is not built")
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "capture.json"
            output_path = Path(directory) / "verified.json"
            input_path.write_text(canonical_json(capture))
            completed = subprocess.run(
                [str(native), "--capture-input", str(input_path), "--output", str(output_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(output_path.read_text())["status"], "pass")
            input_path.write_text(canonical_json(partial))
            completed = subprocess.run(
                [str(native), "--capture-input", str(input_path), "--output", str(output_path)],
                capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()

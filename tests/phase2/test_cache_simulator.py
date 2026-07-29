from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase2"))

from cache_simulator import (  # noqa: E402
    Capacity,
    ExpertKey,
    ExpertRequest,
    SimulationError,
    canonical_json,
    requests_from_trace,
    reuse_distances,
    simulate_manifest,
    simulate_policy,
)


REFERENCE = json.loads(
    (ROOT / "tests/fixtures/phase2/cache-simulator-reference-v1.json").read_text()
)
COST_MODEL = {
    "overlap_model": "serial_no_overlap",
    "tiers": {
        "hot": {"fixed_latency_us": 1, "bandwidth_bytes_per_second": 1_000_000},
        "cold": {"fixed_latency_us": 5, "bandwidth_bytes_per_second": 1_000_000},
        "backing_store": {
            "fixed_latency_us": 10,
            "bandwidth_bytes_per_second": 1_000_000,
        },
    },
}


def make_requests(
    sequence: list[int], sizes: dict[int, int] | None = None, phases: list[str] | None = None
) -> list[ExpertRequest]:
    sizes = sizes or {}
    phases = phases or ["DECODE"] * len(sequence)
    return [
        ExpertRequest(ExpertKey(1, expert), sizes.get(expert, 1), phases[index])
        for index, expert in enumerate(sequence)
    ]


def capacity(value: dict[str, int]) -> Capacity:
    return Capacity(slots=value["slots"], bytes=value["bytes"])


class CacheSimulatorTests(unittest.TestCase):
    def test_lru_hand_checkable_capacities(self) -> None:
        for case in REFERENCE["cases"]:
            with self.subTest(case=case["name"]):
                result = simulate_policy(
                    make_requests(case["sequence"]),
                    capacity(case["hot"]),
                    capacity(case["cold"]),
                    COST_MODEL,
                    "lru",
                )
                expected = case["expected_lru"]
                self.assertEqual(result["overall"]["tiers"]["hot"]["hits"], expected["hot_hits"])
                self.assertEqual(result["overall"]["tiers"]["cold"]["hits"], expected["cold_hits"])
                self.assertEqual(
                    result["overall"]["tiers"]["backing_store"]["requests"],
                    expected["backing_requests"],
                )

    def test_belady_min_reference(self) -> None:
        case = REFERENCE["belady_reference"]
        requests = make_requests(case["sequence"])
        lru = simulate_policy(
            requests, capacity(case["hot"]), capacity(case["cold"]), COST_MODEL, "lru"
        )
        oracle = simulate_policy(
            requests, capacity(case["hot"]), capacity(case["cold"]), COST_MODEL, "belady_min"
        )
        self.assertEqual(
            lru["overall"]["tiers"]["backing_store"]["requests"],
            case["expected_lru_backing_requests"],
        )
        self.assertEqual(
            oracle["overall"]["tiers"]["backing_store"]["requests"],
            case["expected_belady_backing_requests"],
        )

        admission_case = REFERENCE["belady_admission_reference"]
        admission_oracle = simulate_policy(
            make_requests(admission_case["sequence"]),
            capacity(admission_case["hot"]),
            capacity(admission_case["cold"]),
            COST_MODEL,
            "belady_min",
        )
        self.assertEqual(
            admission_oracle["overall"]["tiers"]["backing_store"]["requests"],
            admission_case["expected_belady_backing_requests"],
        )
        self.assertEqual(
            admission_oracle["overall"]["cache_activity"]["cold"]["admissions"],
            admission_case["expected_belady_cold_admissions"],
        )

    def test_unequal_byte_capacity_lru_and_oracle_rejection(self) -> None:
        case = REFERENCE["unequal_byte_reference"]
        sizes = {int(key): value for key, value in case["sizes"].items()}
        requests = make_requests(case["sequence"], sizes)
        result = simulate_policy(
            requests, capacity(case["hot"]), capacity(case["cold"]), COST_MODEL, "lru"
        )
        self.assertEqual(
            result["overall"]["tiers"]["backing_store"]["requests"],
            case["expected_lru_backing_requests"],
        )
        self.assertEqual(
            result["overall"]["cache_activity"]["cold"]["evictions"],
            case["expected_cold_evictions"],
        )
        with self.assertRaisesRegex(SimulationError, "equal-sized"):
            simulate_policy(
                requests,
                capacity(case["hot"]),
                capacity(case["cold"]),
                COST_MODEL,
                "belady_min",
            )

    def test_prefill_decode_separation_and_serial_stall(self) -> None:
        requests = make_requests([0, 0], {0: 100}, ["PREFILL", "DECODE"])
        result = simulate_policy(
            requests, Capacity(1, 100), Capacity(1, 100), COST_MODEL, "lru"
        )
        self.assertEqual(result["by_phase"]["PREFILL"]["tiers"]["backing_store"]["requests"], 1)
        self.assertEqual(result["by_phase"]["DECODE"]["tiers"]["hot"]["hits"], 1)
        self.assertEqual(result["by_phase"]["PREFILL"]["theoretical_stall"]["total"], 110)
        self.assertEqual(result["by_phase"]["DECODE"]["theoretical_stall"]["total"], 101)
        self.assertEqual(result["overall"]["theoretical_stall"]["total"], 211)
        self.assertEqual(result["overall"]["tiers"]["hot"]["bytes_requested"], 200)
        self.assertEqual(result["overall"]["tiers"]["hot"]["bytes_transferred"], 100)
        self.assertEqual(result["overall"]["tiers"]["cold"]["bytes_requested"], 100)
        self.assertEqual(result["overall"]["tiers"]["backing_store"]["bytes_transferred"], 100)

    def test_reuse_distance_and_layer_skew(self) -> None:
        requests = make_requests([0, 1, 2, 0, 0])
        self.assertEqual(reuse_distances(requests), [None, None, None, 2, 0])
        result = simulate_policy(requests, Capacity(0, 0), Capacity(3, 3), COST_MODEL, "lru")
        self.assertEqual(
            result["overall"]["reuse_distance"]["histogram"],
            {"cold": 3, "0": 1, "2": 1},
        )
        self.assertEqual(
            result["overall"]["per_layer_expert_skew"]["1"]["experts"],
            {"0": 3, "1": 1, "2": 1},
        )

    def test_trace_flattening_preserves_top_k_order_and_identity(self) -> None:
        trace = {
            "header": {
                "model_name": "fixture.gguf",
                "model_size": 10,
                "model_sha256": "a" * 64,
                "model_source_revision": "b" * 40,
                "published_gguf_revision": "c" * 40,
            },
            "records": [
                {"phase": "PREFILL", "layer": 1, "selected_experts": [1, 0]},
                {"phase": "DECODE", "layer": 1, "selected_experts": [0, 1]},
            ],
        }
        storage_map = {
            "schema_version": "expert-storage-map-v1",
            "model": {
                "name": "fixture.gguf",
                "size": 10,
                "sha256": "a" * 64,
                "source_revision": "b" * 40,
                "published_gguf_revision": "c" * 40,
            },
            "entries": [
                {"layer": 1, "expert_id": 0, "atomic_bundle_bytes": 10},
                {"layer": 1, "expert_id": 1, "atomic_bundle_bytes": 10},
            ],
        }
        requests = requests_from_trace(trace, storage_map)
        self.assertEqual([request.key.expert_id for request in requests], [1, 0, 0, 1])
        self.assertEqual([request.phase for request in requests], ["PREFILL"] * 2 + ["DECODE"] * 2)

    def test_manifest_output_is_deterministic(self) -> None:
        trace = {
            "header": {
                "schema_version": 1,
                "model_name": "fixture.gguf",
                "model_size": 10,
                "model_sha256": "a" * 64,
                "model_source_revision": "b" * 40,
                "published_gguf_revision": "c" * 40,
                "llama_cpp_revision": "d" * 40,
                "run_id": "fixture",
            },
            "records": [{"phase": "DECODE", "layer": 1, "selected_experts": [0]}],
        }
        storage_map = {
            "schema_version": "expert-storage-map-v1",
            "model": {
                "name": "fixture.gguf",
                "size": 10,
                "sha256": "a" * 64,
                "source_revision": "b" * 40,
                "published_gguf_revision": "c" * 40,
                "llama_cpp_revision": "e" * 40,
            },
            "entries": [{"layer": 1, "expert_id": 0, "atomic_bundle_bytes": 10}],
        }
        manifest = {
            "schema_version": "phase2-simulation-manifest-v1",
            "description": (
                "Synthetic deterministic unit-test costs; not measured hardware latency."
            ),
            "cost_model": COST_MODEL,
            "scenarios": [
                {
                    "name": "one",
                    "hot_capacity": {"slots": 1, "bytes": 10},
                    "cold_capacity": {"slots": 1, "bytes": 10},
                }
            ],
        }
        first = simulate_manifest(trace, storage_map, manifest, "1" * 64, "2" * 64, "3" * 64)
        second = simulate_manifest(trace, storage_map, manifest, "1" * 64, "2" * 64, "3" * 64)
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_simulator_imports_no_ggml_or_cuda_dependency(self) -> None:
        allowed_local = {"cache_simulator", "route_trace"}
        for path in (
            ROOT / "scripts/phase2/cache_simulator.py",
            ROOT / "scripts/phase2/run_cache_simulation.py",
        ):
            tree = ast.parse(path.read_text())
            imported = {
                node.names[0].name.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
            }
            imported.update(
                node.module.split(".", 1)[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            )
            unexpected = imported - sys.stdlib_module_names - allowed_local - {"__future__"}
            self.assertEqual(unexpected, set(), f"unexpected dependency in {path}: {unexpected}")


if __name__ == "__main__":
    unittest.main()

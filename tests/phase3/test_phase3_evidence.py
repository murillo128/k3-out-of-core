import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = ROOT / "scripts/phase3"
sys.path.insert(0, str(SCRIPT_ROOT))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_ROOT / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase3EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load_script("common")
        cls.overhead = load_script("measure_provider_overhead")

    def test_immutable_inputs(self):
        self.assertEqual(self.common.PROJECT_BASE, "81df862da6e4ff9db005f6265470070bb5456f4c")
        self.assertEqual(self.common.LLAMA_BASE, "4daaaa1a4dd26d6465f84891b854b5f7ddc03020")
        self.assertEqual(set(self.common.MODELS), {"f16", "mxfp4"})

    def test_throughput_pairing_and_bound(self):
        runs = []
        for ordinal, side in enumerate(self.overhead.ABBA*5):
            runs.append({
                "run_ordinal": ordinal,
                "side": side,
                "metric": {"value": 100.0},
            })
        analysis = self.overhead.analyze(runs, "value", 0.01, False)
        self.assertEqual(len(analysis["pairs"]), 10)
        self.assertEqual(analysis["one_sided_95_percent_upper_bound"], 0.0)
        self.assertTrue(analysis["passed"])

    def test_latency_slowdown_direction(self):
        runs = []
        for ordinal, side in enumerate(self.overhead.ABBA*5):
            runs.append({
                "run_ordinal": ordinal,
                "side": side,
                "metric": {"value": 1.0 if side == "a" else 1.1},
            })
        analysis = self.overhead.analyze(runs, "value", 0.11, True)
        self.assertAlmostEqual(analysis["paired_mean_relative_b_slowdown"], 0.1)
        self.assertTrue(analysis["passed"])

    def test_manifest_schema_is_valid(self):
        schema = json.loads((ROOT / "schemas/phase3/phase3-manifest-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)


if __name__ == "__main__":
    unittest.main()

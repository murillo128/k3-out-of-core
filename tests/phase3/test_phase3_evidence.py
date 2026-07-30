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
        cls.verifier = load_script("verify_phase3")

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

    def test_verifier_rejects_missing_raw_telemetry(self):
        errors = []
        self.verifier.validate_performance_sample({
            "label": "isolated-baseline", "metric": {},
            "provider_counter_availability": "unavailable-pinned-baseline",
        }, errors)
        self.assertTrue(any("required telemetry" in error for error in errors))

    def test_verifier_accepts_explicit_baseline_provider_absence(self):
        metric = {name: 1.0 for name in self.verifier.REQUIRED_PERFORMANCE_TELEMETRY}
        metric.update({name: None for name in self.verifier.PROVIDER_COUNTERS})
        errors = []
        self.verifier.validate_performance_sample({
            "label": "isolated-baseline", "metric": metric,
            "provider_counter_availability": "unavailable-pinned-baseline",
        }, errors)
        self.assertEqual(errors, [])

    def test_verifier_accepts_only_approved_standing_capture_contract(self):
        overhead = {
            "validation_contract": {
                "rule": self.verifier.FINAL_CAPTURE_RULE,
                "approval_comment_id": self.verifier.FINAL_CAPTURE_APPROVAL_COMMENT_ID,
                "complete_capture_count": 1,
                "retry_or_cross_attempt_selection": "forbidden",
                "result_stands": True,
            },
        }
        errors = []
        self.verifier.validate_final_capture_contract(overhead, errors)
        self.assertEqual(errors, [])

        overhead["composition"] = {"selections": []}
        errors = []
        self.verifier.validate_final_capture_contract(overhead, errors)
        self.assertTrue(any("forbidden cross-attempt composition" in error for error in errors))

    def test_verifier_rejects_unapproved_capture_contract(self):
        errors = []
        self.verifier.validate_final_capture_contract({}, errors)
        self.assertTrue(any("standing final-capture contract" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

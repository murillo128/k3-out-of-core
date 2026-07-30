import copy
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
        cls.disposition = load_script("phase3_disposition")
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
        v1 = json.loads((ROOT / "schemas/phase3/phase3-manifest-v1.schema.json").read_text())
        v2 = json.loads((ROOT / "schemas/phase3/phase3-manifest-v2.schema.json").read_text())
        Draft202012Validator.check_schema(v1)
        Draft202012Validator.check_schema(v2)
        self.assertIn("performance-gate-failed", v1["properties"]["closeout_state"]["enum"])
        self.assertNotIn("accepted-with-notes", v1["properties"]["closeout_state"]["enum"])
        self.assertEqual(v2["properties"]["raw_performance_gate"]["properties"]["status"]["const"], "fail")
        self.assertEqual(
            v2["properties"]["closeout_state"]["enum"],
            ["checkpoint-b-candidate-with-performance-notes", "complete-with-performance-notes"],
        )

    def test_disposition_is_derived_from_immutable_capture(self):
        disposition = self.disposition.derive_disposition(ROOT)
        schema = json.loads((ROOT / "schemas/phase3/phase3-disposition-v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(disposition)
        self.assertEqual(disposition["raw_performance"]["status"], "fail")
        self.assertEqual(disposition["raw_performance"]["metric_cells"], {"passed": 22, "total": 24})
        self.assertEqual(disposition["raw_performance"]["waived_cells"], self.disposition.EXPECTED_WAIVED_CELLS)

    def test_disposition_rejects_broadened_or_changed_waiver(self):
        original = self.disposition.derive_disposition(ROOT)
        mutations = []
        third_cell = copy.deepcopy(original)
        third_cell["raw_performance"]["waived_cells"].append(copy.deepcopy(self.disposition.EXPECTED_WAIVED_CELLS[0]))
        mutations.append(third_cell)
        different_cell = copy.deepcopy(original)
        different_cell["raw_performance"]["waived_cells"][0]["metric"] = "decode_tokens_per_second"
        mutations.append(different_cell)
        for field, value in (
            ("original_budget", 0.03),
            ("paired_mean_slowdown", 0.01),
            ("one_sided_95_percent_upper_bound", 0.02),
        ):
            changed = copy.deepcopy(original)
            changed["raw_performance"]["waived_cells"][0][field] = value
            mutations.append(changed)
        changed_capture = copy.deepcopy(original)
        changed_capture["raw_performance"]["capture"]["sha256"] = "0" * 64
        mutations.append(changed_capture)
        changed_capture_path = copy.deepcopy(original)
        changed_capture_path["raw_performance"]["capture"]["path"] = "replacement.json"
        mutations.append(changed_capture_path)
        changed_comments = copy.deepcopy(original)
        changed_comments["design_authority_comment_ids"] = [5128658370]
        mutations.append(changed_comments)
        raw_pass = copy.deepcopy(original)
        raw_pass["raw_performance"]["status"] = "pass"
        mutations.append(raw_pass)
        baseline_failure = copy.deepcopy(original)
        baseline_failure["raw_performance"]["all_baseline_to_disabled_cells_pass"] = False
        mutations.append(baseline_failure)
        decode_failure = copy.deepcopy(original)
        decode_failure["raw_performance"]["all_decode_cells_pass"] = False
        mutations.append(decode_failure)
        for prerequisite in self.disposition.PREREQUISITE_NAMES:
            prerequisite_failure = copy.deepcopy(original)
            prerequisite_failure["prerequisites"][prerequisite] = False
            mutations.append(prerequisite_failure)
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                errors = []
                self.disposition.validate_disposition(ROOT, mutation, errors)
                self.assertTrue(errors)

    def test_checkpoint_b_schema_rejects_safety_no(self):
        schema = json.loads((ROOT / "schemas/phase3/checkpoint-b-review-v1.schema.json").read_text())
        checkpoint = {
            "schema_version": "checkpoint-b-review-v1", "repository": "murillo128/k3-out-of-core",
            "issue": 13, "execution_profile": "STANDARD", "checkpoint": "B", "comment_id": 1,
            "url": "https://github.com/murillo128/k3-out-of-core/issues/13#issuecomment-1",
            "verdict": "PASS_WITH_NOTES", "safety_to_proceed": "NO", "project_head": "1" * 40,
            "llama_cpp_head": self.disposition.LLAMA_CPP_CANDIDATE,
            "project_range": f"{self.common.PROJECT_BASE}..{'1' * 40}",
            "llama_cpp_range": f"{self.common.LLAMA_BASE}..{self.disposition.LLAMA_CPP_CANDIDATE}",
            "independent_read_only": True,
        }
        self.assertFalse(Draft202012Validator(schema).is_valid(checkpoint))

    def test_checkpoint_b_rejects_a_different_reviewed_head(self):
        errors = []
        self.verifier.validate_reviewed_project_head(ROOT, self.common.PROJECT_BASE, errors)
        self.assertTrue(any("not attestation-only" in error for error in errors))

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
                "artifact": self.verifier.FINAL_CAPTURE_NAME,
                "historical_capture": {
                    "artifact": self.verifier.HISTORICAL_CAPTURE_NAME,
                    "sha256": self.verifier.HISTORICAL_CAPTURE_SHA256,
                    "disposition": "immutable-non-authoritative-history",
                },
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

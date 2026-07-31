import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts/phase7"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase7EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load("common")
        cls.verify = load("verify_phase7")
        cls.schema = json.loads((ROOT / "schemas/phase7/phase7-manifest-v1.schema.json").read_text())

    def test_schema_and_closed_gates(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual(self.schema["properties"]["execution_profile"]["const"], "STANDARD")
        gates = self.schema["properties"]["gates"]
        self.assertFalse(gates["additionalProperties"])
        self.assertEqual(set(gates["required"]), set(gates["properties"]))
        self.assertTrue(all(value["const"] is True for value in gates["properties"].values()))

    def test_authority(self):
        self.assertEqual(self.common.PROJECT_BASE, "96b0b483c6bc0bfc2679669e5bb049081c7660ae")
        self.assertEqual(self.common.LLAMA_CANDIDATE, "b71e40f91b1a0dab578d56ac733211453704d674")
        self.assertEqual(self.common.CHECKPOINT_B_COMMENT, 5140081178)

    def test_closeout_preserves_nested_head(self):
        self.assertEqual(self.verify.ALLOWED_CLOSEOUT, {
            "docs/STATUS.md",
            "results/2026-07-31/skynet/phase7-async-runtime/phase7-manifest.json",
            "results/2026-07-31/skynet/phase7-async-runtime/verification-result.json",
        })
        self.assertNotIn("llama.cpp", self.verify.ALLOWED_CLOSEOUT)

    def test_nested_scope_excludes_phase8_and_ggml_core(self):
        self.assertFalse(any(path.startswith("ggml/") for path in self.verify.ALLOWED_NESTED))
        self.assertFalse(any("cpu-fallback" in path for path in self.verify.ALLOWED_NESTED))
        self.assertIn("src/llama-expert-async-io.cpp", self.verify.ALLOWED_NESTED)
        self.assertIn("src/llama-expert-transfer-ring.cpp", self.verify.ALLOWED_NESTED)

    def test_validation_command_set_is_exact(self):
        self.assertEqual(len(self.verify.EXPECTED_VALIDATION), 15)
        self.assertIn("ctest-cpu", self.verify.EXPECTED_VALIDATION)
        self.assertIn("ctest-cuda", self.verify.EXPECTED_VALIDATION)
        self.assertIn("ctest-asan-ubsan", self.verify.EXPECTED_VALIDATION)
        self.assertIn("ctest-tsan-aslr-disabled", self.verify.EXPECTED_VALIDATION)

    def test_false_runtime_check_rejected(self):
        checks = {"parity": True, "bounded": True, "drained": True}
        self.assertTrue(self.verify.checks_true(checks))
        altered = copy.deepcopy(checks)
        altered["drained"] = False
        self.assertFalse(self.verify.checks_true(altered))

    def test_final_review_candidate_is_nullable(self):
        options = self.schema["properties"]["final_review"]["oneOf"]
        self.assertIn({"type": "null"}, options)


if __name__ == "__main__":
    unittest.main()

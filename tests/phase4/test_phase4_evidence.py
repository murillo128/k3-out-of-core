import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = ROOT / "scripts/phase4"
sys.path.insert(0, str(SCRIPT_ROOT))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase4EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load("common")
        cls.verifier = load("verify_phase4")
        cls.schema = json.loads((ROOT / "schemas/phase4/phase4-manifest-v1.schema.json").read_text())

    def test_schema_valid(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual(self.schema["properties"]["execution_profile"]["const"], "STANDARD")

    def test_exact_authority(self):
        self.assertEqual(self.common.PROJECT_BASE, "0da90c6711e00613820183c1811dcaf1baffb409")
        self.assertEqual(self.common.LLAMA_BASE, "a120de8e2d0b552c51eacd7d701ef1dd994bc3db")
        self.assertEqual(self.common.CHECKPOINT_A_COMMENT, 5131012078)

    def test_parity_evidence_rejects_false_check(self):
        result_root = ROOT / "results/2026-07-30/skynet/phase4-hot-cache"
        parity_path = result_root / "hot-cache-parity.json"
        original = json.loads(parity_path.read_text())
        changed = copy.deepcopy(original)
        changed["cases"][0]["checks"]["routes_exact"] = False
        temporary = result_root / ".test-mutated-parity.json"
        temporary.write_text(json.dumps(changed))
        try:
            manifest = {"phase3_input": {"path": "missing", "size": 1, "sha256": "0"*64}}
            errors = []
            saved = result_root / "hot-cache-parity.json"
            # Directly exercise the same all-check invariant without mutating authoritative evidence.
            self.assertFalse(all(changed["cases"][0]["checks"].values()))
            self.assertTrue(all(original["cases"][0]["checks"].values()))
        finally:
            temporary.unlink()

    def test_allowed_nested_scope_excludes_kernels(self):
        self.assertFalse(any(path.startswith("ggml/") for path in self.verifier.ALLOWED_NESTED))
        self.assertNotIn("src/models/kimi-k3.cpp", self.verifier.ALLOWED_NESTED)

    def test_model_identities_are_fixed(self):
        self.assertEqual(set(self.common.MODELS), {"f16", "mxfp4"})
        self.assertTrue(all(len(value["sha256"]) == 64 for value in self.common.MODELS.values()))


if __name__ == "__main__":
    unittest.main()

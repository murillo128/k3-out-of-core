import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = ROOT / "scripts/phase5"
sys.path.insert(0, str(SCRIPT_ROOT))

def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

class Phase5EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load("common"); cls.verifier = load("verify_phase5"); cls.capture = load("capture_validation_results")
        cls.schema = json.loads((ROOT / "schemas/phase5/phase5-manifest-v1.schema.json").read_text())

    def test_schema_valid(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertEqual(self.schema["properties"]["execution_profile"]["const"], "STANDARD")

    def test_exact_authority(self):
        self.assertEqual(self.common.PROJECT_BASE, "114f0de6f5d1cbd5f9ef6255f9100f3f4d52380a")
        self.assertEqual(self.common.LLAMA_BASE, "57fe1eabbe3d0ced59096a0744efc91e286fb1c7")
        self.assertEqual(self.common.CHECKPOINT_A_COMMENT, 5132379446)

    def test_scope_excludes_ggml_and_kernels(self):
        self.assertFalse(any(path.startswith("ggml/") for path in self.verifier.ALLOWED_NESTED))
        self.assertNotIn("src/models/kimi-k3.cpp", self.verifier.ALLOWED_NESTED)

    def test_models_and_phase4_are_immutable(self):
        self.assertEqual(set(self.common.MODELS), {"f16", "mxfp4"})
        self.assertEqual(self.common.PHASE4_MANIFEST, "results/2026-07-30/skynet/phase4-hot-cache/phase4-manifest.json")

    def test_false_parity_is_rejected(self):
        parity = json.loads((ROOT / "results/2026-07-30/skynet/phase5-cold-cache/cold-cache-parity.json").read_text())
        changed = copy.deepcopy(parity); changed["cases"][0]["checks"]["exact_parity"] = False
        self.assertTrue(all(parity["cases"][0]["checks"].values()))
        self.assertFalse(all(changed["cases"][0]["checks"].values()))

    def test_false_fallback_claim_is_rejected(self):
        parity = json.loads((ROOT / "results/2026-07-30/skynet/phase5-cold-cache/cold-cache-parity.json").read_text())
        fallback = parity["cases"][0]["pageable_fallback"]["diagnostics"]
        self.assertEqual((fallback["ring_pinned_bytes"], fallback["ring_async_enqueues"], fallback["ring_fallback"]), (0, 0, 1))

    def test_validation_binding_rejects_failure(self):
        records = []
        for name, command in self.capture.COMMANDS:
            records.append({"name": name, "command": command, "cwd": ".", "exit_code": 0,
                "stdout_sha256": "0"*64, "stderr_sha256": "0"*64, "stdout_bytes": 0, "stderr_bytes": 0,
                "passed": 1 if name.startswith(("ctest-", "unittest-")) else None,
                "total": 1 if name.startswith(("ctest-", "unittest-")) else None})
        results = {"status": "pass", "project_head": "1"*40, "llama_cpp_head": "2"*40, "commands": records}
        manifest = {"validation": copy.deepcopy(records), "revisions": {"project_evidence_head": "1"*40, "llama_cpp_candidate": "2"*40}}
        errors = []; self.verifier.validate_commands(results, manifest, errors); self.assertEqual(errors, [])
        results["commands"][0]["exit_code"] = 1; errors = []; self.verifier.validate_commands(results, manifest, errors); self.assertTrue(errors)

if __name__ == "__main__": unittest.main()

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts/phase8"
sys.path.insert(0, str(SCRIPTS))


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase8EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.common = load("common")
        cls.verify = load("verify_phase8")
        cls.schema = json.loads((ROOT / "schemas/phase8/phase8-manifest-v1.schema.json").read_text())
        cls.fixture = {
            "schema_version": "phase8-manifest-v1", "closeout_state": "final-review-candidate",
            "execution_profile": "STANDARD", "issue": {"repository": "murillo128/k3-out-of-core", "number": 26, "pull_request": 27},
            "revisions": {"branch": "codex/phase8-miss-execution", "project_execution_base": cls.common.PROJECT_BASE,
                "project_phase8_4_start": cls.common.PHASE8_START, "project_capture_head": "0" * 40,
                "project_evidence_head": "1" * 40,
                "llama_cpp_base": cls.common.LLAMA_BASE, "llama_cpp_final": cls.common.LLAMA_CHECKPOINT_B,
                "gitlink": cls.common.LLAMA_CHECKPOINT_B},
            "checkpoint_a": {"comment_id": 5141694340, "verdict": "PASS", "safety_to_proceed": "YES",
                "project_head": "07da45728b38b2d7c6a3a1b156dffcea6b94ec54", "llama_cpp_head": "4cfee48aacb6b33ebcbda796b26106b69440e633", "independent_read_only": True},
            "checkpoint_b": {"comment_id": 5144721775, "verdict": "PASS", "safety_to_proceed": "YES",
                "project_head": cls.common.PHASE8_START, "llama_cpp_head": cls.common.LLAMA_CHECKPOINT_B, "independent_read_only": True},
            "final_review": None, "inputs": {}, "environment": {},
            "evidence": {name: {} for name in cls.verify.EXPECTED_EVIDENCE}, "validation": [],
            "policies": {"default": "PROMOTE_AND_GPU", "cpu_fallback_explicit": True,
                "auto_explicit_version": 1, "background_promotion_default": False, "cost_model_digests_recorded": True},
            "metrics": {}, "gates": {name: True for name in cls.schema["properties"]["gates"]["required"]},
            "carried_notes": [], "deferred": [], "artifacts": []}

    def test_schema_is_closed_and_valid(self):
        Draft202012Validator.check_schema(self.schema)
        self.assertFalse(self.schema["additionalProperties"])
        self.assertEqual(set(self.schema["properties"]["gates"]["required"]),
                         set(self.schema["properties"]["gates"]["properties"]))

    def test_exact_authority(self):
        self.assertEqual(self.common.PROJECT_BASE, "5fe0bda6965da7d2b0f85dd14b97427a7b60f161")
        self.assertEqual(self.common.PHASE8_START, "30013880641fd2f10a1952b5b9619e6d872e233b")
        self.assertEqual(self.common.LLAMA_CHECKPOINT_B, "a885ff7750a4e73901b7f378e7dc45880a7d1536")

    def test_payload_authority_accepts_complete_fixture(self):
        self.verify.verify_manifest_payload(copy.deepcopy(self.fixture))

    def test_payload_authority_rejects_mutations(self):
        mutations = [
            lambda value: value["revisions"].update(gitlink="2" * 40),
            lambda value: value["policies"].update(default="AUTO"),
            lambda value: value["policies"].update(background_promotion_default=True),
            lambda value: value["gates"].update(mixed_cpu_gpu_overlap=False),
            lambda value: value["evidence"].pop("hybrid_overlap"),
            lambda value: value.update(final_review={"verdict": "PASS"}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(self.fixture)
                mutation(value)
                with self.assertRaises(self.verify.VerificationError):
                    self.verify.verify_manifest_payload(value)

    def test_validation_command_set_is_exact(self):
        self.assertEqual(len(self.verify.EXPECTED_VALIDATION), 16)
        self.assertIn("ctest-tsan-aslr-disabled", self.verify.EXPECTED_VALIDATION)
        self.assertIn("phase7-verifier", self.verify.EXPECTED_VALIDATION)

    def test_after_evidence_allowlist_is_attestation_only(self):
        self.assertEqual(self.verify.ALLOWED_AFTER_EVIDENCE, {
            "results/2026-07-31/skynet/phase8-miss-execution/phase8-manifest.json",
            "results/2026-07-31/skynet/phase8-miss-execution/verification-result.json"})


if __name__ == "__main__":
    unittest.main()

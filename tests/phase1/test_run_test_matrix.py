from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/run_test_matrix.py"
SPEC = importlib.util.spec_from_file_location("run_test_matrix", SCRIPT)
assert SPEC and SPEC.loader
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


class TestMatrixTests(unittest.TestCase):
    def test_parse_lfs_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.gguf"
            path.write_text(
                matrix.LFS_HEADER + "\n" + "oid sha256:" + "a" * 64 + "\nsize 1234\n",
                encoding="utf-8",
            )
            self.assertEqual(matrix.parse_lfs_pointer(path)["payload_size_bytes"], 1234)
            path.write_bytes(b"GGUF payload")
            self.assertIsNone(matrix.parse_lfs_pointer(path))

    def test_parse_ctest_summary(self) -> None:
        self.assertEqual(
            matrix.parse_summary("100% tests passed, 0 tests failed out of 54"),
            {"percent_passed": 100, "failed": 0, "passed": 54, "total": 54},
        )

    def test_quarantine_requires_stable_success_and_pointer_signature(self) -> None:
        result = {
            "stable": {"exit_code": 0, "summary": {"failed": 0}},
            "external_fixture": {"exit_code": 8, "output": "invalid magic characters: 'vers'"},
        }
        backends = {"cpu": result, "cuda": result}
        self.assertTrue(matrix.quarantine_is_valid(backends, [{"path": "fixture"}], "external vocab test"))
        result["stable"]["exit_code"] = 8
        self.assertFalse(matrix.quarantine_is_valid(backends, [{"path": "fixture"}], "external vocab test"))

    def test_classification_names_exact_quarantine(self) -> None:
        data = {
            "fixture": {
                "remote": "https://huggingface.co/ggml-org/vocabs",
                "git_lfs": {"status": "unavailable", "reason": "git-lfs missing"},
                "pointers_after_recovery": [{"path": "x.gguf", "oid_sha256": "a" * 64, "payload_size_bytes": 1}],
            },
            "backends": {
                "cpu": {"stable": {"summary": {"passed": 54, "total": 54}}},
                "cuda": {"stable": {"summary": {"passed": 54, "total": 54}}},
            },
        }
        text = matrix.render_classification(data)
        self.assertIn("quarantined external fixture failure", text)
        self.assertIn(matrix.FIXTURE_TEST, text)


if __name__ == "__main__":
    unittest.main()

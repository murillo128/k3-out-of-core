from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/run_test_matrix.py"
SPEC = importlib.util.spec_from_file_location("run_test_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
matrix = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(matrix)


class RunTestMatrixTests(unittest.TestCase):
    def test_inspect_lfs_pointer_records_first_bytes_and_payload_contract(self) -> None:
        oid = "a" * 64
        data = (
            "version https://git-lfs.github.com/spec/v1\n"
            f"oid sha256:{oid}\n"
            "size 1234\n"
        ).encode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "type" / "fixture.gguf"
            path.parent.mkdir()
            path.write_bytes(data)
            observed = matrix.inspect_fixture_file(path, root)
        self.assertEqual(observed["state"], "git-lfs-pointer")
        self.assertEqual(observed["lfs_oid_sha256"], oid)
        self.assertEqual(observed["lfs_payload_size_bytes"], 1234)
        self.assertEqual(observed["first_96_bytes_hex"], data[:96].hex())

    def test_inspect_payload_hashes_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "type" / "fixture.gguf"
            path.parent.mkdir()
            path.write_bytes(b"GGUF payload")
            observed = matrix.inspect_fixture_file(path, root)
        self.assertEqual(observed["state"], "payload")
        self.assertEqual(observed["actual_size_bytes"], 12)

    def test_parse_ctest_summary_uses_final_summary(self) -> None:
        output = "100% tests passed, 0 tests failed out of 54\nTotal Test time = 1.00 sec"
        self.assertEqual(
            matrix.parse_ctest_summary(output),
            {"percent": 100, "failed": 0, "total": 54},
        )

    def test_parse_ctest_summary_rejects_missing_result(self) -> None:
        with self.assertRaisesRegex(matrix.MatrixError, "does not contain"):
            matrix.parse_ctest_summary("CTest did not run")

    def test_recovery_command_pins_repository_and_revision(self) -> None:
        command = matrix.recovery_command(
            ["SPM/fixture.gguf"], Path("/tmp/fixtures")
        )
        self.assertEqual(command[0], matrix.HF_PATH)
        self.assertEqual(command[1:4], ["download", "ggml-org/vocabs", "SPM/fixture.gguf"])
        self.assertIn(matrix.FIXTURE_REVISION, command)

    def test_classify_fixture_resolved_when_both_backends_pass(self) -> None:
        fixture_results = {
            "cpu": {"status": "pass"},
            "cuda": {"status": "pass"},
        }
        classification, _ = matrix.classify_fixture(
            fixture_results,
            {"files": []},
            {"status": "not-needed"},
        )
        self.assertEqual(classification, "resolved")

    def test_classify_fixture_quarantines_only_pointer_specific_failures(self) -> None:
        fixture_results = {
            "cpu": {"status": "fail", "stdout": "failed SPM/fixture.gguf", "stderr": ""},
            "cuda": {"status": "fail", "stdout": "failed SPM/fixture.gguf", "stderr": ""},
        }
        classification, _ = matrix.classify_fixture(
            fixture_results,
            {"files": [{"path": "SPM/fixture.gguf", "state": "git-lfs-pointer"}]},
            {"status": "fail"},
        )
        self.assertEqual(classification, "quarantined-external-fixture")

    def test_classify_fixture_does_not_hide_non_pointer_failure(self) -> None:
        fixture_results = {
            "cpu": {"status": "fail", "stdout": "assertion failed", "stderr": ""},
            "cuda": {"status": "fail", "stdout": "assertion failed", "stderr": ""},
        }
        classification, _ = matrix.classify_fixture(
            fixture_results,
            {"files": [{"path": "SPM/fixture.gguf", "state": "payload"}]},
            {"status": "pass"},
        )
        self.assertEqual(classification, "hard-failure")

    def test_stable_command_excludes_exactly_external_fixture(self) -> None:
        command = matrix.stable_command(Path("build-cpu"))
        self.assertEqual(
            command[command.index("-E") + 1],
            "^test-tokenizers-ggml-vocabs$",
        )
        self.assertIn("--no-tests=error", command)


if __name__ == "__main__":
    unittest.main()

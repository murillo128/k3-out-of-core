from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/verify_closeout.py"
SPEC = importlib.util.spec_from_file_location("verify_closeout", SCRIPT)
assert SPEC and SPEC.loader
verify_closeout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_closeout)


class VerifyCloseoutTests(unittest.TestCase):
    def test_checkpoint_c_is_required_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            for name in verify_closeout.EVIDENCE_FILES:
                (results / name).write_text("{}", encoding="utf-8")
            (results / "checkpoints.json").write_text(
                json.dumps({"A": {"verdict": "PASS"}, "B": {"verdict": "PASS_WITH_NOTES"}, "C": {"verdict": "PENDING"}}),
                encoding="utf-8",
            )
            errors: list[str] = []
            verify_closeout.verify_evidence(results, False, errors)
            self.assertIn("Checkpoint C is incomplete", errors)

    def test_pending_checkpoint_c_is_only_accepted_by_pre_review_set(self) -> None:
        verdict = "PENDING"
        self.assertIn(verdict, {"PASS", "PASS_WITH_NOTES", "PENDING"})
        self.assertNotIn(verdict, {"PASS", "PASS_WITH_NOTES"})

    def test_checksum_manifest_rejects_unlisted_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            (results / "evidence.sha256").write_text("0" * 64 + "  ../escape\n", encoding="utf-8")
            errors: list[str] = []
            verify_closeout.verify_checksums(results, errors)
            self.assertTrue(any("unexpected path" in error for error in errors))

    def test_write_and_verify_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory)
            with mock.patch.object(verify_closeout, "EVIDENCE_FILES", ("one.json", "two.log")):
                (results / "one.json").write_text("{}\n", encoding="utf-8")
                (results / "two.log").write_text("ok\n", encoding="utf-8")
                verify_closeout.write_checksums(results)
                errors: list[str] = []
                verify_closeout.verify_checksums(results, errors)
                self.assertEqual(errors, [])
                (results / "two.log").write_text("changed\n", encoding="utf-8")
                verify_closeout.verify_checksums(results, errors)
                self.assertIn("checksum mismatch: two.log", errors)


if __name__ == "__main__":
    unittest.main()

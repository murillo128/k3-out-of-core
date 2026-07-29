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
    def complete_attestation(self):
        head = "1" * 40
        comment_id = 123456
        verdict = "PASS_WITH_NOTES"
        checkpoints = {
            "C": {
                "verdict": verdict,
                "reviewed_head": head,
                "reviewed_range": f"{verify_closeout.CHECKPOINT_C_BASE}..{head}",
                "issue_comment_id": comment_id,
                "safety_gate": "YES",
                "note": "Independent review accepted the complete corrective state.",
            }
        }
        manifest = {
            "baseline": {"status": "phase1-validated"},
            "phase1_validation": {
                "checkpoint_c": verdict,
                "checkpoint_c_reviewed_head": head,
                "checkpoint_c_issue_comment_id": comment_id,
            },
        }
        common = (
            f"Checkpoint C: **{verdict}**\n"
            f"Checkpoint C reviewed head: `{head}`\n"
            f"https://example.invalid/#issuecomment-{comment_id}\n"
        )
        documents = {
            "docs/STATUS.md": common,
            "docs/plan/00-foundation.md": common,
            "docs/REPOSITORIES_AND_ARTIFACTS.md": common,
            "SUMMARY.md": common,
        }
        return checkpoints, manifest, documents

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

    def test_complete_checkpoint_c_attestation_passes(self) -> None:
        checkpoints, manifest, documents = self.complete_attestation()
        errors: list[str] = []
        verify_closeout.verify_checkpoint_c_attestation(checkpoints, manifest, documents, errors)
        self.assertEqual(errors, [])

    def test_incomplete_checkpoint_c_fields_fail(self) -> None:
        mutations = {
            "range": lambda c, m, d: c["C"].__setitem__("reviewed_range", "wrong"),
            "head": lambda c, m, d: c["C"].__setitem__("reviewed_head", None),
            "safety": lambda c, m, d: c["C"].__setitem__("safety_gate", None),
            "comment": lambda c, m, d: c["C"].__setitem__("issue_comment_id", None),
            "note": lambda c, m, d: c["C"].__setitem__("note", "PENDING"),
            "manifest_status": lambda c, m, d: m["baseline"].__setitem__("status", "checkpoint-c-pending"),
            "manifest_verdict": lambda c, m, d: m["phase1_validation"].__setitem__("checkpoint_c", "PENDING"),
            "manifest_head": lambda c, m, d: m["phase1_validation"].__setitem__("checkpoint_c_reviewed_head", None),
            "manifest_comment": lambda c, m, d: m["phase1_validation"].__setitem__("checkpoint_c_issue_comment_id", None),
            "summary": lambda c, m, d: d.__setitem__("SUMMARY.md", "Checkpoint C: **PENDING**"),
            "status": lambda c, m, d: d.__setitem__("docs/STATUS.md", "Checkpoint C: **PENDING**"),
            "plan": lambda c, m, d: d.__setitem__("docs/plan/00-foundation.md", "Checkpoint C: **PENDING**"),
            "repositories": lambda c, m, d: d.__setitem__("docs/REPOSITORIES_AND_ARTIFACTS.md", "Checkpoint C: **PENDING**"),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                checkpoints, manifest, documents = self.complete_attestation()
                mutation(checkpoints, manifest, documents)
                errors: list[str] = []
                verify_closeout.verify_checkpoint_c_attestation(checkpoints, manifest, documents, errors)
                self.assertNotEqual(errors, [])

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

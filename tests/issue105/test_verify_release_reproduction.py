from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "issue105"))

from verify_release_reproduction import (  # noqa: E402
    ARCHIVE_ROOT,
    ReproductionError,
    file_identities,
    validate_member_paths,
)


class Issue105ReleaseReproductionTests(unittest.TestCase):
    def test_file_identities_are_relative_and_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "nested").mkdir()
            (root / "nested/value.txt").write_text("value\n", encoding="utf-8")
            identities = file_identities(root)
            self.assertEqual(list(identities), ["nested/value.txt"])
            self.assertEqual(identities["nested/value.txt"]["bytes"], 6)

    def test_invalid_archive_fails_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "invalid.tar.zst"
            archive.write_text("invalid", encoding="utf-8")
            with self.assertRaises(Exception):
                validate_member_paths(archive)

    def test_archive_root_is_fixed(self) -> None:
        self.assertEqual(ARCHIVE_ROOT, "issue105-curated-analysis-v1")
        self.assertTrue(issubclass(ReproductionError, ValueError))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "issue105"))

from package_evidence import (  # noqa: E402
    ARCHIVE_ROOT,
    PackagingError,
    create_archive,
    member_identities,
    validate_selected,
    verify_archive,
)


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Issue105PackagingTests(unittest.TestCase):
    def test_archive_is_byte_deterministic_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            left = root / "left.txt"
            right = root / "right.txt"
            left.write_text("left\n", encoding="utf-8")
            right.write_text("right\n", encoding="utf-8")
            selected = [
                (right, f"{ARCHIVE_ROOT}/right.txt", "TEST"),
                (left, f"{ARCHIVE_ROOT}/left.txt", "TEST"),
            ]
            members = member_identities(selected)
            first = root / "first.tar.zst"
            second = root / "second.tar.zst"
            create_archive(first, selected)
            create_archive(second, list(reversed(selected)))
            self.assertEqual(digest(first), digest(second))
            verify_archive(first, members)

    def test_archive_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "value"
            path.write_text("value", encoding="utf-8")
            with self.assertRaisesRegex(PackagingError, "unsafe"):
                validate_selected([(path, "../escape", "TEST")])


if __name__ == "__main__":
    unittest.main()

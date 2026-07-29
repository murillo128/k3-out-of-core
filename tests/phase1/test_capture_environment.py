from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_environment.py"
SPEC = importlib.util.spec_from_file_location("capture_environment", SCRIPT)
assert SPEC and SPEC.loader
capture_environment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture_environment)


class CaptureEnvironmentTests(unittest.TestCase):
    def test_sha256_reads_binary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture"
            path.write_bytes(b"K3\x00fixture")
            self.assertEqual(
                capture_environment.sha256(path),
                "6c89f907b5378fcb0eba6236c338a81faa74b4619f823c6d8fe39bffb2c3cd7e",
            )

    def test_required_command_missing_fails(self) -> None:
        with mock.patch.object(capture_environment.shutil, "which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "required command is unavailable"):
                capture_environment.run(["missing-command"])

    def test_optional_command_missing_is_explicit(self) -> None:
        with mock.patch.object(capture_environment.shutil, "which", return_value=None):
            self.assertEqual(
                capture_environment.run(["missing-command"], required=False),
                {"status": "unavailable", "value": None, "reason": "missing-command not found"},
            )

    def test_mismatch_fails_with_observed_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected expected, observed actual"):
            capture_environment.require_equal("fixture", "actual", "expected")

    def test_cmake_cache_requires_mandatory_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CMakeCache.txt"
            path.write_text("GGML_CUDA:BOOL=ON\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "mandatory CMake settings missing"):
                capture_environment.cmake_cache(path)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_environment.py"
SPEC = importlib.util.spec_from_file_location("capture_environment", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureEnvironmentTests(unittest.TestCase):
    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"phase-1\n")
            self.assertEqual(
                capture.sha256_file(path),
                "c86646a63e8a5dda5996e4f63f454977744d26ec07164da4aa59caf569ac2348",
            )

    def test_parse_hf_revisions_rejects_invalid_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "REVISIONS.txt"
            path.write_text("owner/model not-a-sha\n", encoding="utf-8")
            with self.assertRaisesRegex(capture.CaptureError, "invalid Hugging Face"):
                capture.parse_hf_revisions(path)

    def test_file_manifest_is_sorted_and_ignores_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "z.txt").write_text("z", encoding="utf-8")
            (root / "a.txt").write_text("a", encoding="utf-8")
            cache_dir = root / "__pycache__"
            cache_dir.mkdir()
            (cache_dir / "ignored.pyc").write_bytes(b"ignored")
            download_cache = root / ".cache" / "download"
            download_cache.mkdir(parents=True)
            (download_cache / "ignored.metadata").write_text("ignored", encoding="utf-8")
            manifest = capture.file_manifest(root)
            self.assertEqual([item["path"] for item in manifest], ["a.txt", "z.txt"])
            self.assertTrue(all(len(item["sha256"]) == 64 for item in manifest))

    def test_parse_cmake_cache_captures_build_flags(self) -> None:
        cache_text = "\n".join(
            [
                "BUILD_SHARED_LIBS:BOOL=ON",
                "CMAKE_BUILD_TYPE:STRING=Release",
                "CMAKE_C_COMPILER:FILEPATH=/usr/bin/cc",
                "CMAKE_CXX_COMPILER:FILEPATH=/usr/bin/c++",
                "CMAKE_GENERATOR:INTERNAL=Unix Makefiles",
                "CMAKE_CXX_FLAGS:STRING=-O3",
                "GGML_CUDA:BOOL=ON",
                "LLAMA_BUILD_TESTS:BOOL=ON",
                "LLAMA_BUILD_TOOLS:BOOL=ON",
                "UNRELATED:STRING=secret-looking-but-excluded",
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CMakeCache.txt"
            path.write_text(cache_text, encoding="utf-8")
            parsed = capture.parse_cmake_cache(path)
        self.assertEqual(parsed["CMAKE_CXX_FLAGS"]["value"], "-O3")
        self.assertEqual(parsed["GGML_CUDA"]["value"], "ON")
        self.assertNotIn("UNRELATED", parsed)

    def test_optional_command_is_explicit_when_unavailable(self) -> None:
        with mock.patch.object(capture.shutil, "which", return_value=None):
            observed = capture.command_observation(
                ["optional-tool", "--version"], required=False
            )
        self.assertEqual(observed["status"], "unavailable")
        self.assertIn("command not found", observed["reason"])

    def test_mandatory_command_fails_when_unavailable(self) -> None:
        with mock.patch.object(capture.shutil, "which", return_value=None):
            with self.assertRaisesRegex(capture.CaptureError, "mandatory command"):
                capture.command_observation(["required-tool"], required=True)

    def test_validate_documents_rejects_wrong_revision(self) -> None:
        environment = {
            "schema_version": capture.SCHEMA_VERSION,
            "host": {"hostname": "skynet"},
            "validation": {"status": "pass"},
        }
        inputs = {
            "schema_version": capture.SCHEMA_VERSION,
            "approved_contract": {
                "execution_base": capture.EXECUTION_BASE,
                "execution_branch": capture.EXECUTION_BRANCH,
            },
            "llama_cpp": {"commit": "0" * 40},
            "validation": {"status": "pass"},
        }
        with self.assertRaisesRegex(capture.CaptureError, "llama.cpp commit mismatch"):
            capture.validate_documents(environment, inputs)

    def test_atomic_json_writer_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "out" / "evidence.json"
            capture.write_json_atomic(path, {"status": "first"})
            capture.write_json_atomic(path, {"status": "second"})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"status": "second"})
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

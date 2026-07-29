from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_environment.py"
ROOT = Path(__file__).resolve().parents[2]
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

    def test_tool_info_rejects_or_classifies_empty_output(self) -> None:
        observed_empty = {"status": "observed", "value": "", "reason": None}
        with mock.patch.object(capture_environment, "run", return_value=observed_empty):
            with mock.patch.object(capture_environment.shutil, "which", return_value="/bin/tool"):
                with self.assertRaisesRegex(RuntimeError, "required command produced no output"):
                    capture_environment.tool_info(["tool", "--version"])
                result = capture_environment.tool_info(["tool", "--version"], required=False)
        self.assertEqual(result["path"], "/bin/tool")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["value"])
        self.assertTrue(result["reason"])

    def test_mismatch_fails_with_observed_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected expected, observed actual"):
            capture_environment.require_equal("fixture", "actual", "expected")

    def test_cmake_cache_requires_mandatory_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "CMakeCache.txt"
            path.write_text("GGML_CUDA:BOOL=ON\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "mandatory CMake settings missing"):
                capture_environment.cmake_cache(path)

    def test_source_revision_record_is_mandatory_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "REVISIONS.txt"
            with self.assertRaisesRegex(RuntimeError, "mandatory source revision record missing"):
                capture_environment.source_revisions(path)
            path.write_text("repo not-a-sha\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "malformed source revision"):
                capture_environment.source_revisions(path)
            path.write_text(
                "inference-optimization/Kimi-K3-0.40B " + "0" * 40 + "\n"
                "inference-optimization/Kimi-K3-0.40B-MXFP4 " + "1" * 40 + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "source revisions mismatch"):
                capture_environment.source_revisions(path)

    def test_wrong_host_fails_before_capture(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "hostname mismatch"):
            capture_environment.capture(ROOT, observed_hostname="wrong-host")

    def test_wrong_gpu_fails_before_artifact_capture(self) -> None:
        def wrong_gpu(argv, **kwargs):
            if argv[0] == "nvidia-smi":
                return {"status": "observed", "value": "Wrong GPU, 1, driver", "reason": None}
            return capture_environment.run(argv, **kwargs)

        with self.assertRaisesRegex(RuntimeError, "GPU name mismatch"):
            capture_environment.capture(ROOT, command_runner=wrong_gpu)

    def test_optional_file_unavailable_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = capture_environment.optional_file(Path(directory) / "missing")
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["value"])
        self.assertTrue(result["reason"])

    def test_real_capture_has_required_schema(self) -> None:
        environment, inputs = capture_environment.capture(ROOT)
        for build in ("cpu", "cuda"):
            values = environment["builds"][build]
            for key in (
                "CMAKE_BUILD_TYPE",
                "CMAKE_GENERATOR",
                "CMAKE_C_COMPILER",
                "CMAKE_CXX_COMPILER",
                "CMAKE_C_FLAGS",
                "CMAKE_CXX_FLAGS",
                "GGML_CPU",
                "GGML_CUDA",
            ):
                self.assertIn(key, values)
        self.assertIn("CMAKE_CUDA_COMPILER", environment["builds"]["cuda"])
        self.assertIn("CMAKE_CUDA_FLAGS", environment["builds"]["cuda"])
        for tool in (
            "cuda_toolkit",
            "cc",
            "cxx",
            "cmake",
            "nvidia_smi",
            "lsblk",
            "findmnt",
            "lscpu",
            "lsb_release",
            "python",
            "hf",
        ):
            record = environment["toolchain"][tool]
            if record["status"] == "observed":
                self.assertTrue(record["path"])
                self.assertTrue(record["value"])
                self.assertIsNone(record["reason"])
            else:
                self.assertEqual(record["status"], "unavailable")
                self.assertIsNone(record["value"])
                self.assertTrue(record["reason"])
        storage = environment["storage"]
        self.assertEqual(storage["root_disk"]["tran"], "nvme")
        self.assertIn(storage["firmware_revision"]["status"], ("observed", "unavailable"))
        for value in storage["pcie_link"].values():
            self.assertIn(value["status"], ("observed", "unavailable"))
            if value["status"] == "unavailable":
                self.assertIsNone(value["value"])
                self.assertTrue(value["reason"])
        self.assertEqual(inputs["models"]["source_revisions_record"]["revisions"], capture_environment.EXPECTED["source_revisions"])


if __name__ == "__main__":
    unittest.main()

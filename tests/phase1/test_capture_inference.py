from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_inference.py"
SPEC = importlib.util.spec_from_file_location("capture_inference", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
inference = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inference)


class CaptureInferenceTests(unittest.TestCase):
    def test_parse_probe_stdout(self) -> None:
        top = ",".join(f"{token}:{10.0 - token}" for token in range(10))
        stdout = "\n".join(
            (
                "CONFIG\tprompt=According to all known laws\tseed=1",
                "DEVICE\t0\tCUDA0\tNVIDIA GeForce GTX 1650\t2\t1\t2",
                "MODEL\tvocabulary=16\tlayers=8\tbytes=10\tparameters=20",
                "PROMPT_IDS\t1,2,3",
                f"STEP\t0\t0\t0\t{top}",
                "GENERATED_IDS\t0",
                "RESULT\tsteps=1\texit=0",
            )
        )
        result = inference.parse_probe_stdout(stdout)
        self.assertEqual(result["prompt_ids"], [1, 2, 3])
        self.assertEqual(result["generated_ids"], [0])
        self.assertEqual(result["devices"][0]["description"], "NVIDIA GeForce GTX 1650")
        self.assertEqual(len(result["steps"][0]["top_10"]), 10)

    def test_parse_probe_stdout_rejects_missing_result(self) -> None:
        with self.assertRaisesRegex(inference.InferenceError, "missing records"):
            inference.parse_probe_stdout("PROMPT_IDS\t1,2")

    def test_validate_probe_contract_rejects_wrong_seed(self) -> None:
        parsed = {
            "config": {
                "prompt": inference.PROMPT,
                "seed": "2",
                "temperature": "0",
                "context": "512",
                "generate": "32",
                "threads": "8",
                "gpu_layers": "0",
            },
            "prompt_ids": inference.EXPECTED_PROMPT_IDS,
            "generated_ids": list(range(32)),
            "steps": [
                {"index": index, "generated_id": index}
                for index in range(32)
            ],
            "result": {"steps": "32", "exit": "0"},
        }
        with self.assertRaisesRegex(inference.InferenceError, "contract checks failed"):
            inference.validate_probe_contract(parsed, "cpu")

    def test_parse_placement(self) -> None:
        stderr = "\n".join(
            (
                "load_tensors: layer   1 assigned to device CUDA0, is_swa = 0",
                "load_tensors: layer   4 assigned to device CUDA0, is_swa = 0",
                "load_tensors:        CUDA0 model buffer size =   10.00 MiB",
                "llama_context:  CUDA0 compute buffer size =    2.00 MiB",
                "llama_kv: CUDA0 KV buffer size = 1.00 MiB",
            )
        )
        result = inference.parse_placement(stderr, [])
        self.assertEqual(result["layer_assignments"], {"CUDA0": [1, 4]})
        self.assertEqual([item["kind"] for item in result["buffers"]], ["model", "compute", "KV"])

    def test_hard_failure_scan_rejects_nonfinite_logits(self) -> None:
        result = inference.hard_failure_scan("", np.array([[0.0, np.nan]], dtype=np.float32))
        self.assertFalse(result["passed"])
        self.assertFalse(result["all_logits_finite"])

    def test_hard_failure_scan_rejects_hidden_fallback(self) -> None:
        result = inference.hard_failure_scan(
            "warning: hidden backend fallback",
            np.zeros((1, 2), dtype=np.float32),
        )
        self.assertFalse(result["passed"])
        self.assertIn("hidden_fallback_warning", result["log_scan_matches"])

    def test_vector_metrics_exact(self) -> None:
        values = np.array([[1.0, 2.0, 3.0]], dtype=np.float32)
        result = inference.vector_metrics(values, values.copy())
        self.assertEqual(result["maximum_absolute_difference"], 0.0)
        self.assertEqual(result["mean_absolute_difference"], 0.0)
        self.assertAlmostEqual(result["cosine_similarity"], 1.0)

    def test_same_artifact_comparison_passes_thresholds(self) -> None:
        top = [{"token_id": token, "logit": float(10 - token)} for token in range(10)]
        run = {
            "prompt_ids": [1, 2],
            "generated_ids": [0],
            "steps": [{"top_10": top}],
        }
        logits = np.arange(16, dtype=np.float32).reshape(1, 16)
        result = inference.compare_same_artifact(run, run, logits, logits.copy())
        self.assertEqual(result["status"], "pass")
        self.assertTrue(all(result["threshold_checks"].values()))

    def test_same_artifact_comparison_allows_rank_swap_with_exact_id_set(self) -> None:
        cpu_top = [{"token_id": token, "logit": float(10 - token)} for token in range(10)]
        cuda_top = cpu_top.copy()
        cuda_top[8], cuda_top[9] = cuda_top[9], cuda_top[8]
        cpu = {"prompt_ids": [1], "generated_ids": [0], "steps": [{"top_10": cpu_top}]}
        cuda = {"prompt_ids": [1], "generated_ids": [0], "steps": [{"top_10": cuda_top}]}
        logits = np.arange(16, dtype=np.float32).reshape(1, 16)
        result = inference.compare_same_artifact(cpu, cuda, logits, logits.copy())
        self.assertTrue(result["top_10_id_sets_exact_each_step"])
        self.assertFalse(result["top_10_ordered_rankings_exact_each_step"])
        self.assertEqual(len(result["top_10_ordered_ranking_differences"]), 1)

    def test_same_artifact_comparison_rejects_generated_id_difference(self) -> None:
        top = [{"token_id": token, "logit": float(token)} for token in range(10)]
        cpu = {"prompt_ids": [1], "generated_ids": [1], "steps": [{"top_10": top}]}
        cuda = {"prompt_ids": [1], "generated_ids": [2], "steps": [{"top_10": top}]}
        logits = np.arange(16, dtype=np.float32).reshape(1, 16)
        with self.assertRaisesRegex(inference.InferenceError, "comparison failed"):
            inference.compare_same_artifact(cpu, cuda, logits, logits)


if __name__ == "__main__":
    unittest.main()

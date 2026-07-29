from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/capture_tokenizer.py"
SPEC = importlib.util.spec_from_file_location("capture_tokenizer", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
tokenizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tokenizer)


class CaptureTokenizerTests(unittest.TestCase):
    def test_workaround_removes_only_duplicate_extra_special_tokens(self) -> None:
        document = {
            "bos_token": "[BOS]",
            "additional_special_tokens": ["<|im_end|>", "[EOT]"],
            "extra_special_tokens": ["<|im_end|>", "[EOT]"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer_config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = tokenizer.apply_tokenizer_workaround(path)
            transformed = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(result["changed"])
        self.assertNotIn("extra_special_tokens", transformed)
        self.assertEqual(
            transformed["additional_special_tokens"],
            ["<|im_end|>", "[EOT]"],
        )

    def test_workaround_is_idempotent(self) -> None:
        document = {"additional_special_tokens": ["<|im_end|>"]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer_config.json"
            path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
            first = tokenizer.apply_tokenizer_workaround(path)
            second = tokenizer.apply_tokenizer_workaround(path)
        self.assertFalse(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(first["transformed_sha256"], second["transformed_sha256"])

    def test_workaround_rejects_conflicting_special_token_lists(self) -> None:
        document = {
            "additional_special_tokens": ["<|im_end|>"],
            "extra_special_tokens": ["different"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer_config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(tokenizer.TokenizerError, "does not match"):
                tokenizer.apply_tokenizer_workaround(path)

    def test_parse_ids_reads_python_list(self) -> None:
        self.assertEqual(
            tokenizer.parse_ids("diagnostic\n[18805, 308, 799, 5624, 12524]\n"),
            [18805, 308, 799, 5624, 12524],
        )

    def test_parse_ids_rejects_non_list_output(self) -> None:
        with self.assertRaisesRegex(tokenizer.TokenizerError, "does not contain"):
            tokenizer.parse_ids("no token IDs")

    def test_consistency_record_explains_source_conflict(self) -> None:
        config = {
            "bos_token_id": 1,
            "eos_token_id": 2,
            "pad_token_id": 0,
        }
        hf = {
            "bos": {"id": 163584},
            "eos": {"id": 163585},
            "pad": {"id": 163839},
            "markers": {"<|im_end|>": {"convert_tokens_to_ids": 163840}},
        }
        gguf = {
            "bos_token_id_metadata": 1,
            "eos_token_id_metadata": 2,
            "padding_token_id_metadata": 0,
            "marker_token_ids": {
                "[BOS]": 163584,
                "[EOS]": 163585,
                "[PAD]": 163839,
                "<|im_end|>": None,
            },
        }
        result = tokenizer.build_consistency_record(config, hf, gguf)
        self.assertEqual(result["status"], "documented-source-conflict")
        self.assertTrue(
            result["details"]["end_of_message"][
                "hf_id_is_outside_declared_vocab"
            ]
        )


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Freeze token-exact prefix cases for the issue-102 prefill-depth curve."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import sys
from typing import Any


DEPTHS = (9, 16, 32, 64, 100)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(encoded)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=pathlib.Path)
    parser.add_argument("--tokenizer-source", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    return parser.parse_args()


def load_tokenizer(source: pathlib.Path):
    module_path = source / "tokenization_kimi.py"
    sys.path.insert(0, str(source))
    spec = importlib.util.spec_from_file_location("issue102_tokenization_kimi", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load tokenizer module {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.TikTokenTokenizer.from_pretrained(
        str(source), trust_remote_code=True, local_files_only=True
    )


def main() -> int:
    args = arguments()
    corpus_path = args.corpus.resolve()
    tokenizer_source = args.tokenizer_source.resolve()
    output_path = args.output.resolve()
    corpus = json.loads(corpus_path.read_text())
    sentinel = corpus["sentinel"]
    templated_prompt = sentinel["templated_prompt"]

    tokenizer = load_tokenizer(tokenizer_source)
    original_ids = tokenizer.encode(templated_prompt, allow_special_tokens=True)
    if len(original_ids) != 100:
        raise RuntimeError(f"sentinel token count is {len(original_ids)}, expected 100")

    cases: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    for depth in DEPTHS:
        prefix_ids = original_ids[:depth]
        prefix = tokenizer.decode(prefix_ids)
        roundtrip_ids = tokenizer.encode(prefix, allow_special_tokens=True)
        if roundtrip_ids != prefix_ids:
            raise RuntimeError(f"depth {depth} prefix does not round-trip token-exactly")
        case_id = f"issue102-sentinel-prefix-{depth:03d}"
        cases.append({
            "id": case_id,
            "semantic_family": "sentinel-prefill-depth-diagnostic",
            "length_level": depth,
            "prompt": prefix,
            "expected_prompt_tokens": depth,
        })
        proofs.append({
            "depth": depth,
            "case_id": case_id,
            "prefix_utf8_bytes": len(prefix.encode("utf-8")),
            "prefix_sha256": sha256_bytes(prefix.encode("utf-8")),
            "token_ids": prefix_ids,
            "token_ids_canonical_sha256": canonical_sha256(prefix_ids),
            "roundtrip_token_ids_equal_original_prefix": True,
        })

    result = {
        "schema_version": "issue102-prefill-depth-prefix-corpus-v1",
        "status": "pass",
        "purpose": (
            "diagnostic-only token-exact prefixes of the frozen issue-102 sentinel; "
            "this does not alter the Stage-A corpus or ordering"
        ),
        "depths": list(DEPTHS),
        "source": {
            "corpus_path": str(corpus_path),
            "corpus_sha256": sha256_file(corpus_path),
            "sentinel_id": sentinel["id"],
            "sentinel_templated_prompt_sha256": sha256_bytes(
                templated_prompt.encode("utf-8")
            ),
            "sentinel_token_count": len(original_ids),
            "sentinel_token_ids_canonical_sha256": canonical_sha256(original_ids),
            "tokenizer_source": str(tokenizer_source),
            "tiktoken_model_sha256": sha256_file(tokenizer_source / "tiktoken.model"),
            "tokenizer_config_sha256": sha256_file(
                tokenizer_source / "tokenizer_config.json"
            ),
            "tokenization_kimi_sha256": sha256_file(
                tokenizer_source / "tokenization_kimi.py"
            ),
        },
        "cases": cases,
        "prefix_proofs": proofs,
        "invariants": {
            "all_prefixes_roundtrip_token_exact": True,
            "prefixes_are_first_n_original_token_ids": True,
            "stage_a_corpus_or_order_changed": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({
        "status": "pass",
        "output": str(output_path),
        "output_sha256": sha256_file(output_path),
        "depths": list(DEPTHS),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

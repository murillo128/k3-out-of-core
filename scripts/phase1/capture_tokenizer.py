#!/usr/bin/env python3
"""Capture reproducible Kimi-K3 tokenizer preparation and behavior evidence."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
PROMPT = "According to all known laws"
EXPECTED_VOCAB_SIZE = 163840
LLAMA_CPP_COMMIT = "84245db4c790af22135f34992689edcc11877003"
SPECIAL_MARKERS = ("[BOS]", "[EOS]", "[EOT]", "[PAD]", "<|im_end|>")
SPECIAL_SEQUENCE = "[BOS][EOS][EOT]<|im_end|>"
TOKENIZER_FILES = (
    "added_tokens.json",
    "config.json",
    "encoding_k3.py",
    "tiktoken.model",
    "tokenization_kimi.py",
    "tokenizer_config.json",
    "tokenizer_config.json.orig",
)
MODELS = {
    "f16": {
        "source_path": "models/hf/Kimi-K3-0.40B",
        "gguf_path": "models/gguf/Kimi-K3-0.40B-F16.gguf",
    },
    "mxfp4": {
        "source_path": "models/hf/Kimi-K3-0.40B-MXFP4",
        "gguf_path": "models/gguf/Kimi-K3-0.40B-MXFP4.gguf",
    },
}


class TokenizerError(RuntimeError):
    """Raised when tokenizer preparation or behavior violates the contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_files(root: Path) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for name in TOKENIZER_FILES:
        path = root / name
        if not path.is_file():
            raise TokenizerError(f"required tokenizer input is unavailable: {path}")
        snapshot[name] = {
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    return snapshot


def apply_tokenizer_workaround(path: Path) -> dict[str, Any]:
    before_bytes = path.read_bytes()
    before_hash = hashlib.sha256(before_bytes).hexdigest()
    document = json.loads(before_bytes)
    additional = document.get("additional_special_tokens")
    removed = document.pop("extra_special_tokens", None)
    if removed is not None and removed != additional:
        raise TokenizerError(
            "extra_special_tokens does not match additional_special_tokens"
        )
    after_bytes = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.write_bytes(after_bytes)
    return {
        "source_sha256": before_hash,
        "transformed_sha256": hashlib.sha256(after_bytes).hexdigest(),
        "removed_extra_special_tokens": removed,
        "preserved_additional_special_tokens": additional,
        "changed": before_bytes != after_bytes,
    }


def copy_tokenizer_fixture(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix != ".safetensors":
            shutil.copy2(path, destination / path.name)
    shutil.copy2(
        source / "tokenizer_config.json.orig",
        destination / "tokenizer_config.json",
    )


def load_hf_tokenizer(path: Path) -> Any:
    try:
        from transformers import AutoTokenizer
    except ImportError as error:
        raise TokenizerError("transformers is unavailable") from error
    return AutoTokenizer.from_pretrained(
        path,
        trust_remote_code=True,
        local_files_only=True,
    )


def capture_hf_behavior(tokenizer: Any) -> dict[str, Any]:
    markers: dict[str, Any] = {}
    for marker in SPECIAL_MARKERS:
        markers[marker] = {
            "convert_tokens_to_ids": tokenizer.convert_tokens_to_ids(marker),
            "allow_special_tokens_true": tokenizer.encode(
                marker, allow_special_tokens=True
            ),
            "allow_special_tokens_false": tokenizer.encode(
                marker, allow_special_tokens=False
            ),
        }
    return {
        "class": f"{type(tokenizer).__module__}.{type(tokenizer).__name__}",
        "vocab_size": tokenizer.vocab_size,
        "len": len(tokenizer),
        "prompt": PROMPT,
        "prompt_ids": tokenizer.encode(PROMPT),
        "prompt_decoded": tokenizer.decode(tokenizer.encode(PROMPT)),
        "bos": {"token": tokenizer.bos_token, "id": tokenizer.bos_token_id},
        "eos": {"token": tokenizer.eos_token, "id": tokenizer.eos_token_id},
        "pad": {"token": tokenizer.pad_token, "id": tokenizer.pad_token_id},
        "additional_special_tokens": tokenizer.additional_special_tokens,
        "additional_special_token_ids": tokenizer.additional_special_tokens_ids,
        "markers": markers,
    }


def scalar_field(reader: Any, name: str) -> int | bool | str | None:
    field = reader.fields.get(name)
    if field is None:
        return None
    value = field.contents()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (int, bool, str)):
        return value
    return int(value)


def capture_gguf_metadata(repo_root: Path, gguf_path: Path) -> dict[str, Any]:
    gguf_python = repo_root / "llama.cpp/gguf-py"
    sys.path.insert(0, str(gguf_python))
    try:
        from gguf import GGUFReader
    except ImportError as error:
        raise TokenizerError("pinned GGUF reader is unavailable") from error
    finally:
        sys.path.pop(0)

    reader = GGUFReader(gguf_path, "r")
    tokens = list(reader.fields["tokenizer.ggml.tokens"].contents())
    marker_ids = {
        marker: ([index for index, token in enumerate(tokens) if token == marker] or [None])[0]
        for marker in SPECIAL_MARKERS
    }
    return {
        "vocab_size": int(scalar_field(reader, "kimi-k3.vocab_size")),
        "token_count": len(tokens),
        "model": scalar_field(reader, "tokenizer.ggml.model"),
        "pre": scalar_field(reader, "tokenizer.ggml.pre"),
        "bos_token_id_metadata": scalar_field(
            reader, "tokenizer.ggml.bos_token_id"
        ),
        "eos_token_id_metadata": scalar_field(
            reader, "tokenizer.ggml.eos_token_id"
        ),
        "padding_token_id_metadata": scalar_field(
            reader, "tokenizer.ggml.padding_token_id"
        ),
        "add_bos_token_metadata": scalar_field(
            reader, "tokenizer.ggml.add_bos_token"
        ),
        "add_eos_token_metadata": scalar_field(
            reader, "tokenizer.ggml.add_eos_token"
        ),
        "marker_token_ids": marker_ids,
    }


def parse_ids(stdout: str) -> list[int]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if re.fullmatch(r"\[(?:\d+(?:,\s*)?)*\]", line):
            value = ast.literal_eval(line)
            if isinstance(value, list) and all(isinstance(item, int) for item in value):
                return value
    raise TokenizerError("llama-tokenize output does not contain an ID list")


def run_llama_tokenize(
    repo_root: Path,
    gguf_path: Path,
    text: str,
    *,
    parse_special: bool,
) -> dict[str, Any]:
    executable = repo_root / "llama.cpp/build-cpu/bin/llama-tokenize"
    command = [str(executable), "-m", str(gguf_path), "-p", text, "--ids", "--offline"]
    if not parse_special:
        command.append("--no-parse-special")
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise TokenizerError(
            f"llama-tokenize failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "ids": parse_ids(completed.stdout),
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def model_config_ids(source_root: Path) -> dict[str, int | None]:
    config = json.loads((source_root / "config.json").read_text(encoding="utf-8"))
    text_config = config.get("text_config", {})
    return {
        "bos_token_id": text_config.get("bos_token_id"),
        "eos_token_id": text_config.get("eos_token_id"),
        "pad_token_id": text_config.get("pad_token_id"),
        "vocab_size": text_config.get("vocab_size"),
    }


def build_consistency_record(
    config_ids: dict[str, Any],
    hf: dict[str, Any],
    gguf: dict[str, Any],
) -> dict[str, Any]:
    conflicts = {
        "bos": {
            "model_config_id": config_ids["bos_token_id"],
            "hf_tokenizer_id": hf["bos"]["id"],
            "gguf_metadata_id": gguf["bos_token_id_metadata"],
            "gguf_marker_id": gguf["marker_token_ids"]["[BOS]"],
        },
        "eos": {
            "model_config_id": config_ids["eos_token_id"],
            "hf_tokenizer_id": hf["eos"]["id"],
            "gguf_metadata_id": gguf["eos_token_id_metadata"],
            "gguf_marker_id": gguf["marker_token_ids"]["[EOS]"],
        },
        "pad": {
            "model_config_id": config_ids["pad_token_id"],
            "hf_tokenizer_id": hf["pad"]["id"],
            "gguf_metadata_id": gguf["padding_token_id_metadata"],
            "gguf_marker_id": gguf["marker_token_ids"]["[PAD]"],
        },
        "end_of_message": {
            "marker": "<|im_end|>",
            "hf_tokenizer_id": hf["markers"]["<|im_end|>"][
                "convert_tokens_to_ids"
            ],
            "gguf_marker_id": gguf["marker_token_ids"]["<|im_end|>"],
            "hf_id_is_outside_declared_vocab": hf["markers"]["<|im_end|>"][
                "convert_tokens_to_ids"
            ]
            >= EXPECTED_VOCAB_SIZE,
        },
    }
    has_conflict = any(
        len({value for value in record.values() if isinstance(value, int)}) > 1
        for record in (conflicts["bos"], conflicts["eos"], conflicts["pad"])
    ) or conflicts["end_of_message"]["gguf_marker_id"] is None
    return {
        "status": "documented-source-conflict" if has_conflict else "consistent",
        "details": conflicts,
        "explanation": (
            "The model text config supplies BOS/EOS/PAD IDs used by GGUF metadata, "
            "while the custom tiktoken tokenizer maps the named markers near the end "
            "of its 163840-token vocabulary. <|im_end|> is an added token at ID 163840 "
            "and therefore is absent from the 0..163839 GGUF token table. Ordinary "
            "prompt tokenization remains identical across HF and GGUF paths."
        ),
    }


def capture_model(repo_root: Path, name: str, paths: dict[str, str]) -> dict[str, Any]:
    source_root = repo_root / paths["source_path"]
    gguf_path = repo_root / paths["gguf_path"]
    before = snapshot_files(source_root)

    with tempfile.TemporaryDirectory(prefix=f"k3-tokenizer-{name}-") as directory:
        temporary_root = Path(directory) / source_root.name
        copy_tokenizer_fixture(source_root, temporary_root)
        transformation = apply_tokenizer_workaround(
            temporary_root / "tokenizer_config.json"
        )
        first_hash = sha256_file(temporary_root / "tokenizer_config.json")
        idempotence = apply_tokenizer_workaround(
            temporary_root / "tokenizer_config.json"
        )
        second_hash = sha256_file(temporary_root / "tokenizer_config.json")
        existing_prepared_hash = sha256_file(source_root / "tokenizer_config.json")
        if first_hash != second_hash or idempotence["changed"]:
            raise TokenizerError(f"tokenizer workaround is not idempotent for {name}")
        if first_hash != existing_prepared_hash:
            raise TokenizerError(
                f"temporary transformation differs from prepared fixture for {name}"
            )
        tokenizer = load_hf_tokenizer(temporary_root)
        hf_behavior = capture_hf_behavior(tokenizer)

    after = snapshot_files(source_root)
    if before != after:
        raise TokenizerError(f"source tokenizer fixture changed during capture: {name}")

    gguf_metadata = capture_gguf_metadata(repo_root, gguf_path)
    gguf_prompt = run_llama_tokenize(
        repo_root, gguf_path, PROMPT, parse_special=True
    )
    gguf_special = run_llama_tokenize(
        repo_root, gguf_path, SPECIAL_SEQUENCE, parse_special=True
    )
    gguf_ordinary = run_llama_tokenize(
        repo_root, gguf_path, SPECIAL_SEQUENCE, parse_special=False
    )
    config_ids = model_config_ids(source_root)

    return {
        "source_path": paths["source_path"],
        "gguf_path": paths["gguf_path"],
        "source_files_before": before,
        "source_files_after": after,
        "source_unchanged": True,
        "temporary_workaround": {
            **transformation,
            "transformed_matches_existing_prepared_config": True,
            "second_application_changed": idempotence["changed"],
            "second_application_sha256": second_hash,
            "idempotent": True,
        },
        "model_text_config": config_ids,
        "hf_tokenizer": hf_behavior,
        "gguf_metadata": gguf_metadata,
        "gguf_tokenization": {
            "prompt": gguf_prompt,
            "special_sequence": {
                "text": SPECIAL_SEQUENCE,
                "parse_special_true": gguf_special,
                "parse_special_false": gguf_ordinary,
            },
        },
        "special_token_consistency": build_consistency_record(
            config_ids, hf_behavior, gguf_metadata
        ),
    }


def validate(models: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    prompt_sequences = []
    gguf_prompt_sequences = []
    for name, model in models.items():
        hf = model["hf_tokenizer"]
        gguf = model["gguf_metadata"]
        if not model["source_unchanged"]:
            errors.append(f"{name} source fixture changed")
        if not model["temporary_workaround"]["idempotent"]:
            errors.append(f"{name} tokenizer workaround is not idempotent")
        if hf["vocab_size"] != EXPECTED_VOCAB_SIZE:
            errors.append(f"{name} HF vocabulary size mismatch")
        if gguf["vocab_size"] != EXPECTED_VOCAB_SIZE:
            errors.append(f"{name} GGUF vocabulary size mismatch")
        if gguf["token_count"] != EXPECTED_VOCAB_SIZE:
            errors.append(f"{name} GGUF token count mismatch")
        if hf["prompt_decoded"] != PROMPT:
            errors.append(f"{name} HF prompt round trip mismatch")
        hf_prompt = hf["prompt_ids"]
        gguf_prompt = model["gguf_tokenization"]["prompt"]["ids"]
        if hf_prompt != gguf_prompt:
            errors.append(f"{name} HF/GGUF prompt token mismatch")
        prompt_sequences.append(hf_prompt)
        gguf_prompt_sequences.append(gguf_prompt)
    if len(prompt_sequences) != 2 or prompt_sequences[0] != prompt_sequences[1]:
        errors.append("F16/MXFP4 HF prompt token mismatch")
    if len(gguf_prompt_sequences) != 2 or gguf_prompt_sequences[0] != gguf_prompt_sequences[1]:
        errors.append("F16/MXFP4 GGUF prompt token mismatch")
    return errors


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    output = args.output if args.output.is_absolute() else repo_root / args.output
    llama_root = repo_root / "llama.cpp"
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=llama_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if head != LLAMA_CPP_COMMIT:
            raise TokenizerError(f"unexpected llama.cpp revision: {head}")
        before_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=llama_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if before_status:
            raise TokenizerError("llama.cpp submodule is not clean")
        models = {
            name: capture_model(repo_root, name, paths)
            for name, paths in MODELS.items()
        }
        errors = validate(models)
        after_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=llama_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if after_status:
            errors.append("llama.cpp submodule changed during tokenizer capture")
        document = {
            "schema_version": SCHEMA_VERSION,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "prompt": PROMPT,
            "expected_vocab_size": EXPECTED_VOCAB_SIZE,
            "llama_cpp_commit": LLAMA_CPP_COMMIT,
            "models": models,
            "cross_model": {
                "hf_prompt_ids_equal": models["f16"]["hf_tokenizer"]["prompt_ids"]
                == models["mxfp4"]["hf_tokenizer"]["prompt_ids"],
                "gguf_prompt_ids_equal": models["f16"]["gguf_tokenization"][
                    "prompt"
                ]["ids"]
                == models["mxfp4"]["gguf_tokenization"]["prompt"]["ids"],
                "hf_and_gguf_prompt_ids_equal": models["f16"]["hf_tokenizer"][
                    "prompt_ids"
                ]
                == models["f16"]["gguf_tokenization"]["prompt"]["ids"],
                "special_behavior_equal": models["f16"]["hf_tokenizer"]["markers"]
                == models["mxfp4"]["hf_tokenizer"]["markers"],
            },
            "validation": {"status": "pass" if not errors else "fail", "errors": errors},
        }
        write_json_atomic(output.resolve(), document)
    except (
        TokenizerError,
        OSError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"tokenizer capture failed: {error}", file=sys.stderr)
        return 1
    print(f"prompt IDs: {models['f16']['hf_tokenizer']['prompt_ids']}")
    print(
        "special-token status:",
        models["f16"]["special_token_consistency"]["status"],
    )
    print(f"wrote {output.resolve()}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

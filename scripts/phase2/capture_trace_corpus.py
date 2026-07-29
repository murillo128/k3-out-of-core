#!/usr/bin/env python3
"""Capture, validate, and archive the bounded issue #10 Phase 2 trace corpus."""

from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from route_trace import read_route_trace


PUBLISHED_GGUF_REVISION = "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"
CORPUS_VERSION = "phase2-k3-route-corpus-v1"
ARTIFACTS = {
    "f16": {
        "name": "Kimi-K3-0.40B-F16.gguf",
        "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "source_revision": "d853649387ffe8f48ce0198a29ac1a44205031f7",
    },
    "mxfp4": {
        "name": "Kimi-K3-0.40B-MXFP4.gguf",
        "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "source_revision": "ef3902c318fb8e13c3507e26055656e687fdfe38",
    },
}
BACKENDS = {"cpu": 0, "cuda": 999}


class CorpusError(RuntimeError):
    """Raised when corpus capture violates the approved contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=root,
        env=dict(os.environ),
        text=True,
        capture_output=True,
        check=False,
    )


def compile_probe(root: Path, temporary: Path, backend: str) -> tuple[Path, list[str]]:
    build_bin = root / f"llama.cpp/build-{backend}/bin"
    output = temporary / f"route-probe-{backend}"
    command = [
        "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
        f"-I{root / 'llama.cpp/include'}",
        f"-I{root / 'llama.cpp/ggml/include'}",
        str(root / "scripts/phase2/route_probe.cpp"),
        str(root / "scripts/phase2/route_trace.cpp"),
        f"-L{build_bin}", f"-Wl,-rpath,{build_bin}",
        "-lllama", "-lggml", "-lggml-base", "-o", str(output),
    ]
    completed = run(command, root)
    if completed.returncode != 0:
        raise CorpusError(f"{backend} probe compilation failed: {completed.stderr}")
    return output, command


def parse_ids(output: str, field: str) -> list[int]:
    lines = [line for line in output.splitlines() if line.startswith(field + "\t")]
    if len(lines) != 1:
        raise CorpusError(f"expected exactly one {field} line")
    return [int(value) for value in lines[0].split("\t", 1)[1].split(",")]


def parse_stats(output: str) -> dict[str, int]:
    lines = [line for line in output.splitlines() if line.startswith("ROUTE_STATS\t")]
    if len(lines) != 1:
        raise CorpusError("expected exactly one ROUTE_STATS line")
    return {
        name: int(value)
        for name, value in (
            field.split("=", 1) for field in lines[0].split("\t")[1:]
        )
    }


def parse_value(output: str, field: str) -> str:
    lines = [line for line in output.splitlines() if line.startswith(field + "\t")]
    if len(lines) != 1:
        raise CorpusError(f"expected exactly one {field} line")
    return lines[0].split("\t", 1)[1]


def trace_arguments(
    metadata: dict[str, Any], revision: str, trace: Path, run_id: str
) -> list[str]:
    return [
        "--trace", str(trace),
        "--model-name", metadata["name"],
        "--model-size", str(metadata["size"]),
        "--model-sha256", metadata["sha256"],
        "--model-source-revision", metadata["source_revision"],
        "--published-gguf-revision", PUBLISHED_GGUF_REVISION,
        "--llama-cpp-revision", revision,
        "--run-id", run_id,
        "--max-ubatch-payload", "131072",
    ]


def capture_once(
    root: Path,
    temporary: Path,
    binary: Path,
    artifact: str,
    backend: str,
    prompt: dict[str, Any],
    revision: str,
    trace_path: Path,
    repeat: bool,
) -> dict[str, Any]:
    metadata = ARTIFACTS[artifact]
    prompt_file = temporary / f"{prompt['id']}.txt"
    prompt_file.write_text(prompt["text"])
    suffix = "repeat" if repeat else "primary"
    run_id = f"{CORPUS_VERSION}-{artifact}-{backend}-{prompt['id']}"
    command = [
        str(binary),
        "--model", str(root / "models/gguf" / metadata["name"]),
        "--gpu-layers", str(BACKENDS[backend]),
        "--logits", str(temporary / f"{artifact}-{backend}-{prompt['id']}-{suffix}.logits"),
        "--prompt-file", str(prompt_file),
        "--max-generate", str(prompt["max_generated_tokens"]),
        "--skip-logits-write",
    ] + trace_arguments(metadata, revision, trace_path, run_id)
    completed = run(command, root)
    if completed.returncode != 0 or "RESULT\texit=0" not in completed.stdout:
        raise CorpusError(
            f"{artifact}/{backend}/{prompt['id']}/{suffix} failed "
            f"({completed.returncode}): {completed.stderr}"
        )
    if re.search(r"\b(?:nan|inf)\b", completed.stderr, re.IGNORECASE):
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} emitted a hard-failure marker")

    prompt_ids = parse_ids(completed.stdout, "PROMPT_IDS")
    generated_ids = parse_ids(completed.stdout, "GENERATED_IDS")
    stop_reason = parse_value(completed.stdout, "STOP_REASON")
    if stop_reason not in {"eog", "cap"}:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} stop reason is invalid")
    stats = parse_stats(completed.stdout)
    expected_records = (len(prompt_ids) + len(generated_ids) - 1) * 7
    expected = {
        "ubatches": len(generated_ids),
        "layers": len(generated_ids) * 7,
        "copy_bytes": expected_records * 16,
        "synchronizations": len(generated_ids),
        "failures": 0,
        "records": expected_records,
    }
    if any(stats.get(name) != value for name, value in expected.items()):
        raise CorpusError(
            f"{artifact}/{backend}/{prompt['id']} observer accounting changed: {stats}"
        )
    if stats.get("trace_bytes") != trace_path.stat().st_size:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} trace byte count differs")
    if not 1 <= stats.get("flushes", 0) < expected_records:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} trace flushing is invalid")

    trace = read_route_trace(trace_path)
    records = trace["records"]
    if len(records) != expected_records:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} record count differs")
    prefill_records = [record for record in records if record["phase"] == "PREFILL"]
    decode_records = [record for record in records if record["phase"] == "DECODE"]
    if len(prefill_records) != len(prompt_ids) * 7:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} prefill coverage differs")
    if len(decode_records) != max(0, len(generated_ids) - 1) * 7:
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} decode coverage differs")
    if any(
        len(record["selected_experts"]) != 2
        or len(set(record["selected_experts"])) != 2
        or any(expert < 0 or expert >= 8 for expert in record["selected_experts"])
        or any(not math.isfinite(weight) or weight < 0 for weight in record["weights"])
        or abs(sum(record["weights"]) - 1.0) > 1e-5
        for record in records
    ):
        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} route invariant differs")

    return {
        "artifact": artifact,
        "backend": backend,
        "prompt_id": prompt["id"],
        "run_id": run_id,
        "prompt_ids": prompt_ids,
        "generated_ids": generated_ids,
        "observed_generated_tokens": len(generated_ids),
        "stop_reason": stop_reason,
        "natural_eog": stop_reason == "eog",
        "route_stats": stats,
        "trace_sha256": sha256_file(trace_path),
        "trace_bytes": trace_path.stat().st_size,
        "trace_checksum": trace["checksum"],
    }


def compare_parity(cpu_path: Path, cuda_path: Path) -> dict[str, Any]:
    cpu = read_route_trace(cpu_path)
    cuda = read_route_trace(cuda_path)
    if len(cpu["records"]) != len(cuda["records"]):
        raise CorpusError("CPU/CUDA trace record counts differ")
    max_weight_delta = 0.0
    selection_mismatches = 0
    selected_set_mismatches = 0
    matched_selection_records = 0
    for cpu_record, cuda_record in zip(cpu["records"], cuda["records"], strict=True):
        identity_fields = (
            "request_ordinal", "ubatch_ordinal", "phase", "layer", "batch_row",
            "position", "sequence_ids",
        )
        if any(cpu_record[field] != cuda_record[field] for field in identity_fields):
            raise CorpusError("CPU/CUDA route coordinates differ")
        if cpu_record["selected_experts"] != cuda_record["selected_experts"]:
            selection_mismatches += 1
            if set(cpu_record["selected_experts"]) != set(cuda_record["selected_experts"]):
                selected_set_mismatches += 1
        else:
            matched_selection_records += 1
            max_weight_delta = max(
                max_weight_delta,
                *(abs(left - right) for left, right in zip(
                    cpu_record["weights"], cuda_record["weights"], strict=True
                )),
            )
    return {
        "records": len(cpu["records"]),
        "route_coordinates_exact": True,
        "selected_experts_exact": selection_mismatches == 0,
        "selected_expert_mismatch_records": selection_mismatches,
        "selected_expert_mismatch_rate": selection_mismatches / len(cpu["records"]),
        "selected_expert_set_mismatch_records": selected_set_mismatches,
        "matched_selection_records": matched_selection_records,
        "maximum_absolute_weight_delta_on_matched_selections": max_weight_delta,
        "weight_comparison_threshold": None,
    }


def validate_prompt_definition(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("schema_version") != "phase2-prompt-corpus-v1":
        raise CorpusError("unsupported prompt corpus version")
    if document.get("seed") != 1 or document.get("temperature") != 0 or document.get("context") != 512:
        raise CorpusError("prompt corpus deterministic configuration differs")
    prompts = document.get("prompts")
    if not isinstance(prompts, list) or len(prompts) < 6:
        raise CorpusError("prompt corpus is too small")
    ids = {prompt.get("id") for prompt in prompts}
    if len(ids) != len(prompts) or any(not re.fullmatch(r"[a-z0-9-]+", value or "") for value in ids):
        raise CorpusError("prompt IDs are missing, duplicate, or unsafe")
    domains = {prompt.get("domain") for prompt in prompts}
    required_domains = {"constructed prose", "code", "structured data", "technical", "narrative"}
    if not required_domains.issubset(domains):
        raise CorpusError("prompt domain coverage is incomplete")
    if {prompt.get("language") for prompt in prompts} != {"English", "Spanish"}:
        raise CorpusError("prompt language coverage is incomplete")
    if not all(
        prompt.get("prefill_class") in {"small", "large"}
        and prompt.get("decode_class") in {"short", "long"}
        and isinstance(prompt.get("text"), str)
        and prompt["text"]
        and (
            prompt["max_generated_tokens"] <= 16
            if prompt["decode_class"] == "short"
            else prompt["max_generated_tokens"] == 128
        )
        for prompt in prompts
    ):
        raise CorpusError("prompt class or decode cap is invalid")
    parity_ids = document.get("cuda_parity_prompt_ids")
    if not isinstance(parity_ids, list) or len(parity_ids) < 2 or not set(parity_ids).issubset(ids):
        raise CorpusError("CUDA parity subset is invalid")
    return prompts


def deterministic_archive(source: Path, archive: Path) -> dict[str, Any]:
    members = []
    paths = sorted(
        item
        for item in source.rglob("*")
        if item.is_file() and item.resolve() != archive.resolve()
    )
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as target:
                for path in paths:
                    data = path.read_bytes()
                    relative = Path(CORPUS_VERSION) / path.relative_to(source)
                    info = tarfile.TarInfo(relative.as_posix())
                    info.size = len(data)
                    info.mode = 0o644
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    target.addfile(info, io.BytesIO(data))
                    members.append(
                        {"path": relative.as_posix(), "size": len(data), "sha256": sha256_bytes(data)}
                    )
    return {
        "filename": archive.name,
        "size": archive.stat().st_size,
        "sha256": sha256_file(archive),
        "members": members,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--prompts", type=Path, required=True)
    parser.add_argument("--llama-revision", required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    root = args.project_root.resolve()
    prompts_path = args.prompts.resolve()
    raw_dir = args.raw_dir.resolve()
    output = args.output.resolve()
    observed_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root / "llama.cpp",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    if observed_revision != args.llama_revision:
        raise CorpusError("nested llama.cpp revision differs")
    document = json.loads(prompts_path.read_text())
    prompts = validate_prompt_definition(document)

    for metadata in ARTIFACTS.values():
        model = root / "models/gguf" / metadata["name"]
        if model.stat().st_size != metadata["size"] or sha256_file(model) != metadata["sha256"]:
            raise CorpusError(f"model identity differs: {model}")
    if raw_dir.exists() and any(raw_dir.iterdir()):
        raise CorpusError(f"raw corpus directory is not empty: {raw_dir}")
    raw_dir.mkdir(parents=True, exist_ok=True)
    traces_dir = raw_dir / "traces"
    traces_dir.mkdir()

    cases: list[dict[str, Any]] = []
    trace_paths: dict[tuple[str, str, str], Path] = {}
    parity: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="phase2-corpus-capture-") as temporary_name:
        temporary = Path(temporary_name)
        binaries = {}
        compilations = {}
        for backend in BACKENDS:
            binaries[backend], compilations[backend] = compile_probe(root, temporary, backend)

        for artifact in ARTIFACTS:
            for prompt in prompts:
                backends = ["cpu"]
                if prompt["id"] in document["cuda_parity_prompt_ids"]:
                    backends.append("cuda")
                for backend in backends:
                    name = f"{artifact}-{backend}-{prompt['id']}.bin"
                    primary_path = traces_dir / name
                    repeat_path = temporary / f"repeat-{name}"
                    primary = capture_once(
                        root, temporary, binaries[backend], artifact, backend,
                        prompt, args.llama_revision, primary_path, False,
                    )
                    repeat = capture_once(
                        root, temporary, binaries[backend], artifact, backend,
                        prompt, args.llama_revision, repeat_path, True,
                    )
                    if primary_path.read_bytes() != repeat_path.read_bytes():
                        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} repeat bytes differ")
                    if (
                        primary["prompt_ids"] != repeat["prompt_ids"]
                        or primary["generated_ids"] != repeat["generated_ids"]
                    ):
                        raise CorpusError(f"{artifact}/{backend}/{prompt['id']} repeat tokens differ")
                    primary["repeat_trace_bytes_identical"] = True
                    primary["archive_member"] = f"{CORPUS_VERSION}/traces/{name}"
                    cases.append(primary)
                    trace_paths[(artifact, backend, prompt["id"])] = primary_path

        prompt_ids_by_prompt: dict[str, set[tuple[int, ...]]] = defaultdict(set)
        for case in cases:
            prompt_ids_by_prompt[case["prompt_id"]].add(tuple(case["prompt_ids"]))
        if any(len(values) != 1 for values in prompt_ids_by_prompt.values()):
            raise CorpusError("prompt token IDs differ across artifacts or backends")

        for prompt in prompts:
            count = len(next(iter(prompt_ids_by_prompt[prompt["id"]])))
            if prompt["prefill_class"] == "small" and count > 32:
                raise CorpusError(f"small prompt {prompt['id']} has {count} tokens")
            if prompt["prefill_class"] == "large" and not 256 <= count <= 384:
                raise CorpusError(f"large prompt {prompt['id']} has {count} tokens")

        by_key = {(case["artifact"], case["backend"], case["prompt_id"]): case for case in cases}
        for artifact in ARTIFACTS:
            for prompt_id in document["cuda_parity_prompt_ids"]:
                cpu_case = by_key[(artifact, "cpu", prompt_id)]
                cuda_case = by_key[(artifact, "cuda", prompt_id)]
                if cpu_case["prompt_ids"] != cuda_case["prompt_ids"]:
                    raise CorpusError("CPU/CUDA prompt IDs differ")
                if cpu_case["generated_ids"] != cuda_case["generated_ids"]:
                    raise CorpusError("CPU/CUDA generated IDs differ")
                parity.append(
                    {
                        "artifact": artifact,
                        "prompt_id": prompt_id,
                        "prompt_ids_exact": True,
                        "generated_ids_exact": True,
                        **compare_parity(
                            trace_paths[(artifact, "cpu", prompt_id)],
                            trace_paths[(artifact, "cuda", prompt_id)],
                        ),
                    }
                )

    prompt_manifest = []
    for prompt in prompts:
        prompt_manifest.append(
            {
                **prompt,
                "prompt_ids": list(next(iter(prompt_ids_by_prompt[prompt["id"]]))),
                "prompt_tokens": len(next(iter(prompt_ids_by_prompt[prompt["id"]]))),
            }
        )
    internal_index = {
        "schema_version": "phase2-trace-corpus-index-v1",
        "corpus_version": CORPUS_VERSION,
        "llama_cpp_revision": args.llama_revision,
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "configuration": {
            "seed": document["seed"],
            "temperature": document["temperature"],
            "context": document["context"],
            "sampling": "greedy finite-logit argmax",
        },
        "prompts": prompt_manifest,
        "cases": cases,
        "cpu_cuda_parity": parity,
    }
    (raw_dir / "corpus-index.json").write_text(
        json.dumps(internal_index, indent=2, sort_keys=True) + "\n"
    )
    shutil.copyfile(prompts_path, raw_dir / "prompts-v1.json")
    readme = (
        "# K3 Phase 2 route corpus version 1\n\n"
        "Deterministic raw route traces for k3-out-of-core issue #10. "
        "See corpus-index.json for exact artifacts, prompts, token IDs, revisions, and checksums.\n"
    )
    (raw_dir / "README.md").write_text(readme)
    checksums = []
    for path in sorted(item for item in raw_dir.rglob("*") if item.is_file()):
        checksums.append(f"{sha256_file(path)}  {path.relative_to(raw_dir).as_posix()}")
    (raw_dir / "SHA256SUMS").write_text("\n".join(checksums) + "\n")

    archive_path = raw_dir / f"{CORPUS_VERSION}.tar.gz"
    archive = deterministic_archive(raw_dir, archive_path)
    report = {
        "schema_version": "phase2-corpus-capture-v1",
        "status": "pass",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "corpus_version": CORPUS_VERSION,
        "llama_cpp_revision": args.llama_revision,
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "models": ARTIFACTS,
        "configuration": internal_index["configuration"],
        "prompts": prompt_manifest,
        "cases": cases,
        "cpu_cuda_parity": parity,
        "archive": archive,
        "checks": {
            "cpu_cases": sum(case["backend"] == "cpu" for case in cases),
            "cuda_cases": sum(case["backend"] == "cuda" for case in cases),
            "all_repeats_byte_identical": all(case["repeat_trace_bytes_identical"] for case in cases),
            "all_prompt_ids_equal_across_artifacts_and_backends": True,
            "all_cpu_cuda_generated_ids_exact": all(item["generated_ids_exact"] for item in parity),
            "cpu_cuda_route_differences_reported": True,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "archive": str(archive_path), "cases": len(cases)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

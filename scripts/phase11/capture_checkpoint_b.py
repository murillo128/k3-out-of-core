#!/usr/bin/env python3
"""Capture or verify Phase 11 Checkpoint B one-pool/readiness parity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = {
    "f16": {"name": "Kimi-K3-0.40B-F16.gguf", "size": 784318432,
        "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "pool_bytes": 1572864},
    "mxfp4": {"name": "Kimi-K3-0.40B-MXFP4.gguf", "size": 751976576,
        "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "pool_bytes": 417792},
}
IDENTITY = ("prompt_ids", "tokens", "logits_hash", "route_hash", "route_records")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}")
    return completed


def fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1:
        raise ValueError(f"expected one {prefix} record")
    result: dict[str, Any] = {}
    for item in lines[0].split("\t")[1:]:
        key, value = item.split("=", 1)
        try:
            result[key] = int(value)
        except ValueError:
            try:
                result[key] = float(value)
            except ValueError:
                result[key] = value
    return result


def execute(binary: Path, model: Path, mode: str, pool_bytes: int = 0) -> dict[str, Any]:
    command = [str(binary), "--model", str(model), "--mode", mode, "--steps", "5"]
    prefix = "PHASE5_LIVE"
    if mode == "uma":
        command += ["--capacity", "2", "--cold-bytes", str(pool_bytes), "--ring-bytes", "0"]
        prefix = "PHASE11_UMA_LIVE"
    completed = run(command)
    return {"command": command, "diagnostics": fields(completed.stdout, prefix),
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}


def validate(document: dict[str, Any]) -> None:
    if set(document) != {"schema_version", "status", "scope", "revisions", "models", "cases", "commands"} or \
            document["schema_version"] != "phase11-checkpoint-b-v1" or document["status"] != "pass" or \
            document["scope"] != "gb10_coherent_uma_buffered_storage_fallback":
        raise ValueError("unsupported Checkpoint B evidence")
    if document["revisions"]["gitlink"] != document["revisions"]["nested_head"]:
        raise ValueError("project/nested gitlink mismatch")
    if set(document["cases"]) != set(MODELS):
        raise ValueError("both bound model cases are required")
    for name, case in document["cases"].items():
        baseline = case["original_baseline"]["diagnostics"]
        original = case["original_uma"]["diagnostics"]
        split = case["split_uma"]["diagnostics"]
        if not all(baseline[key] == original[key] == split[key] for key in IDENTITY):
            raise ValueError(f"{name}: route/output parity failed")
        for run_record in (original, split):
            if run_record["provider_pool_bytes"] != MODELS[name]["pool_bytes"] or \
                    run_record["provider_pool_generations"] != 1 or \
                    run_record["provider_effective_capacity"] != 2 or \
                    run_record["provider_tensor_copies"] != 0 or run_record["provider_failures"] != 0 or \
                    run_record["storage_read_requests"] <= 0 or run_record["storage_read_bytes"] <= 0 or \
                    run_record["scheduler_active"] != 0 or run_record["ring_requested_bytes"] != 0 or \
                    run_record["ring_actual_bytes"] != 0 or run_record["ring_h2d_bytes"] != 0 or \
                    run_record["io_async"] != 0 or run_record["execution_ids_cpu"] <= 0 or \
                    run_record["execution_ids_non_cpu"] != 0 or run_record["execution_backend_device_type"] != 2:
                raise ValueError(f"{name}: one-pool/readiness/no-copy invariant failed")
        if case["split_count"] < 2:
            raise ValueError(f"{name}: split-aware case is not split")


def capture(binary: Path, splitter: Path, models: dict[str, Path], project_head: str,
        nested_head: str) -> dict[str, Any]:
    observed_models = {}
    for name, path in models.items():
        expected = MODELS[name]
        if path.stat().st_size != expected["size"] or sha256(path) != expected["sha256"]:
            raise ValueError(f"{name}: immutable model identity mismatch")
        observed_models[name] = {"path": str(path), "size": path.stat().st_size,
            "sha256": sha256(path)}
    gitlink = subprocess.run(["git", "ls-tree", project_head, "--", "llama.cpp"], cwd=ROOT,
        text=True, capture_output=True, check=True).stdout.split()[2]
    cases = {}
    with tempfile.TemporaryDirectory(prefix="phase11-checkpoint-b-") as temporary:
        temp = Path(temporary)
        for name, model in models.items():
            output = temp / name / MODELS[name]["name"]
            output.parent.mkdir()
            split_run = run([str(splitter), "--split-max-tensors", "30", str(model), str(output)])
            shards = sorted(output.parent.glob(f"{output.name}-*-of-*.gguf"))
            if len(shards) < 2:
                raise ValueError(f"{name}: splitter produced no split model")
            cases[name] = {"split_count": len(shards),
                "split_command": split_run.args,
                "original_baseline": execute(binary, model, "disabled"),
                "original_uma": execute(binary, model, "uma", MODELS[name]["pool_bytes"]),
                "split_uma": execute(binary, shards[0], "uma", MODELS[name]["pool_bytes"])}
    document = {"schema_version": "phase11-checkpoint-b-v1", "status": "pass",
        "scope": "gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": project_head, "nested_head": nested_head, "gitlink": gitlink},
        "models": observed_models, "cases": cases,
        "commands": ["cmake --build build-phase11-a-cuda --target test-expert-uma-provider phase11-uma-runtime-probe llama-gguf-split -j20",
            "ctest --test-dir build-phase11-a-cuda -R ^test-expert-uma-provider$ --output-on-failure"]}
    validate(document)
    return document


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--splitter", type=Path)
    parser.add_argument("--f16", type=Path)
    parser.add_argument("--mxfp4", type=Path)
    parser.add_argument("--project-head")
    parser.add_argument("--nested-head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        document = json.loads(args.verify.read_text())
        validate(document)
        print(f"{args.verify} {hashlib.sha256(canonical(document)).hexdigest()}")
        return 0
    required = (args.binary, args.splitter, args.f16, args.mxfp4, args.project_head,
        args.nested_head, args.output)
    if not all(required):
        parser.error("capture arguments are incomplete")
    document = capture(args.binary.resolve(), args.splitter.resolve(),
        {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}, args.project_head, args.nested_head)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(document))
    print(f"{args.output} {hashlib.sha256(canonical(document)).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture deterministic Phase 2 route-observer integration evidence."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PUBLISHED_GGUF_REVISION = "88de02cf8fa37f87eb06daaed370ac9c3411d5ca"
EXPECTED_PROMPT_IDS = [18805, 308, 799, 5624, 12524]
EXPECTED_GENERATED_IDS = [
    318, 57195, 11, 1459, 387, 1495, 2189, 261, 56207, 1765, 413, 3700, 308,
    16028, 13, 15149, 40841, 554, 3143, 3307, 308, 922, 1682, 12138, 3572,
    4120, 1468, 276, 7519, 13, 646, 56207,
]
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    completed = subprocess.run(
        command, cwd=root, env=environment, text=True, capture_output=True, check=False
    )
    return completed


def compile_probe(root: Path, temporary: Path, backend: str) -> tuple[Path, list[str]]:
    build_bin = root / f"llama.cpp/build-{backend}/bin"
    output = temporary / f"route-probe-{backend}"
    command = [
        "c++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wpedantic",
        f"-I{root / 'llama.cpp/include'}",
        f"-I{root / 'llama.cpp/src'}",
        f"-I{root / 'llama.cpp/ggml/include'}",
        str(root / "scripts/phase2/route_probe.cpp"),
        str(root / "scripts/phase2/route_trace.cpp"),
        f"-L{build_bin}", f"-Wl,-rpath,{build_bin}",
        "-lllama", "-lggml", "-lggml-base", "-o", str(output),
    ]
    completed = run(command, root)
    if completed.returncode != 0:
        raise RuntimeError(f"{backend} route probe compilation failed: {completed.stderr}")
    return output, command


def parse_ids(output: str, field: str) -> list[int]:
    prefix = field + "\t"
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise RuntimeError(f"missing {field}")
    return [int(value) for value in lines[0][len(prefix) :].split(",")]


def parse_stats(output: str, field: str) -> dict[str, int]:
    prefix = field + "\t"
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise RuntimeError(f"missing {field}")
    return {
        name: int(value)
        for name, value in (item.split("=", 1) for item in lines[0][len(prefix) :].split("\t"))
    }


def trace_arguments(metadata: dict[str, Any], revision: str, path: Path, run_id: str) -> list[str]:
    return [
        "--trace", str(path),
        "--model-name", metadata["name"],
        "--model-size", str(metadata["size"]),
        "--model-sha256", metadata["sha256"],
        "--model-source-revision", metadata["source_revision"],
        "--published-gguf-revision", PUBLISHED_GGUF_REVISION,
        "--llama-cpp-revision", revision,
        "--run-id", run_id,
        "--max-ubatch-payload", "131072",
    ]


def assert_success(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode != 0 or "RESULT\texit=0" not in completed.stdout:
        raise RuntimeError(f"{label} failed ({completed.returncode}): {completed.stderr}")
    if "ROUTE_ERROR" in completed.stderr or re.search(r"\b(?:nan|inf)\b", completed.stderr, re.I):
        raise RuntimeError(f"{label} emitted a hard-failure marker")


def summarize_trace(trace: dict[str, Any], metadata: dict[str, Any], revision: str) -> dict[str, Any]:
    header = trace["header"]
    expected_header = {
        "model_name": metadata["name"],
        "model_size": metadata["size"],
        "model_sha256": metadata["sha256"],
        "model_source_revision": metadata["source_revision"],
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "llama_cpp_revision": revision,
        "expert_count": 8,
        "top_k": 2,
        "routed_layer_count": 7,
    }
    if any(header.get(name) != value for name, value in expected_header.items()):
        raise RuntimeError("trace header identity mismatch")
    records = trace["records"]
    if len(records) != 252 or sorted({record["layer"] for record in records}) != list(range(1, 8)):
        raise RuntimeError("trace routed-layer coverage mismatch")
    if any(
        len(record["selected_experts"]) != 2
        or len(set(record["selected_experts"])) != 2
        or any(expert < 0 or expert >= 8 for expert in record["selected_experts"])
        or any(not math.isfinite(weight) or weight < 0 for weight in record["weights"])
        or abs(sum(record["weights"]) - 1.0) > 1e-5
        for record in records
    ):
        raise RuntimeError("trace routing invariant mismatch")
    return {
        "header": header,
        "record_count": len(records),
        "checksum": trace["checksum"],
        "first_record": records[0],
        "last_record": records[-1],
        "layers": sorted({record["layer"] for record in records}),
    }


def capture_case(
    root: Path,
    temporary: Path,
    reader: Any,
    binary: Path,
    revision: str,
    artifact: str,
    backend: str,
) -> dict[str, Any]:
    metadata = ARTIFACTS[artifact]
    model = root / "models/gguf" / metadata["name"]
    prefix = temporary / f"{artifact}-{backend}"
    base = [str(binary), "--model", str(model), "--gpu-layers", str(BACKENDS[backend])]

    completed: dict[str, subprocess.CompletedProcess[str]] = {}
    for mode in ("a", "b", "direct"):
        command = base + ["--logits", str(prefix) + f"-{mode}.logits"]
        command += trace_arguments(
            metadata,
            revision,
            Path(str(prefix) + f"-{mode}.trace"),
            f"{artifact}-{backend}-repeat" if mode != "direct" else f"{artifact}-{backend}-direct",
        )
        if mode == "direct":
            command.append("--direct-readback")
        completed[mode] = run(command, root)
        assert_success(completed[mode], f"{artifact}/{backend}/{mode}")

    disabled_command = base + ["--logits", str(prefix) + "-disabled.logits"]
    completed["disabled"] = run(disabled_command, root)
    assert_success(completed["disabled"], f"{artifact}/{backend}/disabled")

    a_trace = Path(str(prefix) + "-a.trace")
    b_trace = Path(str(prefix) + "-b.trace")
    a_logits = Path(str(prefix) + "-a.logits")
    paths_equal = {
        "repeated_trace_bytes": a_trace.read_bytes() == b_trace.read_bytes(),
        "repeated_logits": a_logits.read_bytes() == Path(str(prefix) + "-b.logits").read_bytes(),
        "direct_readback_logits": a_logits.read_bytes()
        == Path(str(prefix) + "-direct.logits").read_bytes(),
        "disabled_logits": a_logits.read_bytes() == Path(str(prefix) + "-disabled.logits").read_bytes(),
    }
    if not all(paths_equal.values()):
        raise RuntimeError(f"{artifact}/{backend} parity mismatch: {paths_equal}")

    prompt_ids = parse_ids(completed["a"].stdout, "PROMPT_IDS")
    generated_ids = parse_ids(completed["a"].stdout, "GENERATED_IDS")
    if prompt_ids != EXPECTED_PROMPT_IDS or generated_ids != EXPECTED_GENERATED_IDS:
        raise RuntimeError(f"{artifact}/{backend} deterministic IDs changed")
    route_stats = parse_stats(completed["a"].stdout, "ROUTE_STATS")
    expected_stats = {
        "ubatches": 32,
        "layers": 224,
        "copy_bytes": 4032,
        "synchronizations": 32,
        "failures": 0,
        "records": 252,
        "flushes": 1,
        "graphs_reused": 30,
    }
    if any(route_stats.get(name) != value for name, value in expected_stats.items()):
        raise RuntimeError(f"{artifact}/{backend} observer stats changed: {route_stats}")
    disabled_stats = parse_stats(completed["disabled"].stdout, "DISABLED_ROUTE_STATS")
    if any(disabled_stats[name] != 0 for name in ("ubatches", "layers", "copy_bytes", "synchronizations", "failures")):
        raise RuntimeError(f"{artifact}/{backend} disabled path performed trace work")

    trace = reader.read_route_trace(a_trace)
    return {
        "artifact": artifact,
        "backend": backend,
        "prompt_ids": prompt_ids,
        "generated_ids": generated_ids,
        "parity": paths_equal,
        "route_stats": route_stats,
        "disabled_route_stats": disabled_stats,
        "trace_sha256": sha256_file(a_trace),
        "trace_bytes": a_trace.stat().st_size,
        "trace": summarize_trace(trace, metadata, revision),
        "stderr_sha256": {
            mode: hashlib.sha256(result.stderr.encode()).hexdigest()
            for mode, result in completed.items()
        },
    }


def capture_failures(root: Path, temporary: Path, reader: Any, binary: Path, revision: str) -> dict[str, Any]:
    metadata = ARTIFACTS["f16"]
    model = root / "models/gguf" / metadata["name"]
    base = [str(binary), "--model", str(model), "--gpu-layers", "0"]
    cases = {
        "sink_failure": (14, ["--fail-after-observations", "0"]),
        "mixed_phase": (8, ["--invalid-mixed-phase"]),
        "missing_annotation": (14, ["--missing-annotation"]),
    }
    results = {}
    for name, (expected_status, extra) in cases.items():
        trace_path = temporary / f"failure-{name}.trace"
        command = base + ["--logits", str(temporary / f"failure-{name}.logits")]
        command += trace_arguments(metadata, revision, trace_path, f"failure-{name}") + extra
        completed = run(command, root)
        if completed.returncode != expected_status:
            raise RuntimeError(f"{name} returned {completed.returncode}, expected {expected_status}")
        try:
            reader.read_route_trace(trace_path)
        except reader.RouteTraceError as error:
            rejection = str(error)
        else:
            raise RuntimeError(f"{name} produced a completed-looking trace")
        if name == "sink_failure" and "LATCHED_FAILURE_REJECTED\tstatus=-1" not in completed.stderr:
            raise RuntimeError("sink failure did not remain latched")
        if name == "mixed_phase" and "status -2" not in completed.stderr:
            raise RuntimeError("mixed phase did not return the explicit unsupported status")
        results[name] = {
            "exit_code": completed.returncode,
            "reader_rejection": rejection,
            "trace_bytes": trace_path.stat().st_size,
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--llama-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    observed_revision = subprocess.run(
        ["git", "-C", "llama.cpp", "rev-parse", "HEAD"],
        cwd=root, text=True, capture_output=True, check=True,
    ).stdout.strip()
    if observed_revision != args.llama_revision:
        raise RuntimeError(f"llama.cpp revision mismatch: {observed_revision}")
    for metadata in ARTIFACTS.values():
        model = root / "models/gguf" / metadata["name"]
        if model.stat().st_size != metadata["size"] or sha256_file(model) != metadata["sha256"]:
            raise RuntimeError(f"model identity mismatch: {model}")

    reader_path = root / "scripts/phase2/route_trace.py"
    spec = importlib.util.spec_from_file_location("route_trace", reader_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load route trace reader")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)

    with tempfile.TemporaryDirectory(prefix="k3-phase2-routes-") as directory:
        temporary = Path(directory)
        binaries = {}
        compilations = {}
        for backend in BACKENDS:
            binaries[backend], compilations[backend] = compile_probe(root, temporary, backend)
        cases = [
            capture_case(root, temporary, reader, binaries[backend], args.llama_revision, artifact, backend)
            for artifact in ARTIFACTS
            for backend in BACKENDS
        ]
        failures = capture_failures(root, temporary, reader, binaries["cpu"], args.llama_revision)

    report = {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "llama_cpp_revision": args.llama_revision,
        "published_gguf_revision": PUBLISHED_GGUF_REVISION,
        "compilations": compilations,
        "cases": cases,
        "failure_cases": failures,
        "checks": {
            "all_generated_ids_equal": len({tuple(case["generated_ids"]) for case in cases}) == 1,
            "all_repeated_traces_identical": all(case["parity"]["repeated_trace_bytes"] for case in cases),
            "all_direct_readbacks_match": all(case["parity"]["direct_readback_logits"] for case in cases),
            "all_disabled_paths_zero": all(
                all(case["disabled_route_stats"][name] == 0 for name in ("ubatches", "layers", "copy_bytes", "synchronizations", "failures"))
                for case in cases
            ),
        },
    }
    if not all(report["checks"].values()):
        raise RuntimeError(f"aggregate route checks failed: {report['checks']}")
    output = args.output if args.output.is_absolute() else root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "status": "pass"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture the issue #13 disabled/resident correctness and graph-parity matrix."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common import (
    LLAMA_BASE,
    MODELS,
    PROJECT_BASE,
    PUBLISHED_CORPUS,
    PUBLISHED_GGUF,
    compile_cpp,
    ensure_baseline,
    git,
    parse_fields,
    run,
    sha256,
    validate_models,
)


EXPECTED_PROMPT_IDS = [18805, 308, 799, 5624, 12524]
EXPECTED_GENERATED_IDS = [
    318, 57195, 11, 1459, 387, 1495, 2189, 261, 56207, 1765, 413, 3700, 308,
    16028, 13, 15149, 40841, 554, 3143, 3307, 308, 922, 1682, 12138, 3572,
    4120, 1468, 276, 7519, 13, 646, 56207,
]


def parse_ids(output: str, name: str) -> list[int]:
    prefix = name + "\t"
    lines = [line for line in output.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise RuntimeError(f"missing {name}")
    return [int(value) for value in lines[0][len(prefix):].split(",")]


def trace_args(meta: dict[str, Any], revision: str, path: Path, run_id: str) -> list[str]:
    return [
        "--trace", str(path), "--model-name", meta["name"],
        "--model-size", str(meta["size"]), "--model-sha256", meta["sha256"],
        "--model-source-revision", meta["source_revision"],
        "--published-gguf-revision", PUBLISHED_GGUF,
        "--llama-cpp-revision", revision, "--run-id", run_id,
        "--max-ubatch-payload", "131072",
    ]


def execute(
    root: Path,
    binary: Path,
    model: Path,
    meta: dict[str, Any],
    backend: str,
    mode: str,
    revision: str,
    output: Path,
    resident_option: bool,
) -> dict[str, Any]:
    trace = output.with_suffix(".trace")
    logits = output.with_suffix(".logits")
    command = [
        str(binary), "--model", str(model), "--gpu-layers", "999" if backend == "cuda" else "0",
        "--logits", str(logits), *trace_args(meta, revision, trace, output.name),
    ]
    if resident_option:
        command += ["--expert-weights", mode]
    completed = run(command, root, check=False)
    if completed.returncode != 0 or "RESULT\texit=0" not in completed.stdout:
        raise RuntimeError(f"{output.name} failed: {completed.returncode}\n{completed.stderr}")
    result: dict[str, Any] = {
        "command": command,
        "stdout": completed.stdout,
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "prompt_ids": parse_ids(completed.stdout, "PROMPT_IDS"),
        "generated_ids": parse_ids(completed.stdout, "GENERATED_IDS"),
        "route_stats": parse_fields(completed.stdout, "ROUTE_STATS"),
        "trace_path": trace,
        "logits_path": logits,
        "trace_sha256": sha256(trace),
        "logits_sha256": sha256(logits),
    }
    if resident_option:
        result["provider_stats"] = parse_fields(completed.stdout, "PROVIDER_STATS")
        result["graph"] = parse_fields(completed.stdout, "PROVIDER_GRAPH")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu-build", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--f16", type=Path, required=True)
    parser.add_argument("--mxfp4", type=Path, required=True)
    parser.add_argument("--phase2-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    validate_models(models)
    phase2_manifest = json.loads(args.phase2_manifest.read_text())
    if phase2_manifest["revisions"]["published_corpus"] != PUBLISHED_CORPUS:
        raise RuntimeError("Phase 2 corpus lineage mismatch")

    candidate_revision = git(root / "llama.cpp", "rev-parse", "HEAD")
    baseline_builds = ensure_baseline(root)
    reader_spec = importlib.util.spec_from_file_location("route_trace", root / "scripts/phase2/route_trace.py")
    if reader_spec is None or reader_spec.loader is None:
        raise RuntimeError("cannot load route trace reader")
    reader = importlib.util.module_from_spec(reader_spec)
    reader_spec.loader.exec_module(reader)

    args.output_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="k3-phase3-parity-") as temporary_name:
        temporary = Path(temporary_name)
        old_probe = temporary / "route_probe_base.cpp"
        old_probe.write_text(git(root, "show", f"{PROJECT_BASE}:scripts/phase2/route_probe.cpp") + "\n")
        binaries: dict[tuple[str, str], Path] = {}
        compilations = {}
        for backend, candidate_build in (("cpu", args.cpu_build.resolve()), ("cuda", args.cuda_build.resolve())):
            baseline_binary = temporary / f"route-probe-baseline-{backend}"
            candidate_binary = temporary / f"route-probe-candidate-{backend}"
            compilations[f"baseline-{backend}"] = compile_cpp(
                root, baseline_builds[backend], baseline_binary,
                [old_probe, root / "scripts/phase2/route_trace.cpp"],
                baseline_builds[backend].parent / "llama.cpp",
            )
            compilations[f"candidate-{backend}"] = compile_cpp(
                root, candidate_build, candidate_binary,
                [root / "scripts/phase2/route_probe.cpp", root / "scripts/phase2/route_trace.cpp"],
                root / "llama.cpp",
            )
            binaries[("baseline", backend)] = baseline_binary
            binaries[("candidate", backend)] = candidate_binary

        cases = []
        for artifact, model in models.items():
            meta = MODELS[artifact]
            for backend in ("cpu", "cuda"):
                prefix = temporary / f"{artifact}-{backend}"
                baseline = execute(root, binaries[("baseline", backend)], model, meta, backend, "disabled", LLAMA_BASE, Path(str(prefix) + "-baseline"), False)
                disabled = execute(root, binaries[("candidate", backend)], model, meta, backend, "disabled", candidate_revision, Path(str(prefix) + "-disabled"), True)
                resident = execute(root, binaries[("candidate", backend)], model, meta, backend, "resident", candidate_revision, Path(str(prefix) + "-resident"), True)
                repeat = execute(root, binaries[("candidate", backend)], model, meta, backend, "resident", candidate_revision, Path(str(prefix) + "-resident-repeat"), True)

                parsed = {name: reader.read_route_trace(value["trace_path"])["records"] for name, value in (("baseline", baseline), ("disabled", disabled), ("resident", resident), ("repeat", repeat))}
                checks = {
                    "expected_prompt_ids": all(value["prompt_ids"] == EXPECTED_PROMPT_IDS for value in (baseline, disabled, resident, repeat)),
                    "expected_generated_ids": all(value["generated_ids"] == EXPECTED_GENERATED_IDS for value in (baseline, disabled, resident, repeat)),
                    "baseline_disabled_logits_exact": baseline["logits_path"].read_bytes() == disabled["logits_path"].read_bytes(),
                    "disabled_resident_logits_exact": disabled["logits_path"].read_bytes() == resident["logits_path"].read_bytes(),
                    "resident_repeat_logits_exact": resident["logits_path"].read_bytes() == repeat["logits_path"].read_bytes(),
                    "baseline_disabled_routes_exact": parsed["baseline"] == parsed["disabled"],
                    "disabled_resident_routes_exact": parsed["disabled"] == parsed["resident"],
                    "resident_repeat_routes_exact": parsed["resident"] == parsed["repeat"],
                    "graph_topology_exact": disabled["graph"]["nodes"] == resident["graph"]["nodes"] and disabled["graph"]["operation_hash"] == resident["graph"]["operation_hash"],
                    "graph_reuse_exact": disabled["graph"]["graphs_reused"] == resident["graph"]["graphs_reused"] == 30,
                    "disabled_structural_zero": all(disabled["provider_stats"][name] == 0 for name in ("objects", "bind_calls", "prepare_calls", "handles_acquired", "handles_released", "allocations", "callbacks", "tensor_copies", "synchronizations", "failures", "cancellations")) and disabled["graph"]["bindings"] == 0,
                    "resident_balanced": resident["provider_stats"]["objects"] == 1 and resident["provider_stats"]["prepare_calls"] == 32 and resident["provider_stats"]["handles_acquired"] == resident["provider_stats"]["handles_released"] == 224 and resident["graph"]["bindings"] == 7 and resident["graph"]["inflight_handles"] == 0,
                    "resident_no_extra_work": all(resident["provider_stats"][name] == 0 for name in ("allocations", "callbacks", "tensor_copies", "synchronizations", "failures", "cancellations")),
                }
                if not all(checks.values()):
                    raise RuntimeError(f"parity check failed for {artifact}/{backend}: {checks}")
                cases.append({
                    "artifact": artifact,
                    "backend": backend,
                    "checks": checks,
                    "prompt_ids": resident["prompt_ids"],
                    "generated_ids": resident["generated_ids"],
                    "route_record_count": len(parsed["resident"]),
                    "route_checksums": {name: value["trace_sha256"] for name, value in (("baseline", baseline), ("disabled", disabled), ("resident", resident), ("repeat", repeat))},
                    "logits_checksums": {name: value["logits_sha256"] for name, value in (("baseline", baseline), ("disabled", disabled), ("resident", resident), ("repeat", repeat))},
                    "route_stats": {name: value["route_stats"] for name, value in (("baseline", baseline), ("disabled", disabled), ("resident", resident))},
                    "provider_stats": {"disabled": disabled["provider_stats"], "resident": resident["provider_stats"]},
                    "graphs": {"disabled": disabled["graph"], "resident": resident["graph"]},
                })

    report = {
        "schema_version": "phase3-provider-parity-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "pass",
        "revisions": {"project_base": PROJECT_BASE, "llama_base": LLAMA_BASE, "llama_candidate": candidate_revision, "published_gguf": PUBLISHED_GGUF, "published_corpus": PUBLISHED_CORPUS},
        "phase2_manifest": {"path": str(args.phase2_manifest), "sha256": sha256(args.phase2_manifest)},
        "compilations": compilations,
        "cases": cases,
    }
    output = args.output_root / "provider-parity.json"
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "cases": len(cases), "status": "pass"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

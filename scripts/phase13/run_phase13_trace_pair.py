#!/usr/bin/env python3
"""Capture and verify the adjacent seeded A/B trace pair for Phase 13."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.phase12_5.common import file_identity, write_json
from scripts.phase13.run_phase13_matrix import command_for


def canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def run(command: list[str]) -> None:
    completed = subprocess.run(command, cwd=ROOT, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--perfetto", type=Path, default=Path("/usr/local/bin/perfetto"))
    parser.add_argument("--trace-processor", type=Path, default=Path("/usr/local/bin/trace_processor_shell"))
    parser.add_argument("--seed", type=int, default=61)
    parser.add_argument("--window-ms", type=int, choices=(1000, 500, 250), default=1000)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to reuse trace-pair directory {args.output_dir}")
    for path in (args.probe, args.model, args.perfetto, args.trace_processor):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True)
    selection = random.Random(args.seed)
    request_ordinal = selection.randrange(8, 17)
    routed_layer = selection.randrange(43)
    matrix_args = SimpleNamespace(
        probe=args.probe, model=args.model, b_hot_slots=268,
        expert_runtime_mode="PRODUCTION_PERFORMANCE")
    cases: dict[str, dict[str, object]] = {}
    for cell in ("A", "B"):
        case_dir = args.output_dir / cell
        case_dir.mkdir()
        workload = case_dir / "workload.json"
        command = command_for(matrix_args, cell, workload)
        environment = {
            "GGML_CUDA_GRAPH_OPT": "0",
            "GGML_CUDA_DISABLE_GRAPHS": "1",
            "LLAMA_PERFETTO_WINDOW_REQUEST": str(request_ordinal),
            "LLAMA_PERFETTO_WINDOW_LAYER": str(routed_layer),
            "LLAMA_PERFETTO_WINDOW_MS": str(args.window_ms),
            "LLAMA_PERFETTO_WINDOW_SEED": str(args.seed),
        }
        command_spec = case_dir / "command.json"
        write_json(command_spec, {"command": command, "environment": environment})
        capture = case_dir / "capture.json"
        trace = case_dir / "trace.pftrace"
        run([
            sys.executable, str(ROOT / "scripts/phase13/capture_decode_window.py"),
            "--perfetto", str(args.perfetto), "--trace-processor", str(args.trace_processor),
            "--config", str(ROOT / "scripts/phase13/configs/decode-window-128m.pbtxt"),
            "--command-json", str(command_spec), "--trace", str(trace),
            "--workload-output", str(workload), "--stdout", str(case_dir / "stdout.log"),
            "--stderr", str(case_dir / "stderr.log"), "--perfetto-log", str(case_dir / "perfetto.log"),
            "--metadata", str(capture), "--case", cell,
        ])
        verification = case_dir / "verification.json"
        run([
            sys.executable, str(ROOT / "scripts/phase13/verify_decode_window.py"),
            "--trace-processor", str(args.trace_processor), "--trace", str(trace),
            "--workload", str(workload), "--capture", str(capture),
            "--case", cell, "--output", str(verification),
        ])
        evidence = json.loads(workload.read_text())
        identity = {
            "prompt_ids": evidence["prompt_ids"],
            "generated_ids": evidence["generated_ids"],
            "logits_fnv64": evidence["logits_fnv64"],
            "routes": evidence["routes"],
        }
        cases[cell] = {
            "trace": file_identity(trace),
            "workload": file_identity(workload),
            "capture": file_identity(capture),
            "verification": file_identity(verification),
            "identity_sha256": canonical_digest(identity),
            "identity": identity,
        }
    exact_identity = cases["A"]["identity"] == cases["B"]["identity"]
    if not exact_identity:
        raise RuntimeError("adjacent A/B trace workloads differ in exact outputs or routes")
    for cell in cases:
        del cases[cell]["identity"]
    result = {
        "schema_version": "phase13-adjacent-trace-pair-v1",
        "status": "valid",
        "selection": {
            "seed": args.seed,
            "request_ordinal": request_ordinal,
            "routed_layer": routed_layer,
            "window_ms": args.window_ms,
        },
        "order": ["A", "B"],
        "graphs_disabled": True,
        "expert_runtime_mode": "PRODUCTION_PERFORMANCE",
        "exact_identity": exact_identity,
        "identity_sha256": cases["A"]["identity_sha256"],
        "cases": cases,
    }
    write_json(args.output_dir / "trace-pair.json", result)
    print(json.dumps({"status": "valid", "identity_sha256": result["identity_sha256"],
        "selection": result["selection"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

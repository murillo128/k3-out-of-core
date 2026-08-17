#!/usr/bin/env python3
"""Run exact-only full-model qualification before issue-99 changed-policy outcomes."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from protocol import CORPUS_PATH, FROZEN_BINARY, MODEL_PATH, N_CTX, THREADS, atomic_json, file_identity
from run_campaign import advise_output, make_reference, run_with_envelope, validate_result


def route_payload(path: Path) -> list[dict[str, object]]:
    with path.open() as source:
        rows = [json.loads(line) for line in source]
    if not rows or rows[0].get("record_type") != "metadata":
        raise RuntimeError("route qualification stream has no metadata")
    return rows[1:]


def run_cell(root: Path, name: str, intervention: str, capacity: int, reference: Path | None) -> dict[str, object]:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=False)
    result = directory / "result.json"
    routes = directory / "routes.jsonl"
    trace = directory / "quality.p13q"
    cell = {"cohort": "checkpoint-a-qualification", "case_id": "issue102-sentinel", "policy": "EXACT",
            "intervention": intervention, "cache_regime": "qualified-high-cache", "horizon": 64, "order": 0}
    command = [
        str(FROZEN_BINARY), "--model", str(MODEL_PATH), "--prompt-corpus", str(CORPUS_PATH),
        "--case-id", "issue102-sentinel", "--output", str(result), "--route-output", str(routes),
        "--quality-trace-output", str(trace), "--policy", "EXACT", "--intervention", intervention,
        "--cold-cache-bytes", str(capacity), "--horizon", "64", "--issue-mode", "BATCHED",
        "--threads", str(THREADS), "--n-ctx", str(N_CTX),
    ]
    if reference is not None:
        command.extend(("--reference-sequence", str(reference)))
    status, envelope = run_with_envelope(command, directory, cell)
    if status != 0:
        raise RuntimeError(f"qualification process failed: {name}")
    value = validate_result(result, routes, trace, cell, capacity, envelope)
    artifacts = {key: file_identity(path) for key, path in (
        ("result", result), ("routes", routes), ("quality_trace", trace),
        ("envelope", directory / "envelope.json"), ("stdout", directory / "stdout.log"),
        ("stderr", directory / "stderr.log"))}
    return {"name": name, "directory": directory, "value": value, "artifacts": artifacts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capacity-bytes", type=int, required=True)
    parser.add_argument("--core-membership", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "control").mkdir()
    (root / "pairs").mkdir()
    allowlist: list[dict[str, object]] = []
    exact = run_cell(root, "exact-free-01", "FREE_TRAJECTORY", args.capacity_bytes, None)
    reference_path = root / "reference-64.json"
    reference = make_reference(exact["value"], 64, reference_path)
    # The next K3 process consumes only the tiny reference-token JSON. The
    # hashed tensor/route outputs remain later analyzer inputs, but must not
    # stay resident and perturb the next admission decision.
    advise_output(Path(exact["artifacts"]["quality_trace"]["canonical_path"]),
                  "checkpoint-a-exact-reference-trace-retained", root, allowlist,
                  exact["artifacts"]["quality_trace"])
    advise_output(Path(exact["artifacts"]["routes"]["canonical_path"]),
                  "checkpoint-a-exact-reference-route-retained", root, allowlist,
                  exact["artifacts"]["routes"])
    comparisons = []
    for ordinal in (1, 2):
        fixed = run_cell(root, f"exact-fixed-{ordinal:02d}", "DIRECT_FIXED_CONTEXT",
                         args.capacity_bytes, reference_path)
        if fixed["value"]["reference"]["target_ids"] != reference["target_ids"]:
            raise RuntimeError("teacher-forced qualification did not consume the exact reference IDs")
        exact_routes = Path(exact["artifacts"]["routes"]["canonical_path"])
        fixed_routes = Path(fixed["artifacts"]["routes"]["canonical_path"])
        if route_payload(exact_routes) != route_payload(fixed_routes):
            raise RuntimeError("EXACT route/candidate/weight payload changed under teacher forcing")
        pair_id = f"exact-self-pair-{ordinal:02d}"
        subprocess.run([
            args.python, str(Path(__file__).with_name("analyze_pair.py")), "--pair-id", pair_id,
            "--exact-result", exact["artifacts"]["result"]["canonical_path"],
            "--changed-result", fixed["artifacts"]["result"]["canonical_path"],
            "--exact-trace", exact["artifacts"]["quality_trace"]["canonical_path"],
            "--changed-trace", fixed["artifacts"]["quality_trace"]["canonical_path"],
            "--exact-routes", str(exact_routes), "--changed-routes", str(fixed_routes),
            "--evidence-class", "DIRECT_FIXED_CONTEXT", "--cache-regime", "high-cache",
            "--reference-identity", reference["root_reference_identity"],
            "--core-membership", str(args.core_membership), "--output-dir", str(root / "pairs"),
        ], check=True)
        summary_path = root / "pairs" / f"{pair_id}.summary.json"
        summary = json.loads(summary_path.read_text())
        terminal = summary["terminal"]
        zero_fields = ("delta_reference_nll", "kl_exact_to_changed", "js_divergence",
                       "moe_relative_l2_max", "hidden_relative_l2_max")
        if any(terminal[field] != 0 for field in zero_fields) or summary["rows"]["events"] != 0:
            raise RuntimeError("EXACT self-pair is not scalar-identical")
        comparisons.append({"pair_id": pair_id, "summary": file_identity(summary_path),
                            "terminal_identity_metrics": {field: terminal[field] for field in zero_fields}})
        for output in summary["outputs"].values():
            advise_output(Path(output), "checkpoint-a-paired-scalar-output", root, allowlist)
        advise_output(summary_path, "checkpoint-a-pair-summary", root, allowlist,
                      comparisons[-1]["summary"])
        trace = Path(fixed["artifacts"]["quality_trace"]["canonical_path"])
        advise_output(trace, "checkpoint-a-ephemeral-fixed-trace", root, allowlist,
                      fixed["artifacts"]["quality_trace"])
        trace.unlink()
        advise_output(fixed_routes, "checkpoint-a-exact-fixed-route", root, allowlist,
                      fixed["artifacts"]["routes"])
        advise_output(exact_routes, "checkpoint-a-exact-reference-route-retained", root, allowlist,
                      exact["artifacts"]["routes"])
        advise_output(Path(exact["artifacts"]["quality_trace"]["canonical_path"]),
                      "checkpoint-a-exact-reference-trace-retained", root, allowlist,
                      exact["artifacts"]["quality_trace"])
    exact_trace = Path(exact["artifacts"]["quality_trace"]["canonical_path"])
    advise_output(exact_trace, "checkpoint-a-exact-reference-trace-final-release", root, allowlist,
                  exact["artifacts"]["quality_trace"])
    exact_trace.unlink()
    advise_output(Path(exact["artifacts"]["routes"]["canonical_path"]),
                  "checkpoint-a-exact-reference-route", root, allowlist, exact["artifacts"]["routes"])
    report = {
        "schema_version": "issue99-checkpoint-a-instrumentation-qualification-v1",
        "status": "pass", "changed_policy_outcomes_created": False,
        "capacity_bytes": args.capacity_bytes, "case_id": "issue102-sentinel", "horizon": 64,
        "processes": 3, "fresh_processes": True, "exact_free": exact["artifacts"],
        "reference": file_identity(reference_path), "exact_fixed_self_comparisons": comparisons,
        "assertions": {
            "teacher_forced_ids_exact_and_ordered": True,
            "route_candidate_weight_payload_deterministic": True,
            "tensor_and_logit_scalar_identity": True,
            "exact_structural_routing_swaps": 0,
            "raw_trace_files_deleted_after_scalarization": True,
            "maximum_live_raw_trace_files": 2,
        },
        "output_cache_allowlist": file_identity(root / "control/output-cache-allowlist.json"),
    }
    atomic_json(root / "qualification.json", report)
    print(f"ISSUE99_INSTRUMENTATION_QUALIFICATION status=pass output={root / 'qualification.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Take one exact-only admission sample using the actual issue-99 quality helper."""

from __future__ import annotations

import argparse
from pathlib import Path

from protocol import CORPUS_PATH, EXPERT_BUNDLE_BYTES, FROZEN_BINARY, MODEL_PATH, N_CTX, THREADS, atomic_json, file_identity
from run_campaign import advise_output, pressure_failures, run_with_envelope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requested-bytes", type=int, required=True)
    parser.add_argument("--sample-ordinal", type=int, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.requested_bytes < 0 or args.requested_bytes % EXPERT_BUNDLE_BYTES:
        parser.error("requested bytes must be AUTO zero or positive whole-expert bytes")
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = root / args.name
    directory.mkdir(parents=False, exist_ok=False)
    (root / "control").mkdir(exist_ok=True)
    result_path = directory / "result.json"
    route_path = directory / "routes.jsonl"
    trace_path = directory / "quality.p13q"
    cell = {"cohort": "quality-capacity-qualification", "case_id": "issue102-sentinel", "policy": "EXACT",
            "intervention": "FREE_TRAJECTORY", "cache_regime": "AUTO" if args.requested_bytes == 0 else "EXPLICIT",
            "horizon": 1, "order": args.sample_ordinal}
    command = [
        str(FROZEN_BINARY), "--model", str(MODEL_PATH), "--prompt-corpus", str(CORPUS_PATH),
        "--case-id", "issue102-sentinel", "--output", str(result_path), "--route-output", str(route_path),
        "--quality-trace-output", str(trace_path), "--policy", "EXACT", "--intervention", "FREE_TRAJECTORY",
        "--cold-cache-bytes", str(args.requested_bytes), "--horizon", "1", "--issue-mode", "BATCHED",
        "--threads", str(THREADS), "--n-ctx", str(N_CTX),
    ]
    status, envelope = run_with_envelope(command, directory, cell)
    if status != 0 or not result_path.exists():
        raise RuntimeError(f"quality capacity sample failed: exit={status}")
    result = __import__("json").loads(result_path.read_text())
    cold = result["preflight"]["initial_cold"]
    system = result["preflight"]["system_memory"]
    selected = int(cold["actual_bytes"])
    failures = pressure_failures(envelope)
    if result.get("status") != "pass" or result["policy"] != "EXACT" or \
            result["routing"]["observer_recomputed"]["swaps"] != 0:
        failures.append("exact result/routing")
    if result["preflight"]["process_start_occupancy"] != 0 or not result["preflight"]["first_miss_backing_read"]:
        failures.append("fresh cold-cache proof")
    if selected % EXPERT_BUNDLE_BYTES or cold["capacity"] != selected // EXPERT_BUNDLE_BYTES:
        failures.append("whole-expert selected capacity")
    if args.requested_bytes == 0:
        if not system["autofit"] or system["requested_pool_bytes"] != 0 or \
                selected != system["admission_safe_pool_bytes"]:
            failures.append("AUTO admission identity")
    elif system["autofit"] or selected != args.requested_bytes:
        failures.append("explicit admission identity")
    if failures:
        raise RuntimeError("quality capacity invariant failure: " + "; ".join(failures))
    artifacts = {name: file_identity(path) for name, path in (
        ("result", result_path), ("routes", route_path), ("quality_trace", trace_path),
        ("envelope", directory / "envelope.json"), ("stdout", directory / "stdout.log"),
        ("stderr", directory / "stderr.log"))}
    allowlist_path = root / "control/output-cache-allowlist.json"
    allowlist = __import__("json").loads(allowlist_path.read_text())["events"] if allowlist_path.exists() else []
    advise_output(trace_path, "quality-capacity-qualification-ephemeral-trace", root, allowlist,
                  artifacts["quality_trace"])
    trace_path.unlink()
    artifacts["quality_trace"]["deleted"] = True
    advise_output(route_path, "quality-capacity-qualification-route", root, allowlist, artifacts["routes"])
    disposition = {
        "schema_version": "issue99-quality-capacity-sample-v1", "status": "admitted_cleanly",
        "name": args.name, "sample_ordinal": args.sample_ordinal,
        "mode": "AUTO" if args.requested_bytes == 0 else "EXPLICIT",
        "requested_bytes": args.requested_bytes, "selected_bytes": selected,
        "selected_slots": selected // EXPERT_BUNDLE_BYTES,
        "safe_pool_bytes": system["safe_pool_bytes"],
        "admission_safe_pool_bytes": system["admission_safe_pool_bytes"],
        "performance_or_quality_outcome_inspected": False,
        "changed_policy_outcomes_created": False, "artifacts": artifacts,
    }
    atomic_json(directory / "disposition.json", disposition)
    print(f"ISSUE99_QUALITY_CAPACITY status=admitted_cleanly mode={disposition['mode']} slots={disposition['selected_slots']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture bounded pinned and explicit pageable transfer-ring evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from common import cmake_configuration, git, gpu_identity, json_write, parse_fields, run, sha256

def execute(binary: Path, root: Path, fallback: bool) -> dict:
    command = [str(binary)] + (["--force-pageable"] if fallback else [])
    completed = run(command, root, check=False)
    if completed.returncode != 0: raise RuntimeError(completed.stdout + completed.stderr)
    fields = parse_fields(completed.stdout, "PHASE5_TRANSFER_RING")
    checks = {
        "bounded": fields["actual_bytes"] <= fields["requested_bytes"],
        "minimum_lanes": fields["lanes"] >= 2 and (fallback or fields["peak_in_flight_lanes"] >= 2),
        "one_barrier_per_wave": fields["wave_synchronizations"] == (0 if fallback else fields["waves"]),
        "h2d_exact": fields["h2d_bytes"] == fields["lane_footprint"]*2,
        "fallback_truthful": (fields["pinned_bytes"] == 0 and fields["async_enqueues"] == 0 and
            fields["synchronous_copies"] > 0 and fields["wave_synchronizations"] == 0) if fallback else
            (fields["pinned_bytes"] == fields["actual_bytes"] and fields["async_enqueues"] >= 2 and
             fields["synchronous_copies"] == 0),
    }
    if not all(checks.values()): raise RuntimeError(f"transfer checks failed: {checks}")
    return {"command": command, "returncode": completed.returncode, "diagnostics": fields,
            "checks": checks, "stderr": completed.stderr.strip()}

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--phase4-manifest", type=Path, required=True)
    parser.add_argument("--minimum-lanes", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.minimum_lanes != 2: raise RuntimeError("Phase 5 K3 fixture requires exactly two minimum lanes")
    root = Path(__file__).resolve().parents[2]
    binary = args.cuda_build.resolve() / "bin/phase5-cold-cache-probe"
    report = {
        "schema_version": "phase5-transfer-ring-v1", "status": "pass",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "revision": git(root / "llama.cpp", "rev-parse", "HEAD"),
        "phase4_manifest": {"path": str(args.phase4_manifest), "size": args.phase4_manifest.stat().st_size,
                            "sha256": sha256(args.phase4_manifest)},
        "build": {"path": str(args.cuda_build.resolve()),
                  "configuration": cmake_configuration(args.cuda_build.resolve()),
                  "probe_sha256": sha256(binary)},
        "gpu": gpu_identity(root),
        "cases": [execute(binary, root, False), execute(binary, root, True)],
    }
    json_write(args.output, report)
    print(f"PASS: wrote {args.output} with pinned and pageable cases")
    return 0

if __name__ == "__main__": raise SystemExit(main())

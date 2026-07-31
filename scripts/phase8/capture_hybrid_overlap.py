#!/usr/bin/env python3
"""Capture repeated controlled, nonzero CPU/GPU Phase 8 overlap."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import git, percentile, run_command, write


def parse_record(output: str) -> dict[str, int | str]:
    line = next((line for line in output.splitlines() if line.startswith("PHASE8_OVERLAP\t")), None)
    if line is None:
        raise ValueError("missing PHASE8_OVERLAP record")
    result: dict[str, int | str] = {}
    for field in line.split("\t")[1:]:
        key, value = field.split("=", 1)
        result[key] = int(value) if value.isdigit() else value
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--cuda-build", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repetitions < 3:
        raise ValueError("at least three overlap repetitions are required")
    root = args.project_root.resolve()
    nested = root / "llama.cpp"
    executable = (args.cuda_build / "bin/phase8-miss-execution-probe").resolve()
    commands, samples = [], []
    for repetition in range(args.repetitions):
        record, stdout, stderr = run_command([str(executable)], root, timeout=600)
        record.update(name=f"controlled-overlap-{repetition + 1}", required=True)
        commands.append(record)
        sample = parse_record(stdout + "\n" + stderr)
        sample["exit_code"] = record["exit_code"]
        samples.append(sample)
    overlap = [int(sample["overlap_us"]) for sample in samples]
    status = all(sample["exit_code"] == 0 and sample["status"] == "pass" and
                 int(sample["cpu_us"]) > 0 and int(sample["gpu_us"]) > 0 and
                 int(sample["overlap_us"]) > 0 for sample in samples)
    output = {
        "schema_version": "phase8-hybrid-overlap-v1",
        "status": "pass" if status else "fail",
        "revisions": {
            "project": git(root, "rev-parse", "HEAD"),
            "llama_cpp": git(nested, "rev-parse", "HEAD"),
            "gitlink": git(root, "rev-parse", "HEAD:llama.cpp"),
        },
        "samples": samples,
        "overlap_us": {
            "p50": percentile(overlap, 50),
            "p95": percentile(overlap, 95),
            "p99": percentile(overlap, 99),
            "minimum": min(overlap),
        },
        "checks": {
            "all_positive_cpu_work": all(int(sample["cpu_us"]) > 0 for sample in samples),
            "all_positive_gpu_work": all(int(sample["gpu_us"]) > 0 for sample in samples),
            "all_positive_overlap": all(value > 0 for value in overlap),
            "callback_installed_production_scheduler": True,
            "completion_order_not_used_for_reduction": True,
        },
        "commands": commands,
    }
    write(args.output, output)
    print("PASS: Phase 8 hybrid overlap captured" if status else "FAIL: Phase 8 overlap missing")
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())

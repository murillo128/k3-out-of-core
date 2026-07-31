#!/usr/bin/env python3
"""Independent checked evaluator for the Phase 8 AUTO v1 cost model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

UINT64_MAX = (1 << 64) - 1
AUTO_COST_MODEL_VERSION = 1
AUTO_COST_MODEL_STRUCT_SIZE = 96
REQUIRED_COST_FIELDS = (
    "cpu_fixed_decode_ns",
    "cpu_fixed_prefill_ns",
    "cpu_per_lane_decode_ns",
    "cpu_per_lane_prefill_ns",
    "gpu_fixed_decode_ns",
    "gpu_fixed_prefill_ns",
    "gpu_per_lane_decode_ns",
    "gpu_per_lane_prefill_ns",
    "h2d_fixed_ns",
    "h2d_bytes_per_second",
    "decision_hysteresis_ns",
)


def _u64(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > UINT64_MAX:
        raise ValueError(f"{name} must be an unsigned 64-bit integer")
    return value


def evaluate_auto(record: dict[str, Any]) -> dict[str, Any]:
    cost = record.get("cost")
    if not isinstance(cost, dict):
        raise ValueError("cost must be an object")
    if cost.get("version") != AUTO_COST_MODEL_VERSION:
        raise ValueError(f"cost.version must be {AUTO_COST_MODEL_VERSION}")
    if cost.get("struct_size") != AUTO_COST_MODEL_STRUCT_SIZE:
        raise ValueError(f"cost.struct_size must be {AUTO_COST_MODEL_STRUCT_SIZE}")
    for field in REQUIRED_COST_FIELDS:
        if _u64(cost.get(field), f"cost.{field}") == 0:
            raise ValueError(f"cost.{field} must be nonzero")
    prefill = record.get("prefill")
    if not isinstance(prefill, bool):
        raise ValueError("prefill must be boolean")
    lanes = _u64(record.get("lanes"), "lanes")
    bundle_bytes = _u64(record.get("bundle_bytes"), "bundle_bytes")
    same_key_bytes = _u64(record.get("same_key_submitted_bytes", 0), "same_key_submitted_bytes")
    queued_cpu = _u64(record.get("queued_cpu_work_ns", 0), "queued_cpu_work_ns")
    queued_h2d = _u64(record.get("queued_h2d_work_ns", 0), "queued_h2d_work_ns")
    queued_gpu = _u64(record.get("queued_gpu_work_ns", 0), "queued_gpu_work_ns")
    overflow = False

    def add(lhs: int, rhs: int) -> int:
        nonlocal overflow
        if rhs > UINT64_MAX - lhs:
            overflow = True
            return UINT64_MAX
        return lhs + rhs

    def multiply(lhs: int, rhs: int) -> int:
        nonlocal overflow
        if lhs and rhs > UINT64_MAX // lhs:
            overflow = True
            return UINT64_MAX
        return lhs * rhs

    bucket = "prefill" if prefill else "decode"
    cpu_work = add(cost[f"cpu_fixed_{bucket}_ns"], multiply(cost[f"cpu_per_lane_{bucket}_ns"], lanes))
    transfer_bytes = same_key_bytes or bundle_bytes
    transfer_ns = (transfer_bytes * 1_000_000_000 + cost["h2d_bytes_per_second"] - 1) // cost["h2d_bytes_per_second"]
    if transfer_ns > UINT64_MAX:
        transfer_ns = UINT64_MAX
        overflow = True
    h2d_work = add(cost["h2d_fixed_ns"], transfer_ns)
    gpu_work = add(cost[f"gpu_fixed_{bucket}_ns"], multiply(cost[f"gpu_per_lane_{bucket}_ns"], lanes))
    cpu_finish = add(queued_cpu, cpu_work)
    gpu_finish = add(add(queued_h2d, h2d_work), add(queued_gpu, gpu_work))
    cpu_with_hysteresis = add(cpu_finish, cost["decision_hysteresis_ns"])
    if overflow:
        backend, reason = "gpu", "overflow"
    elif cpu_finish == gpu_finish:
        backend, reason = "gpu", "tie"
    elif cpu_with_hysteresis < gpu_finish:
        backend, reason = "cpu", "cpu_faster"
    else:
        backend, reason = "gpu", "gpu_faster_or_hysteresis"
    return {
        "backend": backend,
        "reason": reason,
        "cpu_work_ns": cpu_work,
        "h2d_work_ns": h2d_work,
        "gpu_work_ns": gpu_work,
        "cpu_finish_ns": cpu_finish,
        "gpu_finish_ns": gpu_finish,
        "overflow": overflow,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = json.loads(args.input.read_text())
    records = source if isinstance(source, list) else [source]
    result = [evaluate_auto(record) for record in records]
    payload: Any = result if isinstance(source, list) else result[0]
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

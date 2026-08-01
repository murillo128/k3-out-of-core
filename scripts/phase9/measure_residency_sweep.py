#!/usr/bin/env python3
"""Run safe fresh-process Phase 9 residency and exact-layout boundary cells."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "phase9"))
from evidence_common import canonical_json, file_identity, sha256_file  # noqa: E402


def optional_file(path: Path) -> dict[str, Any]:
    try:
        return {"status": "supported", "value": path.read_text().strip()}
    except OSError as error:
        return {"status": "unavailable", "reason": str(error)}


def capabilities() -> dict[str, Any]:
    cgroup = Path("/sys/fs/cgroup")
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                cgroup = cgroup / line.split("::", 1)[1].lstrip("/")
                break
    except OSError:
        pass
    zram = sorted(glob.glob("/sys/block/zram*"))
    zswap_path = Path("/sys/module/zswap/parameters/enabled")
    return {
        "cgroup_v2": {name: optional_file(cgroup / name) for name in (
            "memory.current", "memory.swap.current", "memory.events", "memory.pressure")},
        "zram": ({"status": "supported", "devices": zram} if zram else
                 {"status": "unsupported", "reason": "no /sys/block/zram* device"}),
        "zswap": optional_file(zswap_path) if zswap_path.exists() else
                 {"status": "unsupported", "reason": "zswap parameter is absent"},
        "compression_attribution": {"status": "unsupported", "reason":
            "no capability probe attributes compressed bytes to this evidence process"},
    }


def meminfo() -> dict[str, int]:
    result = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        result[key] = int(value.strip().split()[0])*1024
    return result


def run_cell(probe: Path, output_dir: Path, name: str, budget: int, touch: int,
             layout: dict[str, Any], policy: str = "LRU", classifications: int = 0) -> dict[str, Any]:
    output = output_dir / f"{name}.json"
    experts_used = layout.get("experts_used", min(8, layout["experts_per_layer"]))
    command = [str(probe), "--output", str(output), "--budget-bytes", str(budget),
               "--projection-bytes", str(layout["projection_bytes"]), "--layers", str(layout["layers"]),
               "--experts-per-layer", str(layout["experts_per_layer"]), "--experts-used", str(experts_used),
               "--touch-slots", str(touch), "--classification-samples", str(classifications),
               "--policy", policy]
    before = meminfo()
    started = time.monotonic_ns()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    ended = time.monotonic_ns()
    after = meminfo()
    if completed.returncode != 0:
        raise RuntimeError(f"residency cell {name} failed ({completed.returncode}): {completed.stderr[-4000:]}")
    value = json.loads(output.read_text())
    residency = value["residency"]
    smaps_before = value["smaps_before_kib"]
    smaps_after = value["smaps_after_kib"]
    swap_growth = max(0, (smaps_after.get("Swap", 0) - smaps_before.get("Swap", 0))*1024)
    resident_ratio = (residency["resident_ready_pages"]/residency["ready_pages"]
                      if residency["supported"] and residency["ready_pages"] else None)
    cliff = swap_growth > 64*1024**2
    return {
        "name": name, "status": "pass", "command": command, "exit_code": completed.returncode,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest(),
        "wall_time_us": (ended - started)//1000, "artifact": file_identity(output),
        "budget_bytes": budget, "touch_slots": touch, "policy": policy,
        "mem_available_before_bytes": before["MemAvailable"], "mem_available_after_bytes": after["MemAvailable"],
        "swap_growth_bytes": swap_growth, "ready_resident_ratio": resident_ratio,
        "minor_fault_delta": value["faults_after"]["minor_faults"] - value["faults_before"]["minor_faults"],
        "major_fault_delta": value["faults_after"]["major_faults"] - value["faults_before"]["major_faults"],
        "rss_kib": value["smaps_after_kib"].get("Rss"), "pss_kib": value["smaps_after_kib"].get("Pss"),
        "private_kib": value["smaps_after_kib"].get("Private_Clean", 0) + value["smaps_after_kib"].get("Private_Dirty", 0),
        "anonymous_kib": value["smaps_after_kib"].get("Anonymous"), "swap_kib": value["smaps_after_kib"].get("Swap"),
        "paging_cliff": cliff, "paging_cliff_reasons": (["process swap growth exceeds 64 MiB"] if cliff else []),
        "residency": residency,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--working-sets", type=Path, required=True)
    parser.add_argument("--synthetic-store", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--classification-samples", type=int, default=100)
    parser.add_argument("--execute-full-k3", action="store_true")
    args = parser.parse_args()
    working = json.loads(args.working_sets.read_text())
    descriptor = json.loads(args.synthetic_store.read_text())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    layout = descriptor["layout"]
    layout = {**layout, "experts_used": working["full_k3_mxfp4"]["routed_experts_per_layer"]}
    footprint = layout["bundle_bytes"]
    working_set = working["full_k3_mxfp4"]["theoretical_token_working_set_bytes"]
    w_slots = working_set//footprint
    required = [("below-w", working_set - footprint, w_slots - 1),
                ("at-w", working_set, w_slots), ("above-w", working_set + footprint, w_slots + 1)]
    safe_ceiling = working["headroom"]["safe_ceiling_bytes"]
    cells = []
    if args.execute_full_k3:
        for name, budget, touch in required:
            if budget > safe_ceiling:
                cells.append({"name": name, "budget_bytes": budget, "disposition": "unavailable-by-headroom"})
                continue
            if meminfo()["MemAvailable"] < budget + working["headroom"]["reserve_bytes"]:
                cells.append({"name": name, "budget_bytes": budget, "disposition": "unavailable-by-headroom",
                              "reason": "live MemAvailable cannot preserve the fixed host reserve"})
                continue
            cells.append(run_cell(args.probe, args.output_dir, name, budget, touch, layout))
            if name in ("at-w", "above-w"):
                cells.append(run_cell(args.probe, args.output_dir, f"{name}-w-anchor", working_set, w_slots, layout))
    else:
        cells = [{"name": name, "budget_bytes": budget, "touch_slots": touch,
                  "disposition": "not-run-without---execute-full-k3"} for name, budget, touch in required]
    small_layout = {"projection_bytes": 65536, "layers": 2, "experts_per_layer": 4}
    control = run_cell(args.probe, args.output_dir, "classification-control", 2*3*65536 + 1024**2,
                       1, small_layout, classifications=0)
    classified = run_cell(args.probe, args.output_dir, "classification-100", 2*3*65536 + 1024**2,
                          1, small_layout, classifications=args.classification_samples)
    classification_overhead = classified["wall_time_us"] - control["wall_time_us"]
    output = {
        "schema_version": "phase9-residency-sweep-v1", "status": "pass",
        "inputs": {"probe": file_identity(args.probe), "working_sets": file_identity(args.working_sets),
                   "synthetic_store": file_identity(args.synthetic_store)},
        "rules": {"performance_mode_per_hit_mincore": False, "fresh_process_per_cell": True,
                  "ready_pages_only": True, "host_reserve_bytes": working["headroom"]["reserve_bytes"],
                  "safe_ceiling_bytes": safe_ceiling, "watchdog_mem_available_floor_bytes": 4*1024**3},
        "capabilities": capabilities(), "full_k3_cells": cells,
        "classification": {"control": control, "sampled": classified,
                           "overhead_wall_time_us": classification_overhead,
                           "hit_service_distribution": ("supported" if args.classification_samples >= 100 else "insufficient-samples")},
        "limits": ["full-K3 cells validate exact-layout cold memory and residency only",
                   "no full-K3 quality or token-throughput claim", "minor faults alone do not define a paging cliff"],
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(canonical_json(output))
    print(canonical_json({"status": "pass", "summary": str(args.summary), "sha256": sha256_file(args.summary)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

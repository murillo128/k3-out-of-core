#!/usr/bin/env python3
"""Capture or verify Phase 11 Checkpoint D physical qualification evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODELS = {
    "f16": {"size": 784318432, "sha256": "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        "slot": 786432, "working_set": 44040192},
    "mxfp4": {"size": 751976576, "sha256": "0379a1cc623e09eb3fbd1dfcb18737bc8c971dbfe5bf5bc3e08da8b5379ec169",
        "slot": 208896, "working_set": 11698176},
}
IDENTITY = ("prompt_ids", "tokens", "logits_hash", "route_hash", "route_records")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""): digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, success: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if success and completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}\n{completed.stderr}")
    return completed


def fields(output: str, prefix: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.startswith(prefix + "\t")]
    if len(lines) != 1: raise ValueError(f"expected one {prefix} record")
    result: dict[str, Any] = {}
    for item in lines[0].split("\t")[1:]:
        key, value = item.split("=", 1)
        try: result[key] = int(value)
        except ValueError:
            try: result[key] = float(value)
            except ValueError: result[key] = value
    return result


def snapshot() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(("MemAvailable:", "SwapFree:")):
            key, value = line.split(":", 1); values[key] = int(value.split()[0])*1024
    for line in Path("/proc/vmstat").read_text().splitlines():
        key, value = line.split()
        if key in ("pswpin", "pswpout"): values[key] = int(value)
    cgroup = Path("/sys/fs/cgroup")
    for name in ("memory.current", "memory.swap.current"):
        values[name] = int((cgroup/name).read_text().strip())
    for line in (cgroup/"memory.pressure").read_text().splitlines():
        if line.startswith("full "):
            values["psi_full_total_usec"] = int(next(v.split("=")[1] for v in line.split() if v.startswith("total=")))
    query = subprocess.run(["nvidia-smi", "--query-compute-apps=used_memory", "--format=csv,noheader,nounits"],
        text=True, capture_output=True, check=False)
    values["cuda_process_used_mib"] = sum(int(v.strip()) for v in query.stdout.splitlines() if v.strip().isdigit())
    return values


def execute(binary: Path, model: Path, mode: str, steps: int, pool: int = 0,
        *, ignore_eog: bool = False) -> dict[str, Any]:
    command = [str(binary), "--model", str(model), "--mode", mode, "--steps", str(steps)]
    if ignore_eog: command.append("--ignore-eog")
    prefix = "PHASE5_LIVE"
    if mode == "uma":
        command += ["--capacity", "2", "--cold-bytes", str(pool), "--ring-bytes", "0"]
        prefix = "PHASE11_UMA_LIVE"
    elif mode == "cold":
        command += ["--capacity", "2", "--cold-bytes", str(pool), "--ring-bytes", str(4*1024*1024)]
    before = snapshot(); completed = run(command); after = snapshot()
    return {"command": command, "diagnostics": fields(completed.stdout, prefix), "before": before, "after": after,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr.encode()).hexdigest()}


def cached_execute(cache: Path, key: str, binary: Path, model: Path, mode: str, steps: int,
        pool: int = 0, *, ignore_eog: bool = False) -> dict[str, Any]:
    path = cache/f"{key}.json"
    if path.exists(): return json.loads(path.read_text())
    value = execute(binary, model, mode, steps, pool, ignore_eog=ignore_eog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))
    return value


def nearest(values: list[int], numerator: int, denominator: int = 100) -> int:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(len(ordered)*numerator/denominator) - 1))]


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    per_process = [[int(value) for value in str(record["diagnostics"]["token_samples_us"]).split(",")]
        for record in records]
    samples = [value for values in per_process for value in values]
    warm = [value for values in per_process for value in values[1:]]
    return {"processes": len(records), "samples": len(samples), "warm_samples": len(warm),
        "p50_us": nearest(warm, 50), "p95_us": nearest(warm, 95), "p99_us": nearest(warm, 99),
        "max_us": max(warm), "throughput_median": sorted(r["diagnostics"]["decode_tokens_per_second"] for r in records)[len(records)//2]}


def validate_uma(name: str, record: dict[str, Any], identity: dict[str, Any]) -> None:
    d = record["diagnostics"]
    if any(d[key] != identity[key] for key in IDENTITY): raise ValueError(f"{name}: output identity drift")
    zeros = ("provider_tensor_copies", "provider_failures", "ring_h2d_bytes", "ring_actual_bytes",
        "ring_lanes", "scheduler_active", "uma_process_swap_bytes", "uma_pressure_rejections",
        "uma_pressure_circuit_open", "uma_degraded_hits", "uma_unknown_residency_hits",
        "hot_policy_drops", "cold_policy_drops")
    if any(d[key] != 0 for key in zeros) or d["uma_psi_full_supported"] != 1:
        raise ValueError(f"{name}: safety/resource gate failed")
    if record["after"]["memory.swap.current"] != record["before"]["memory.swap.current"] or \
            record["after"]["psi_full_total_usec"] != record["before"]["psi_full_total_usec"]:
        raise ValueError(f"{name}: external swap/PSI-full growth")


def validate(document: dict[str, Any]) -> None:
    required = {"schema_version", "status", "scope", "revisions", "models", "tiny_matrix", "epoch_runs",
        "lifecycle", "full_k3", "negative", "comparisons", "statistics", "disposition_inputs", "commands"}
    if set(document) != required or document["schema_version"] != "phase11-checkpoint-d-v1" or \
            document["status"] != "pass": raise ValueError("unsupported Checkpoint D evidence")
    if document["revisions"]["gitlink"] != document["revisions"]["nested_head"]: raise ValueError("gitlink mismatch")
    for name, matrix in document["tiny_matrix"].items():
        identity = matrix["baseline"]["diagnostics"]
        for cell, records in matrix["uma"].items():
            if len(records) != 5: raise ValueError(f"{name}/{cell}: five processes required")
            for record in records: validate_uma(name, record, identity)
            if document["statistics"][name][cell]["warm_samples"] < 100: raise ValueError("tail sample gate failed")
        copy = matrix["copy_w"]
        if copy["status"] != "unsupported" or "hot-cache target must be one CUDA device" not in copy["reason"]:
            raise ValueError(f"{name}: same-host copy capability classification invalid")
        w = matrix["uma"]["w"]
        if any(r["diagnostics"]["cold_evictions"] != 0 or r["diagnostics"]["cold_hits"] <= 0 for r in w):
            raise ValueError(f"{name}: fitting warm set reread/eviction")
        best = max(document["statistics"][name][cell]["throughput_median"] for cell in ("w_minus_slot", "w", "w_plus_slot", "one_point_five_w"))
        if document["statistics"][name]["autofit"]["throughput_median"] < .95*best:
            raise ValueError(f"{name}: autofit throughput gate failed")
    for record in document["epoch_runs"].values():
        validate_uma("epoch", record, record["diagnostics"])
        if len(str(record["diagnostics"]["token_samples_us"]).split(",")) != 100: raise ValueError("100 epochs required")
    lifecycle = document["lifecycle"]
    if lifecycle["cycles"] != 25 or lifecycle["max_return_delta_bytes"] > lifecycle["return_threshold_bytes"]:
        raise ValueError("25-cycle resource-return gate failed")
    for records in document["full_k3"]["cells"].values():
        if len(records) != 5 or any(r["major_fault_delta"] != 0 or r["swap_growth_bytes"] != 0 or
                not r["fully_resident"] for r in records): raise ValueError("full-K3 residency cell failed")
    if document["negative"]["returncode"] == 0 or document["negative"]["io_started"]:
        raise ValueError("above-safe negative failed")
    if document["disposition_inputs"]["recommended"] != "SUPPORTED_EXPLICIT_NONDEFAULT":
        raise ValueError("unsupported disposition")


def full_k3_cell(probe: Path, cache: Path, name: str, budget: int, run_index: int) -> dict[str, Any]:
    record = cache/f"full-k3-{name}-{run_index}-record.json"
    if record.exists(): return json.loads(record.read_text())
    output = cache/f"full-k3-{name}-{run_index}-raw.json"
    command = [str(probe), "--output", str(output), "--budget-bytes", str(budget),
        "--projection-bytes", "5849088", "--layers", "92", "--experts-per-layer", "896",
        "--experts-used", "16", "--touch-slots", "1472", "--classification-samples", "100", "--policy", "LRU"]
    before = snapshot(); completed = run(command); after = snapshot(); value = json.loads(output.read_text())
    result = {"command": command, "elapsed_us": value["elapsed_us"],
        "major_fault_delta": value["faults_after"]["major_faults"] - value["faults_before"]["major_faults"],
        "swap_growth_bytes": max(0, (value["smaps_after_kib"].get("Swap", 0) - value["smaps_before_kib"].get("Swap", 0))*1024),
        "fully_resident": value["residency"]["ready_pages"] == value["residency"]["resident_ready_pages"],
        "capacity": value["capacity"], "policy_digest": value["policy_digest"], "before": before, "after": after,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "artifact_sha256": sha256(output)}
    record.write_bytes(canonical(result))
    return result


def capture(args: argparse.Namespace) -> dict[str, Any]:
    cache = args.cache_dir.resolve()/args.nested_head
    cache.mkdir(parents=True, exist_ok=True)
    models = {"f16": args.f16.resolve(), "mxfp4": args.mxfp4.resolve()}
    identities = {}
    for name, path in models.items():
        if path.stat().st_size != MODELS[name]["size"] or sha256(path) != MODELS[name]["sha256"]:
            raise ValueError(f"{name}: model identity mismatch")
        identities[name] = {"path": str(path), "size": path.stat().st_size, "sha256": sha256(path)}
    gitlink = run(["git", "ls-tree", args.project_head, "--", "llama.cpp"]).stdout.split()[2]
    matrix = {}; statistics = {}
    cells = ("half_w", "w_minus_slot", "w", "autofit", "w_plus_slot", "one_point_five_w")
    for name, model in models.items():
        spec = MODELS[name]
        baseline = cached_execute(cache, f"{name}-baseline", args.binary.resolve(), model, "disabled", 21)
        pools = {"half_w": spec["working_set"]//2, "w_minus_slot": spec["working_set"] - spec["slot"],
            "w": spec["working_set"], "w_plus_slot": spec["working_set"] + spec["slot"],
            "one_point_five_w": spec["working_set"]*3//2, "autofit": 0}
        uma = {cell: [] for cell in cells}
        copy_command = [str(args.binary.resolve()), "--model", str(model), "--mode", "cold", "--steps", "21",
            "--capacity", "2", "--cold-bytes", str(spec["working_set"]), "--ring-bytes", str(4*1024*1024)]
        copy_attempt = run(copy_command, success=False)
        copy = {"status": "unsupported", "command": copy_command, "returncode": copy_attempt.returncode,
            "reason": "hot-cache target must be one CUDA device" if "hot-cache target must be one CUDA device" in copy_attempt.stderr else copy_attempt.stderr[-500:],
            "stdout_sha256": hashlib.sha256(copy_attempt.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(copy_attempt.stderr.encode()).hexdigest()}
        if copy_attempt.returncode == 0: raise ValueError("same-host copy path unexpectedly became supported")
        for index in range(5):
            for cell in cells:
                uma[cell].append(cached_execute(cache, f"{name}-{cell}-{index}", args.binary.resolve(),
                    model, "uma", 21, pools[cell]))
        matrix[name] = {"baseline": baseline, "uma": uma, "copy_w": copy, "pools": pools}
        statistics[name] = {cell: summary(uma[cell]) for cell in cells}
    epochs = {name: cached_execute(cache, f"{name}-epochs", args.binary.resolve(), model, "uma", 100,
        MODELS[name]["working_set"], ignore_eog=True)
        for name, model in models.items()}
    cycle_before = snapshot(); cycle_records = []
    for index in range(25):
        cycle_records.append(cached_execute(cache, f"lifecycle-{index}", args.binary.resolve(), models["mxfp4"],
            "uma", 2, MODELS["mxfp4"]["working_set"]))
    cycle_after = snapshot(); threshold = max(256*1024*1024, int(.01*125371940864))
    return_delta = max(abs(cycle_after["memory.current"] - cycle_before["memory.current"]),
        abs(cycle_after["cuda_process_used_mib"] - cycle_before["cuda_process_used_mib"])*1024*1024)
    full_cells = {}
    for cell, budget in {"half_w": 12914786304, "w": 25829572608, "one_point_five_w": 38744358912}.items():
        full_cells[cell] = [full_k3_cell(args.residency_probe.resolve(), cache, cell, budget, index) for index in range(5)]
    safe = min(r["diagnostics"]["uma_safe_pool_bytes"] for r in matrix["mxfp4"]["uma"]["w"])
    negative_command = [str(args.binary.resolve()), "--model", str(models["mxfp4"]), "--mode", "uma", "--steps", "1",
        "--capacity", "2", "--cold-bytes", str(safe + MODELS["mxfp4"]["slot"]), "--ring-bytes", "0"]
    neg_before = snapshot(); negative = run(negative_command, success=False); neg_after = snapshot()
    negative_record = {"command": negative_command, "returncode": negative.returncode,
        "io_started": "PHASE11_UMA_LIVE" in negative.stdout, "before": neg_before, "after": neg_after,
        "stdout_sha256": hashlib.sha256(negative.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(negative.stderr.encode()).hexdigest()}
    document = {"schema_version": "phase11-checkpoint-d-v1", "status": "pass",
        "scope": "msi_edgexpert_gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head, "gitlink": gitlink},
        "models": identities, "tiny_matrix": matrix, "epoch_runs": epochs,
        "lifecycle": {"cycles": 25, "before": cycle_before, "after": cycle_after, "records": cycle_records,
            "max_return_delta_bytes": return_delta, "return_threshold_bytes": threshold},
        "full_k3": {"descriptor_revision": "9f62e4e9fffbd0a83ddd60e1c209d828994b3569",
            "bundle_bytes": 17547264, "working_set_bytes": 25829572608,
            "scope_limit": "exact-layout materialized residency and pressure only; not full-checkpoint quality or throughput", "cells": full_cells},
        "negative": negative_record,
        "comparisons": {"spark_copy_path": "unsupported_on_integrated_GB10_target_validation",
            "discrete_cuda": "historical_phase7_to_phase10_context_only_no_direct_ranking",
            "qwen": "unavailable_exact_package_not_present",
            "waste": {"revision": "c4d45c5914d1d15643d201855128938e8fb1698a", "license": "Apache-2.0",
                "hardware": "Apple M5 Pro 64 GiB UMA", "representation": "custom 3-bit full K3",
                "bytes_per_token_gib": 17, "throughput_tokens_per_second": [0.49, 0.54],
                "limits": "normalized context only; different model representation, kernels, hardware, storage and quality; no ranking"}},
        "statistics": statistics,
        "disposition_inputs": {"recommended": "SUPPORTED_EXPLICIT_NONDEFAULT",
            "storage_claim": "buffered threaded pread only; no native io_uring or storage-overlap claim",
            "performance_claim": "no minimum speedup; safe single-request single-device envelope only"},
        "commands": ["five fresh interleaved processes per tiny capacity/model", "100 fixed deterministic-route epochs per model",
            "25 complete fresh-process load/run/unload cycles", "five fresh exact-layout full-K3 residency processes per capacity"]}
    (cache/"candidate.json").write_bytes(canonical(document))
    validate(document)
    return document


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path); parser.add_argument("--residency-probe", type=Path)
    parser.add_argument("--f16", type=Path); parser.add_argument("--mxfp4", type=Path)
    parser.add_argument("--project-head"); parser.add_argument("--nested-head"); parser.add_argument("--output", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path("/tmp/phase11-checkpoint-d-cache"))
    parser.add_argument("--verify", type=Path); args = parser.parse_args()
    if args.verify:
        document = json.loads(args.verify.read_text()); validate(document)
        print(f"{args.verify} {hashlib.sha256(canonical(document)).hexdigest()}"); return 0
    if not all((args.binary, args.residency_probe, args.f16, args.mxfp4, args.project_head, args.nested_head, args.output)):
        parser.error("capture arguments are incomplete")
    document = capture(args); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_bytes(canonical(document))
    print(f"{args.output} {hashlib.sha256(canonical(document)).hexdigest()}"); return 0


if __name__ == "__main__": raise SystemExit(main())

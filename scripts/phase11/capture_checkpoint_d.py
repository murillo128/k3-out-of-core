#!/usr/bin/env python3
"""Capture or verify Phase 11 Checkpoint D physical qualification evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import random
import subprocess
import tarfile
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
PROCESS_COUNT = 15
LONGITUDINAL_PROCESS_COUNT = 101
AUTOFIT_ESTIMATOR = "ratio_of_process_throughput_medians"
DESCRIPTIVE_PROJECT_HEAD = "d25b4b7eed7139f3fafa4cdf717fa8c97e6fcb37"
DESCRIPTIVE_NESTED_HEAD = "89c430735b0b8986ddb8b202b09e60e1f3340a71"
STRUCTURAL_FIELDS = IDENTITY + (
    "cold_actual_bytes", "cold_unused_bytes", "cold_bundle_payload", "cold_slot_footprint", "cold_alignment",
    "cold_slots", "cold_hits", "cold_misses", "cold_evictions", "cold_source_bytes", "cold_failed_copies",
    "cold_failed_cleanups", "cold_reclaimed_bytes", "cold_reclaim_failures", "cold_policy", "cold_policy_scope",
    "cold_policy_admission", "cold_policy_digest", "cold_policy_drops", "hot_hits", "hot_misses",
    "hot_admissions", "hot_evictions", "hot_policy", "hot_policy_scope", "hot_policy_admission",
    "hot_policy_digest", "hot_policy_drops", "provider_pool_bytes", "provider_effective_capacity",
    "provider_tensor_copies", "provider_failures", "uma_effective_pool_bytes", "uma_model_capacity_bytes",
    "uma_effective_slot_count", "uma_storage_misses", "uma_resident_hot_hits",
    "uma_prepared_cold_hits", "uma_degraded_hits", "uma_unknown_residency_hits", "storage_read_requests",
    "storage_read_chunks", "storage_read_bytes", "storage_short_reads", "storage_io_errors",
    "storage_integrity_mismatches", "io_requests", "io_operations", "io_bytes", "io_buffered_fallback_operations",
    "io_buffered_fallback_bytes", "io_active_requests", "io_active_operations", "io_trace_dropped",
    "scheduler_active", "cold_hot_refs", "cold_transfer_refs", "cold_request_refs", "ring_actual_bytes",
    "ring_lanes", "ring_h2d_bytes", "ring_live_events", "ring_live_h2d_events", "ring_live_compute_events",
    "source_pinned_bytes",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024), b""): digest.update(block)
    return digest.hexdigest()


def expected_descriptive_files() -> list[str]:
    cells = ("half_w", "w_minus_slot", "w", "autofit", "w_plus_slot", "one_point_five_w")
    names: list[str] = []
    for model in MODELS:
        names.append(f"{model}-baseline.json")
        names.extend(f"{model}-{cell}-{index}.json" for cell in cells for index in range(PROCESS_COUNT))
        names.extend(f"{model}-longitudinal-{cell}-{index}.json"
            for cell in ("w", "autofit") for index in range(LONGITUDINAL_PROCESS_COUNT))
    return sorted(names)


def publish_descriptive_archive(source: Path, archive: Path, index_path: Path) -> dict[str, Any]:
    names = expected_descriptive_files()
    actual = sorted(path.name for path in source.glob("*.json"))
    if actual != names: raise ValueError("v4 descriptive raw set is incomplete or contains unexpected files")
    entries = [{"path": name, "size": (source/name).stat().st_size, "sha256": sha256(source/name)} for name in names]
    index = {"schema_version": "phase11-v4-raw-index-v1", "status": "complete",
        "source_revisions": {"project_head": DESCRIPTIVE_PROJECT_HEAD, "nested_head": DESCRIPTIVE_NESTED_HEAD},
        "files": entries}
    index_bytes = canonical(index)
    archive.parent.mkdir(parents=True, exist_ok=True); index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(index_bytes)
    with archive.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for name, data in [("phase11-v4-raw/index.json", index_bytes)] + [
                        (f"phase11-v4-raw/{entry['path']}", (source/entry["path"]).read_bytes()) for entry in entries]:
                    info = tarfile.TarInfo(name); info.size = len(data); info.mtime = 0
                    info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
                    tar.addfile(info, io.BytesIO(data))
    archive_label = str(archive.relative_to(ROOT)) if archive.is_relative_to(ROOT) else str(archive)
    index_label = str(index_path.relative_to(ROOT)) if index_path.is_relative_to(ROOT) else str(index_path)
    return {"archive_path": archive_label, "archive_size": archive.stat().st_size,
        "archive_sha256": sha256(archive), "index_path": index_label,
        "index_size": index_path.stat().st_size, "index_sha256": sha256(index_path), "index": index}


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
    warm_two = [values[2] for values in per_process]
    warm_ten = [values[10] for values in per_process]
    return {"processes": len(records), "samples": len(samples), "warm_samples": len(warm),
        "p50_us": nearest(warm, 50), "p95_us": nearest(warm, 95), "p99_us": nearest(warm, 99),
        "max_us": max(warm), "warm_run_2_p99_us": nearest(warm_two, 99),
        "warm_run_10_p99_us": nearest(warm_ten, 99),
        "throughput_median": sorted(r["diagnostics"]["decode_tokens_per_second"] for r in records)[len(records)//2]}


def longitudinal_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    run_two = [trace(record, "token_samples_us")[2] for record in records]
    run_ten = [trace(record, "token_samples_us")[10] for record in records]
    return {"processes": len(records), "warm_run_2_p99_us": nearest(run_two, 99),
        "warm_run_10_p99_us": nearest(run_ten, 99), "raw_run_2_us": run_two, "raw_run_10_us": run_ten}


def paired_interval(autofit: list[dict[str, Any]], explicit: list[dict[str, Any]], cell: str) -> dict[str, Any]:
    ratios = [auto["diagnostics"]["decode_tokens_per_second"]/reference["diagnostics"]["decode_tokens_per_second"]
        for auto, reference in zip(autofit, explicit, strict=True)]
    estimate = sorted(auto["diagnostics"]["decode_tokens_per_second"] for auto in autofit)[len(autofit)//2]/\
        sorted(reference["diagnostics"]["decode_tokens_per_second"] for reference in explicit)[len(explicit)//2]
    generator = random.Random(2608)
    bootstrap = sorted(sorted(generator.choices(ratios, k=len(ratios)))[len(ratios)//2] for _ in range(10000))
    return {"explicit_cell": cell, "estimator": AUTOFIT_ESTIMATOR, "ratios": ratios, "estimate": estimate,
        "paired_bootstrap_seed": 2608, "paired_bootstrap_replicates": 10000,
        "paired_bootstrap_95_interval": [bootstrap[249], bootstrap[9749]]}


def trace(record: dict[str, Any], key: str) -> list[int]:
    return [int(value) for value in str(record["diagnostics"][key]).split(",")]


def delta_series(record: dict[str, Any], key: str) -> list[int]:
    values = trace(record, key)
    return [values[0]] + [values[index] - values[index - 1] for index in range(1, len(values))]


def structural_signature(record: dict[str, Any]) -> dict[str, Any]:
    diagnostics = record["diagnostics"]
    missing = [key for key in STRUCTURAL_FIELDS if key not in diagnostics]
    if missing: raise ValueError("structural evidence missing: " + ",".join(missing))
    return {key: diagnostics[key] for key in STRUCTURAL_FIELDS}


def epoch_analysis(record: dict[str, Any], slot: int) -> dict[str, Any]:
    tail = trace(record, "token_samples_us")[20:]
    result = {"final_80_epochs": len(tail), "first_40_p99_us": nearest(tail[:40], 99),
        "last_40_p99_us": nearest(tail[40:], 99), "slot_bytes": slot, "series": {}}
    for key in ("epoch_cold_actual_bytes", "epoch_process_rss_bytes", "epoch_process_swap_bytes",
            "epoch_major_faults", "epoch_degraded_hits", "epoch_unknown_hits", "epoch_storage_read_bytes",
            "epoch_policy_drops", "epoch_scheduler_active", "epoch_cold_hot_refs", "epoch_cold_transfer_refs",
            "epoch_cold_request_refs", "epoch_ring_live_events", "epoch_io_active_operations"):
        values = trace(record, key)[20:]
        result["series"][key] = {"first": values[0], "last": values[-1], "minimum": min(values),
            "maximum": max(values), "growth": values[-1] - values[0]}
    return result


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
        "structural_equivalence", "raw_archive", "lifecycle", "full_k3", "negative", "comparisons",
        "statistics", "disposition_inputs", "commands"}
    if set(document) != required or document["schema_version"] != "phase11-checkpoint-d-v1" or \
            document["status"] != "pass": raise ValueError("unsupported Checkpoint D evidence")
    if document["revisions"]["gitlink"] != document["revisions"]["nested_head"]: raise ValueError("gitlink mismatch")
    if document["revisions"]["descriptive_project_head"] != DESCRIPTIVE_PROJECT_HEAD or \
            document["revisions"]["descriptive_nested_head"] != DESCRIPTIVE_NESTED_HEAD:
        raise ValueError("descriptive revision mismatch")
    archive = document["raw_archive"]
    archive_path = ROOT/archive["archive_path"]; index_path = ROOT/archive["index_path"]
    if not archive_path.is_file() or archive_path.stat().st_size != archive["archive_size"] or \
            sha256(archive_path) != archive["archive_sha256"] or not index_path.is_file() or \
            index_path.stat().st_size != archive["index_size"] or sha256(index_path) != archive["index_sha256"] or \
            json.loads(index_path.read_text()) != archive["index"] or \
            len(archive["index"]["files"]) != len(expected_descriptive_files()):
        raise ValueError("descriptive raw archive identity failed")
    for name, matrix in document["tiny_matrix"].items():
        identity = matrix["baseline"]["diagnostics"]
        for cell, records in matrix["uma"].items():
            if len(records) != PROCESS_COUNT: raise ValueError(f"{name}/{cell}: process count invalid")
            for record in records: validate_uma(name, record, identity)
            if document["statistics"][name][cell]["warm_samples"] < 100: raise ValueError("tail sample gate failed")
        copy = matrix["copy_w"]
        if copy["status"] != "unsupported" or "hot-cache target must be one CUDA device" not in copy["reason"]:
            raise ValueError(f"{name}: same-host copy capability classification invalid")
        w = matrix["uma"]["w"]
        if any(r["diagnostics"]["cold_evictions"] != 0 or r["diagnostics"]["cold_hits"] <= 0 for r in w):
            raise ValueError(f"{name}: fitting warm set reread/eviction")
        longitudinal_identity = matrix["longitudinal"]["w"][0]["diagnostics"]
        for cell, records in matrix["longitudinal"].items():
            if len(records) != LONGITUDINAL_PROCESS_COUNT: raise ValueError(f"{name}/{cell}: longitudinal process count invalid")
            for record in records:
                validate_uma(name, record, longitudinal_identity)
                hits = delta_series(record, "epoch_cold_hits")
                misses = delta_series(record, "epoch_cold_misses")
                storage = delta_series(record, "epoch_storage_read_bytes")
                if (hits[2], misses[2], storage[2]) != (hits[10], misses[10], storage[10]):
                    raise ValueError(f"{name}: warm run hit mix drift")
        for cell in ("w", "autofit"):
            stats = document["statistics"][name]["longitudinal"][cell]
            if stats["warm_run_10_p99_us"] > 1.10*stats["warm_run_2_p99_us"]:
                raise ValueError(f"{name}/{cell}: longitudinal warm tail gate failed")
        candidates = ("w_minus_slot", "w", "w_plus_slot", "one_point_five_w")
        best_cell = max(candidates, key=lambda cell: document["statistics"][name][cell]["throughput_median"])
        paired = document["statistics"][name]["autofit_vs_best_explicit"]
        if paired["explicit_cell"] != best_cell or paired["estimator"] != AUTOFIT_ESTIMATOR or \
                len(paired["ratios"]) != PROCESS_COUNT or paired["paired_bootstrap_seed"] != 2608 or \
                paired["paired_bootstrap_replicates"] != 10000 or len(paired["paired_bootstrap_95_interval"]) != 2:
            raise ValueError(f"{name}: paired throughput analysis invalid")
    for name, cells in document["structural_equivalence"].items():
        if set(cells) != {"explicit_w", "autofit", "signature", "equivalent"} or not cells["equivalent"] or \
                len(cells["explicit_w"]) != 5 or len(cells["autofit"]) != 5:
            raise ValueError(f"{name}: structural equivalence evidence incomplete")
        signatures = [structural_signature(record) for record in cells["explicit_w"] + cells["autofit"]]
        if any(signature != cells["signature"] for signature in signatures):
            raise ValueError(f"{name}: structural equivalence drift")
        identity = document["tiny_matrix"][name]["baseline"]["diagnostics"]
        for record in cells["explicit_w"] + cells["autofit"]: validate_uma(name, record, identity)
        for explicit, autofit in zip(cells["explicit_w"], cells["autofit"], strict=True):
            ed = explicit["diagnostics"]; ad = autofit["diagnostics"]
            if ed["uma_autofit"] != 0 or ad["uma_autofit"] != 1 or \
                    ed["uma_effective_pool_bytes"] != ad["uma_effective_pool_bytes"] or \
                    ed["cold_actual_bytes"] != ad["cold_actual_bytes"] or \
                    ed["uma_effective_slot_count"] != ad["uma_effective_slot_count"] or \
                    ad["uma_model_cap_unused_safe_bytes"] != ad["uma_safe_pool_bytes"] - ad["uma_effective_pool_bytes"]:
                raise ValueError(f"{name}: model-capped autofit telemetry mismatch")
    for name, record in document["epoch_runs"].items():
        validate_uma("epoch", record, record["diagnostics"])
        if len(str(record["diagnostics"]["token_samples_us"]).split(",")) != 100: raise ValueError("100 epochs required")
        analysis = document["statistics"][name]["epoch_longitudinal"]
        if analysis != epoch_analysis(record, MODELS[name]["slot"]): raise ValueError("epoch analysis mismatch")
        if analysis["last_40_p99_us"] > 1.10*analysis["first_40_p99_us"]:
            raise ValueError("100-epoch tail drift")
        for key, values in analysis["series"].items():
            if len(trace(record, key)) != 100: raise ValueError("100-epoch resource series required")
            if key == "epoch_cold_actual_bytes" and values["growth"] > MODELS[name]["slot"]:
                raise ValueError("100-epoch resource growth")
            if key in ("epoch_process_swap_bytes", "epoch_major_faults", "epoch_degraded_hits",
                    "epoch_unknown_hits", "epoch_policy_drops") and values["growth"] != 0:
                raise ValueError("100-epoch counter drift")
            if key in ("epoch_scheduler_active", "epoch_cold_transfer_refs", "epoch_cold_request_refs",
                    "epoch_ring_live_events", "epoch_io_active_operations") and values["maximum"] != 0:
                raise ValueError("100-epoch live resource not drained")
    lifecycle = document["lifecycle"]
    if lifecycle["cycles"] != 25 or lifecycle["max_return_delta_bytes"] > lifecycle["return_threshold_bytes"]:
        raise ValueError("25-cycle resource-return gate failed")
    for records in document["full_k3"]["cells"].values():
        if len(records) != 5 or any(r["major_fault_delta"] != 0 or r["swap_growth_bytes"] != 0 or
                not r["fully_resident"] for r in records): raise ValueError("full-K3 residency cell failed")
        for record in records:
            raw_bytes = record["artifact_text"].encode()
            if len(raw_bytes) != record["artifact_size"] or hashlib.sha256(raw_bytes).hexdigest() != record["artifact_sha256"]:
                raise ValueError("full-K3 embedded artifact identity failed")
    full = document["full_k3"]
    if set(full["cells"]) != {"half_w", "w", "one_point_five_w", "safe_explicit", "autofit"} or \
            full["autofit_bytes"] + full["slot_footprint_bytes"] != full["first_unsafe_bytes"] or \
            any(record["capacity"]["requested_bytes"] != full["autofit_bytes"] for record in full["cells"]["safe_explicit"]) or \
            full["negative"]["returncode"] == 0 or full["negative"]["raw"]["io_started"] or \
            full["negative"]["raw"]["reason"] != "requested_above_safe_limit" or \
            hashlib.sha256(full["negative"]["artifact_text"].encode()).hexdigest() != full["negative"]["artifact_sha256"]:
        raise ValueError("full-K3 autofit/unsafe proximity gate failed")
    if document["negative"]["returncode"] == 0 or document["negative"]["io_started"]:
        raise ValueError("above-safe negative failed")
    if document["disposition_inputs"]["recommended"] != "SUPPORTED_EXPLICIT_NONDEFAULT" or \
            document["disposition_inputs"]["autofit_claim"] != "SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED":
        raise ValueError("unsupported disposition")


def full_k3_cell(probe: Path, cache: Path, name: str, budget: int, run_index: int,
        safe_limit: int = 0) -> dict[str, Any]:
    record = cache/f"full-k3-{name}-{run_index}-record.json"
    if record.exists(): return json.loads(record.read_text())
    output = cache/f"full-k3-{name}-{run_index}-raw.json"
    command = [str(probe), "--output", str(output), "--budget-bytes", str(budget),
        "--projection-bytes", "5849088", "--layers", "92", "--experts-per-layer", "896",
        "--experts-used", "16", "--touch-slots", "1472", "--classification-samples", "100", "--policy", "LRU"]
    if safe_limit: command += ["--safe-limit-bytes", str(safe_limit)]
    before = snapshot(); completed = run(command); after = snapshot(); artifact_text = output.read_text(); value = json.loads(artifact_text)
    result = {"command": command, "elapsed_us": value["elapsed_us"],
        "major_fault_delta": value["faults_after"]["major_faults"] - value["faults_before"]["major_faults"],
        "swap_growth_bytes": max(0, (value["smaps_after_kib"].get("Swap", 0) - value["smaps_before_kib"].get("Swap", 0))*1024),
        "fully_resident": value["residency"]["ready_pages"] == value["residency"]["resident_ready_pages"],
        "capacity": value["capacity"], "policy_digest": value["policy_digest"], "before": before, "after": after,
        "stdout_sha256": hashlib.sha256(completed.stdout.encode()).hexdigest(),
        "artifact_size": output.stat().st_size, "artifact_sha256": sha256(output),
        "artifact_text": artifact_text, "raw": value}
    record.write_bytes(canonical(result))
    return result


def capture(args: argparse.Namespace) -> dict[str, Any]:
    cache = args.cache_dir.resolve()/args.nested_head
    cache.mkdir(parents=True, exist_ok=True)
    args.descriptive_cache = args.descriptive_cache.resolve()
    raw_archive = publish_descriptive_archive(
        args.descriptive_cache, args.raw_archive.resolve(), args.raw_index.resolve())
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
        baseline = json.loads((args.descriptive_cache/f"{name}-baseline.json").read_text())
        pools = {"half_w": spec["working_set"]//2, "w_minus_slot": spec["working_set"] - spec["slot"],
            "w": spec["working_set"], "w_plus_slot": spec["working_set"] + spec["slot"],
            "one_point_five_w": spec["working_set"]*3//2, "autofit": 0}
        uma = {cell: [json.loads((args.descriptive_cache/f"{name}-{cell}-{index}.json").read_text())
            for index in range(PROCESS_COUNT)] for cell in cells}
        copy_command = [str(args.binary.resolve()), "--model", str(model), "--mode", "cold", "--steps", "21",
            "--capacity", "2", "--cold-bytes", str(spec["working_set"]), "--ring-bytes", str(4*1024*1024)]
        copy_attempt = run(copy_command, success=False)
        copy = {"status": "unsupported", "command": copy_command, "returncode": copy_attempt.returncode,
            "reason": "hot-cache target must be one CUDA device" if "hot-cache target must be one CUDA device" in copy_attempt.stderr else copy_attempt.stderr[-500:],
            "stdout_sha256": hashlib.sha256(copy_attempt.stdout.encode()).hexdigest(),
            "stderr_sha256": hashlib.sha256(copy_attempt.stderr.encode()).hexdigest()}
        if copy_attempt.returncode == 0: raise ValueError("same-host copy path unexpectedly became supported")
        matrix[name] = {"baseline": baseline, "uma": uma, "copy_w": copy, "pools": pools}
        statistics[name] = {cell: summary(uma[cell]) for cell in cells}
        candidates = ("w_minus_slot", "w", "w_plus_slot", "one_point_five_w")
        best_cell = max(candidates, key=lambda cell: statistics[name][cell]["throughput_median"])
        statistics[name]["autofit_vs_best_explicit"] = paired_interval(uma["autofit"], uma[best_cell], best_cell)
        longitudinal = {cell: [json.loads((args.descriptive_cache/
            f"{name}-longitudinal-{cell}-{index}.json").read_text()) for index in range(LONGITUDINAL_PROCESS_COUNT)]
            for cell in ("w", "autofit")}
        matrix[name]["longitudinal"] = longitudinal
        statistics[name]["longitudinal"] = {cell: longitudinal_summary(records) for cell, records in longitudinal.items()}
        for cell, records in longitudinal.items():
            for record in records:
                hits = delta_series(record, "epoch_cold_hits"); misses = delta_series(record, "epoch_cold_misses")
                storage = delta_series(record, "epoch_storage_read_bytes")
                if (hits[2], misses[2], storage[2]) != (hits[10], misses[10], storage[10]):
                    raise ValueError(f"{name}/{cell}: longitudinal hit mix drift before full-K3 capture")
            stats = statistics[name]["longitudinal"][cell]
            if stats["warm_run_10_p99_us"] > 1.10*stats["warm_run_2_p99_us"]:
                raise ValueError(f"{name}/{cell}: longitudinal warm tail failed before full-K3 capture")
    structural = {}
    for name, model in models.items():
        pairs = {"explicit_w": [], "autofit": []}
        for index in range(5):
            order = ("explicit_w", "autofit") if index % 2 == 0 else ("autofit", "explicit_w")
            for cell in order:
                pool = MODELS[name]["working_set"] if cell == "explicit_w" else 0
                pairs[cell].append(cached_execute(cache, f"{name}-structural-{cell}-{index}",
                    args.binary.resolve(), model, "uma", 21, pool))
        signatures = [structural_signature(record) for record in pairs["explicit_w"] + pairs["autofit"]]
        structural[name] = {**pairs, "signature": signatures[0],
            "equivalent": all(signature == signatures[0] for signature in signatures)}
    epochs = {name: cached_execute(cache, f"{name}-epochs", args.binary.resolve(), model, "uma", 100,
        MODELS[name]["working_set"], ignore_eog=True)
        for name, model in models.items()}
    for name, record in epochs.items(): statistics[name]["epoch_longitudinal"] = epoch_analysis(record, MODELS[name]["slot"])
    cycle_before = snapshot(); cycle_records = []
    for index in range(25):
        cycle_records.append(execute(args.binary.resolve(), models["mxfp4"], "uma", 2, MODELS["mxfp4"]["working_set"]))
    cycle_after = snapshot(); threshold = max(256*1024*1024, int(.01*125371940864))
    return_delta = max(abs(cycle_after["memory.current"] - cycle_before["memory.current"]),
        abs(cycle_after["cuda_process_used_mib"] - cycle_before["cuda_process_used_mib"])*1024*1024)
    full_safe_source = execute(args.binary.resolve(), models["mxfp4"], "uma", 2, 0)
    full_safe_cap = full_safe_source["diagnostics"]["uma_safe_pool_bytes"]
    slot_footprint = 17547264
    full_autofit = full_safe_cap//slot_footprint*slot_footprint
    first_unsafe = full_autofit + slot_footprint
    full_cells = {}
    for cell, budget in {"half_w": 12914786304, "w": 25829572608, "one_point_five_w": 38744358912}.items():
        full_cells[cell] = [full_k3_cell(args.residency_probe.resolve(), cache, cell, budget, index) for index in range(5)]
    full_cells["safe_explicit"] = [full_k3_cell(args.residency_probe.resolve(), cache, "safe_explicit",
        full_autofit, index, full_autofit) for index in range(5)]
    full_cells["autofit"] = [full_k3_cell(args.residency_probe.resolve(), cache, "autofit", full_autofit,
        index, full_autofit) for index in range(5)]
    full_negative_output = cache/"full-k3-first-unsafe-raw.json"
    full_negative_command = [str(args.residency_probe.resolve()), "--output", str(full_negative_output),
        "--budget-bytes", str(first_unsafe), "--safe-limit-bytes", str(full_autofit),
        "--projection-bytes", "5849088", "--layers", "92", "--experts-per-layer", "896",
        "--experts-used", "16", "--touch-slots", "1472", "--classification-samples", "100", "--policy", "LRU"]
    full_negative_completed = run(full_negative_command, success=False)
    full_negative_text = full_negative_output.read_text(); full_negative_raw = json.loads(full_negative_text)
    full_negative = {"command": full_negative_command, "returncode": full_negative_completed.returncode,
        "artifact_size": full_negative_output.stat().st_size, "artifact_sha256": sha256(full_negative_output),
        "artifact_text": full_negative_text,
        "stdout_sha256": hashlib.sha256(full_negative_completed.stdout.encode()).hexdigest(), "raw": full_negative_raw}
    safe = full_safe_source["diagnostics"]["uma_safe_pool_bytes"]
    negative_command = [str(args.binary.resolve()), "--model", str(models["mxfp4"]), "--mode", "uma", "--steps", "1",
        "--capacity", "2", "--cold-bytes", str(safe + MODELS["mxfp4"]["slot"]), "--ring-bytes", "0"]
    neg_before = snapshot(); negative = run(negative_command, success=False); neg_after = snapshot()
    negative_record = {"command": negative_command, "returncode": negative.returncode,
        "io_started": "PHASE11_UMA_LIVE" in negative.stdout, "before": neg_before, "after": neg_after,
        "stdout_sha256": hashlib.sha256(negative.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(negative.stderr.encode()).hexdigest()}
    document = {"schema_version": "phase11-checkpoint-d-v1", "status": "pass",
        "scope": "msi_edgexpert_gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": args.project_head, "nested_head": args.nested_head, "gitlink": gitlink,
            "descriptive_project_head": DESCRIPTIVE_PROJECT_HEAD, "descriptive_nested_head": DESCRIPTIVE_NESTED_HEAD},
        "models": identities, "tiny_matrix": matrix, "structural_equivalence": structural,
        "raw_archive": raw_archive, "epoch_runs": epochs,
        "lifecycle": {"cycles": 25, "before": cycle_before, "after": cycle_after, "records": cycle_records,
            "max_return_delta_bytes": return_delta, "return_threshold_bytes": threshold},
        "full_k3": {"descriptor_revision": "9f62e4e9fffbd0a83ddd60e1c209d828994b3569",
            "bundle_bytes": 17547264, "working_set_bytes": 25829572608,
            "slot_footprint_bytes": slot_footprint, "safe_cap_source": full_safe_source,
            "autofit_bytes": full_autofit, "first_unsafe_bytes": first_unsafe, "negative": full_negative,
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
            "autofit_claim": "SAFE_CAPACITY_ONLY_NOT_PERFORMANCE_SELECTED",
            "explicit_budget_guidance": "use an evidence-backed explicit budget for performance-critical deployments",
            "storage_claim": "buffered threaded pread only; no native io_uring or storage-overlap claim",
            "performance_claim": "no minimum speedup; safe single-request single-device envelope only"},
        "commands": ["reuse and publish frozen v4 descriptive matrix without relabeling its source target",
            "five fresh exact-target W/autofit structural-equivalence pairs per model",
            "101 frozen v4 alternating W/autofit processes per model for nearest-rank warm-run-2/run-10 p99",
            "100 fixed deterministic-route epochs per model with per-epoch resource series",
            "25 uncached complete fresh-process load/run/unload cycles",
            "five fresh exact-layout full-K3 residency processes per explicit/autofit capacity plus first-unsafe rejection"]}
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
    parser.add_argument("--descriptive-cache", type=Path); parser.add_argument("--raw-archive", type=Path)
    parser.add_argument("--raw-index", type=Path)
    parser.add_argument("--verify", type=Path); args = parser.parse_args()
    if args.verify:
        document = json.loads(args.verify.read_text()); validate(document)
        print(f"{args.verify} {hashlib.sha256(canonical(document)).hexdigest()}"); return 0
    if not all((args.binary, args.residency_probe, args.f16, args.mxfp4, args.project_head, args.nested_head,
            args.output, args.descriptive_cache, args.raw_archive, args.raw_index)):
        parser.error("capture arguments are incomplete")
    document = capture(args); args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_bytes(canonical(document))
    print(f"{args.output} {hashlib.sha256(canonical(document)).hexdigest()}"); return 0


if __name__ == "__main__": raise SystemExit(main())

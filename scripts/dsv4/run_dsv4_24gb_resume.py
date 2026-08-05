#!/usr/bin/env python3
"""Run one bounded DeepSeek-V4 provider validation cell with resource sampling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import time
from pathlib import Path
from typing import Any


MODEL = Path("/workspace/models/DeepSeek-V4-Flash-85ce4196-UD-Q3_K_XL/DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf")
PROBE = Path("/workspace/builds/k3-issue49-cuda/bin/phase9-cache-policy-probe")
PROMPT = "<｜begin▁of▁sentence｜><｜User｜>Explain why a careful measurement should distinguish observed facts from assumptions.<｜Assistant｜><think>"
PROMPT_BYTES = 150
PROMPT_SHA256 = "956f20dbb9de59aba70bd6a510ad3c8ab46df35046000d925f0ae874d433a8b8"
EXPECTED_IDS = [2581, 1309, 304, 8470, 3939, 16372, 11226, 1531]
GIB = 1024**3
MIN_MEM_AVAILABLE = 16 * GIB
OS_RESERVE = 32 * GIB
MIN_DISK_AVAILABLE = 55 * GIB
MIN_GPU_FREE_MIB = 6 * 1024
MAX_PINNED_BYTES = GIB
HOT_SLOT_BYTES = 15_993_600
COLD_SLOT_BYTES = 51_179_520
LANE_BYTES = 16_793_280
ANCHOR_HOT_SLOTS = 268
ANCHOR_MIN_GPU_FREE_MIB = 12_466


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256(path)}


def meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        result[key] = int(value.strip().split()[0]) * 1024
    return result


def smaps_rollup(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        lines = Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines()
    except OSError:
        return result
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields = value.strip().split()
        if fields and fields[0].isdigit():
            result[key] = int(fields[0]) * 1024
    return result


def cgroup_memory_events() -> tuple[str, dict[str, int]]:
    cgroup = Path("/sys/fs/cgroup")
    try:
        for line in Path("/proc/self/cgroup").read_text().splitlines():
            if line.startswith("0::"):
                cgroup /= line.split("::", 1)[1].lstrip("/")
                break
        events = {}
        for line in (cgroup / "memory.events").read_text().splitlines():
            key, value = line.split()
            events[key] = int(value)
        return str(cgroup), events
    except OSError:
        return str(cgroup), {}


def gpu_sample() -> dict[str, int | float]:
    completed = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total,utilization.gpu,utilization.memory,power.draw",
         "--format=csv,noheader,nounits"], text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    fields = [item.strip() for item in completed.stdout.splitlines()[0].split(",")]
    try:
        return {
            "used_mib": int(fields[0]), "free_mib": int(fields[1]), "total_mib": int(fields[2]),
            "gpu_utilization_percent": int(fields[3]), "memory_utilization_percent": int(fields[4]),
            "power_watts": float(fields[5]),
        }
    except (IndexError, ValueError):
        return {}


def disk_available(path: Path) -> int:
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires values")
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999))]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def preflight(args: argparse.Namespace) -> dict[str, Any]:
    memory = meminfo()
    disk = disk_available(args.output_dir)
    gpu = gpu_sample()
    reasons = []
    if args.queue_depth not in (0,) and not 8 <= args.queue_depth <= 4096:
        reasons.append("requested queue depth violates the validated [8,4096] transport invariant")
    if args.trace_capacity not in (0,) and not 1024 <= args.trace_capacity <= 65536:
        reasons.append("requested trace capacity violates the validated [1024,65536] evidence bound")
    cold_actual = (args.cold_bytes // COLD_SLOT_BYTES) * COLD_SLOT_BYTES
    cold_slots = cold_actual // COLD_SLOT_BYTES
    hot_actual = args.hot_slots * HOT_SLOT_BYTES
    ring_actual = (args.ring_bytes // LANE_BYTES) * LANE_BYTES
    ring_lanes = ring_actual // LANE_BYTES
    if args.hot_slots <= 0 or args.cold_bytes <= 0:
        reasons.append("hot and cold capacities must be positive")
    if cold_slots < args.hot_slots:
        reasons.append("cold-cache effective slots cannot be fewer than hot-cache slots")
    if ring_lanes not in (2, 3, 4):
        reasons.append("transfer ring must resolve to 2-4 bounded lanes")
    if memory["MemAvailable"] < cold_actual + OS_RESERVE:
        reasons.append("live MemAvailable cannot preserve the declared 32 GiB OS reserve after cold allocation")
    if disk < MIN_DISK_AVAILABLE:
        reasons.append("filesystem availability is below the fixed 55 GiB reserve")
    if gpu and gpu["free_mib"] * 1024**2 < hot_actual + MIN_GPU_FREE_MIB * 1024**2:
        reasons.append("live VRAM cannot preserve the fixed 6 GiB reserve after the requested hot allocation")
    estimated_min_gpu_free_mib = ANCHOR_MIN_GPU_FREE_MIB + (
        (ANCHOR_HOT_SLOTS - args.hot_slots) * HOT_SLOT_BYTES // 1024**2)
    if estimated_min_gpu_free_mib < MIN_GPU_FREE_MIB:
        reasons.append("anchor-derived VRAM estimate cannot preserve the fixed 6 GiB reserve")
    return {
        "status": "reject" if reasons else "pass", "reasons": reasons,
        "requested": {
            "hot_slots": args.hot_slots, "hot_actual_bytes": hot_actual,
            "cold_bytes": args.cold_bytes, "cold_actual_bytes": cold_actual, "cold_slots": cold_slots,
            "ring_bytes": args.ring_bytes, "ring_actual_bytes": ring_actual, "ring_lanes": ring_lanes,
            "queue_depth": args.queue_depth, "trace_capacity": args.trace_capacity, "transport": args.transport,
        },
        "observed": {
            "mem_available_bytes": memory["MemAvailable"], "disk_available_bytes": disk, "gpu": gpu,
            "anchor_hot_slots": ANCHOR_HOT_SLOTS, "anchor_minimum_gpu_free_mib": ANCHOR_MIN_GPU_FREE_MIB,
            "estimated_minimum_gpu_free_mib": estimated_min_gpu_free_mib,
        },
        "bounds": {
            "os_reserve_bytes": OS_RESERVE, "minimum_mem_available_bytes": MIN_MEM_AVAILABLE,
            "minimum_disk_available_bytes": MIN_DISK_AVAILABLE, "minimum_gpu_free_mib": MIN_GPU_FREE_MIB,
            "maximum_pinned_bytes": MAX_PINNED_BYTES,
        },
    }


def summarize_probe(document: dict[str, Any]) -> dict[str, Any]:
    latencies = document["latency_us"]
    decode = latencies[1:]
    async_io = document["async_io"]["diagnostics"]
    intervals = document["async_io"]["read_intervals"]
    queue_wait = [item["started_us"] - item["queued_us"] for item in intervals]
    service = [item["complete_us"] - item["submit_us"] for item in intervals]
    if async_io["positional_reads_forced"] and not async_io["io_uring_enabled"]:
        actual_transport = "POSITIONAL"
    elif async_io["io_uring_enabled"] and async_io["direct_read_operations"] > 0:
        actual_transport = "DIRECT_IO"
    elif async_io["io_uring_enabled"]:
        actual_transport = "BUFFERED"
    elif async_io["synchronous_fallback_operations"] > 0:
        actual_transport = "POSITIONAL_FALLBACK"
    else:
        actual_transport = "UNRESOLVED"
    return {
        "prompt": {
            "bytes": len(document["prompt"].encode()),
            "sha256": hashlib.sha256(document["prompt"].encode()).hexdigest(),
        },
        "generated_ids": document["generated_ids"], "generated_text": document["generated_text"],
        "generated_text_sha256": hashlib.sha256(document["generated_text"].encode()).hexdigest(),
        "logits_fnv64": document["logits_fnv64"],
        "cpu_time_us": {
            "user": document["cpu_user_time_us"], "system": document["cpu_system_time_us"],
            "total": document["cpu_user_time_us"] + document["cpu_system_time_us"],
        },
        "latency_us": latencies,
        "decode": {
            "samples": len(decode), "throughput_tps": len(decode) * 1_000_000 / sum(decode),
            "p50_us": percentile(decode, 0.50), "p95_us": percentile(decode, 0.95),
            "p99_us": percentile(decode, 0.99), "maximum_us": max(decode),
        },
        "io": {
            "queue_wait_samples": len(queue_wait),
            "queue_wait_p50_us": percentile(queue_wait, 0.50),
            "queue_wait_p95_us": percentile(queue_wait, 0.95),
            "queue_wait_p99_us": percentile(queue_wait, 0.99),
            "queue_wait_max_us": max(queue_wait),
            "service_samples": len(service), "service_p50_us": percentile(service, 0.50),
            "service_p95_us": percentile(service, 0.95), "service_p99_us": percentile(service, 0.99),
            "service_max_us": max(service), "diagnostics": async_io,
        },
        "transport_requested": document["transport_requested"], "transport_actual": actual_transport,
        "capacities": document["capacities"], "mechanism": document["mechanism"],
        "transfer": document["transfer"], "storage": document["storage"],
        "lifecycle": document["lifecycle"], "peak_rss_kib_reported": document["peak_rss_kib"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--transport", choices=("POSITIONAL", "BUFFERED", "DIRECT_IO"), default="BUFFERED")
    parser.add_argument("--hot-slots", type=int, required=True)
    parser.add_argument("--cold-bytes", type=int, required=True)
    parser.add_argument("--ring-bytes", type=int, required=True)
    parser.add_argument("--queue-depth", type=int, default=0)
    parser.add_argument("--trace-capacity", type=int, default=0)
    parser.add_argument("--max-generate", type=int, default=8)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    record_path = args.output_dir / f"{args.name}.record.json"
    check = preflight(args)
    if check["status"] != "pass":
        record = {"schema_version": "dsv4-24gb-resume-cell-v1", "name": args.name,
                  "status": "preflight_rejected", "preflight": check}
        write_json(record_path, record)
        print(json.dumps(record, sort_keys=True))
        return 0

    raw_path = args.output_dir / f"{args.name}.probe.json"
    stdout_path = args.output_dir / f"{args.name}.stdout.txt"
    stderr_path = args.output_dir / f"{args.name}.stderr.txt"
    command = [
        str(PROBE), "--model", str(MODEL), "--output", str(raw_path), "--mode", "cold",
        "--prompt", PROMPT, "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL",
        "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU", "--hot-slots", str(args.hot_slots),
        "--cold-bytes", str(args.cold_bytes), "--ring-bytes", str(args.ring_bytes),
        "--queue-depth", str(args.queue_depth), "--trace-capacity", str(args.trace_capacity),
        "--n-ctx", "4096", "--n-batch", "128", "--n-ubatch", "128",
        "--max-generate", str(args.max_generate), "--background", "0", "--observe-routes", "1",
        "--transport", args.transport, "--config-source", "NULL",
    ]
    cgroup_path, cgroup_before = cgroup_memory_events()
    memory_before = meminfo()
    disk_before = disk_available(args.output_dir)
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    samples: list[dict[str, Any]] = []
    breached: list[str] = []
    started_ns = time.monotonic_ns()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        while process.poll() is None:
            memory = meminfo()
            gpu = gpu_sample()
            sample = {
                "elapsed_ms": (time.monotonic_ns() - started_ns) // 1_000_000,
                "mem_available_bytes": memory["MemAvailable"], "swap_free_bytes": memory["SwapFree"],
                "disk_available_bytes": disk_available(args.output_dir), "gpu": gpu,
                "smaps": smaps_rollup(process.pid),
            }
            samples.append(sample)
            if memory["MemAvailable"] < MIN_MEM_AVAILABLE:
                breached.append("MemAvailable below 16 GiB")
            if sample["disk_available_bytes"] < MIN_DISK_AVAILABLE:
                breached.append("filesystem availability below 55 GiB")
            if gpu and gpu["free_mib"] < MIN_GPU_FREE_MIB:
                breached.append("GPU free memory below 6 GiB")
            if breached:
                process.terminate()
                break
            time.sleep(args.sample_interval)
        exit_code = process.wait()
    ended_ns = time.monotonic_ns()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cgroup_after_path, cgroup_after = cgroup_memory_events()
    memory_after = meminfo()
    disk_after = disk_available(args.output_dir)

    probe_summary = None
    raw_identity = None
    if exit_code == 0 and raw_path.is_file():
        document = json.loads(raw_path.read_text())
        probe_summary = summarize_probe(document)
        compressed = raw_path.with_suffix(raw_path.suffix + ".zst")
        subprocess.run(["zstd", "-T0", "-6", "--quiet", "--rm", str(raw_path), "-o", str(compressed)], check=True)
        raw_identity = identity(compressed)

    min_mem = min((sample["mem_available_bytes"] for sample in samples), default=memory_after["MemAvailable"])
    min_disk = min((sample["disk_available_bytes"] for sample in samples), default=disk_after)
    gpu_samples = [sample["gpu"] for sample in samples if sample["gpu"]]
    smaps_samples = [sample["smaps"] for sample in samples if sample["smaps"]]
    peak_pss_sample = max(smaps_samples, key=lambda item: item.get("Pss", 0), default={})
    event_delta = {key: cgroup_after.get(key, 0) - cgroup_before.get(key, 0)
                   for key in sorted(set(cgroup_before) | set(cgroup_after))}
    gates = {
        "exit_zero": exit_code == 0, "watchdog_not_breached": not breached,
        "prompt_exact": probe_summary is not None and probe_summary["prompt"] == {"bytes": PROMPT_BYTES, "sha256": PROMPT_SHA256},
        "generated_ids_exact": probe_summary is not None and
            probe_summary["generated_ids"][:len(EXPECTED_IDS)] == EXPECTED_IDS and
            len(probe_summary["generated_ids"]) == args.max_generate,
        "transport_exact": probe_summary is not None and probe_summary["transport_actual"] == args.transport,
        "mem_available": min_mem >= MIN_MEM_AVAILABLE,
        "swap_zero": all(sample["smaps"].get("Swap", 0) == 0 for sample in samples),
        "cgroup_pressure_zero": all(event_delta.get(key, 0) == 0 for key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill")),
        "disk_reserve": min_disk >= MIN_DISK_AVAILABLE,
        "vram_reserve": bool(gpu_samples) and min(item["free_mib"] for item in gpu_samples) >= MIN_GPU_FREE_MIB,
        "pinned_bound": probe_summary is not None and probe_summary["capacities"]["ring_pinned_or_registered_bytes"] <= MAX_PINNED_BYTES,
        "terminal": probe_summary is not None and all(probe_summary["lifecycle"][key] == 0 for key in (
            "current_hot_pins", "cold_current_transfer_refs", "cold_current_request_refs",
            "active_background_flights", "hot_failed_cleanups", "cold_failed_cleanups",
            "hot_transcript_dropped", "cold_transcript_dropped")),
        "storage_errors_zero": probe_summary is not None and all(probe_summary["storage"][key] == 0 for key in (
            "cancelled_reads", "short_reads", "io_errors")),
        "async_trace_complete": probe_summary is not None and probe_summary["io"]["diagnostics"]["trace_records_dropped"] == 0,
    }
    record = {
        "schema_version": "dsv4-24gb-resume-cell-v1", "name": args.name,
        "status": "pass" if all(gates.values()) else "fail", "command": command,
        "preflight": check, "exit_code": exit_code, "watchdog_breaches": sorted(set(breached)),
        "wall_time_us": (ended_ns - started_ns) // 1000,
        "resource_usage": {
            "cpu_user_time_us": int((usage_after.ru_utime - usage_before.ru_utime) * 1_000_000),
            "cpu_system_time_us": int((usage_after.ru_stime - usage_before.ru_stime) * 1_000_000),
            "peak_rss_kib": usage_after.ru_maxrss, "major_faults": usage_after.ru_majflt - usage_before.ru_majflt,
            "minor_faults": usage_after.ru_minflt - usage_before.ru_minflt,
            "input_blocks": usage_after.ru_inblock - usage_before.ru_inblock,
            "output_blocks": usage_after.ru_oublock - usage_before.ru_oublock,
            "mem_available_before_bytes": memory_before["MemAvailable"],
            "mem_available_after_bytes": memory_after["MemAvailable"], "minimum_mem_available_bytes": min_mem,
            "disk_available_before_bytes": disk_before, "disk_available_after_bytes": disk_after,
            "minimum_disk_available_bytes": min_disk,
            "minimum_gpu_free_mib": min((item["free_mib"] for item in gpu_samples), default=None),
            "peak_gpu_used_mib": max((item["used_mib"] for item in gpu_samples), default=None),
            "peak_pss_bytes": peak_pss_sample.get("Pss"), "peak_pss_anon_bytes": peak_pss_sample.get("Pss_Anon"),
            "peak_pss_file_bytes_at_peak_pss": peak_pss_sample.get("Pss_File"),
            "peak_process_swap_bytes": max((item.get("Swap", 0) for item in smaps_samples), default=0),
            "cgroup_path": cgroup_path if cgroup_path == cgroup_after_path else [cgroup_path, cgroup_after_path],
            "cgroup_memory_event_delta": event_delta, "sample_count": len(samples),
        },
        "probe": probe_summary, "raw_probe": raw_identity,
        "stdout": identity(stdout_path), "stderr": identity(stderr_path), "gates": gates,
    }
    write_json(record_path, record)
    print(json.dumps({"status": record["status"], "record": str(record_path), "gates": gates}, sort_keys=True))
    return 0 if record["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

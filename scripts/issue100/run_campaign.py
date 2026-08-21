#!/usr/bin/env python3
"""Run issue #100 serially with fail-closed scoring, durability, and resume."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import resource
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from protocol import (
    ANSWER_PATTERN, ATTEMPT_TIMEOUT_S, AUTO_ADMISSION_SHA256,
    AUTO_CACHE_REQUEST_BYTES, CAMPAIGN_SHA256, CAPACITY_FLOOR_BYTES,
    CAPACITY_FLOOR_SLOTS, CUMULATIVE_ATTEMPT_BUDGET_S, DEFAULT_BINARY,
    DEFAULT_EVIDENCE_ROOT, EXPERT_BUNDLE_BYTES, MAX_GENERATED, MAX_RESTARTS,
    MODEL_MANIFEST_SHA256, MODEL_PATH, N_CTX, NESTED_BASELINE,
    MEMLOCK_LIMIT_BYTES,
    PREVIOUS_BINARY_SHA256, PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
    PREVIOUS_ATTEMPT_TIMEOUT_S, PREVIOUS_NESTED_COMMIT, PREVIOUS_PROJECT_COMMIT,
    PREREGISTRATION_SHA256, PUBLIC_AUTO_ADMISSION, RESPONSE_BOUNDARY, THREADS,
    RECOVERY_ATTEMPT_FIRST, RECOVERY_ATTEMPT_LAST, RECOVERY_EPOCH,
    RECOVERY_RUN_ORDINAL,
    ProtocolError, append_canonical_jsonl, atomic_json, bind_checksum,
    file_identity, finite_number, load_json, require_frozen_runtime_identity,
    process_entry_failures, sha256_bytes, sha256_file,
    system_memory_diagnostic_failures, transport_diagnostic_failures,
    transport_teardown_failures, validate_checksum,
)


class CampaignError(RuntimeError):
    pass


DEVICES = ["nvme0n1", "nvme1n1", "nvme2n1"]
RECLAIM_PREFIXES = ("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_")
HARD_PROBE_MARKERS = (
    "non-finite", "invalid token", "identity mismatch", "resource/safety invariant",
    "routing coverage", "CPU production-path", "prefill did not prove",
    "cache did not fill", "tokenization", "prompt plus maximum",
    "AUTO resolved capacity below", "system-memory cold-cache budget",
    "provider error", "context initialization failed",
)
HARD_PROBE_STATUSES = ("provider-failure", "halted-below-capacity-floor")
LIBC = ctypes.CDLL(None, use_errno=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_host_helpers(repo_root: Path) -> Any:
    path = repo_root / "scripts/phase13_6/run_cpu_demand_pairs.py"
    spec = importlib.util.spec_from_file_location("issue100_host_helpers", path)
    if spec is None or spec.loader is None:
        raise CampaignError("unable to load frozen host telemetry helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scalar_value(path: Path | None) -> int | str | None:
    if path is None:
        return None
    try:
        value = path.read_text().strip()
        try:
            return int(value)
        except ValueError:
            return value
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return None


def pressure_totals() -> dict[str, int]:
    result = {}
    try:
        for line in Path("/proc/pressure/memory").read_text().splitlines():
            fields = line.split()
            total = next((item for item in fields[1:] if item.startswith("total=")), None)
            if total:
                result[fields[0]] = int(total.split("=", 1)[1])
    except (FileNotFoundError, PermissionError):
        pass
    return result


def require_frozen_memlock() -> None:
    soft, hard = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    if soft != MEMLOCK_LIMIT_BYTES or hard != MEMLOCK_LIMIT_BYTES:
        raise CampaignError(
            f"process memlock envelope must be exactly {MEMLOCK_LIMIT_BYTES} bytes soft/hard"
        )


def text_lines(path: Path) -> list[str]:
    try:
        return path.read_text().splitlines()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return []


def process_memlock_limits(pid: int) -> tuple[int | str | None, int | str | None]:
    for line in text_lines(Path("/proc") / str(pid) / "limits"):
        if not line.startswith("Max locked memory"):
            continue
        fields = line.split()
        if len(fields) < 6:
            break
        def parse(value: str) -> int | str:
            return int(value) if value.isdigit() else value
        return parse(fields[3]), parse(fields[4])
    return None, None


def process_cgroup_root(pid: int) -> tuple[str | None, Path | None]:
    for line in text_lines(Path("/proc") / str(pid) / "cgroup"):
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0":
            name = "/" + fields[2].lstrip("/")
            return name, Path("/sys/fs/cgroup") / name.lstrip("/")
    return None, None


def process_entry_snapshot(pid: int, host: Any) -> dict[str, Any]:
    soft, hard = process_memlock_limits(pid)
    cgroup_name, cgroup_root = process_cgroup_root(pid)
    meminfo = host.scalar_snapshot(Path("/proc/meminfo"))
    status_path = Path("/proc") / str(pid) / "status"
    status = host.scalar_snapshot(status_path) if status_path.exists() else {}
    events_path = cgroup_root / "memory.events" if cgroup_root else None
    events = host.scalar_snapshot(events_path) if events_path and events_path.exists() else {}
    vmstat = host.scalar_snapshot(Path("/proc/vmstat"))
    system_pressure_path = Path("/proc/pressure/memory")
    cgroup_pressure_path = cgroup_root / "memory.pressure" if cgroup_root else None
    system_pressure = text_lines(system_pressure_path)
    cgroup_pressure = text_lines(cgroup_pressure_path) if cgroup_pressure_path else []
    swap_total = int(meminfo.get("SwapTotal", 0))*1024
    swap_free = int(meminfo.get("SwapFree", 0))*1024
    return {
        "boot_id": scalar_value(Path("/proc/sys/kernel/random/boot_id")),
        "rlimit_memlock_soft_bytes": soft,
        "rlimit_memlock_hard_bytes": hard,
        "io_uring_disabled": scalar_value(Path("/proc/sys/kernel/io_uring_disabled")),
        "cgroup_path": cgroup_name,
        "cgroup_memory_current_bytes": scalar_value(
            cgroup_root / "memory.current" if cgroup_root else None
        ),
        "cgroup_memory_max_bytes": scalar_value(
            cgroup_root / "memory.max" if cgroup_root else None
        ),
        "cgroup_memory_events": events,
        "mem_available_bytes": int(meminfo.get("MemAvailable", 0))*1024,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_used_bytes": max(0, swap_total - swap_free),
        "process_swap_kib": status.get("VmSwap"),
        "vmstat_oom_kill": vmstat.get("oom_kill"),
        "system_memory_pressure_available": system_pressure_path.exists(),
        "system_memory_pressure": system_pressure,
        "cgroup_memory_pressure_available": bool(
            cgroup_pressure_path and cgroup_pressure_path.exists()
        ),
        "cgroup_memory_pressure": cgroup_pressure,
        "page_size_bytes": os.sysconf("SC_PAGE_SIZE"),
    }


def cgroup_paths(host: Any) -> tuple[Path | None, Path | None]:
    events = host.current_cgroup_memory_events()
    if events is None:
        return None, None
    return events, events.parent / "memory.current"


def counter_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, int]:
    return {
        key: int(after.get(key, 0)) - int(value)
        for key, value in before.items()
        if isinstance(value, int) and isinstance(after.get(key, 0), int)
    }


def fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def progress_token_count(path: Path) -> int:
    try:
        with path.open("rb") as source:
            return sum(1 for line in source if b'"record_type":"token"' in line)
    except FileNotFoundError:
        return 0


def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def configure_child_process() -> None:
    """Give the probe its own process group and terminate it with the runner."""
    os.setsid()
    if LIBC.prctl(1, signal.SIGTERM) != 0:  # PR_SET_PDEATHSIG
        os._exit(126)
    if os.getppid() == 1:
        os._exit(128 + signal.SIGTERM)


def process_start_ticks(pid: int) -> int | None:
    try:
        fields = (Path("/proc") / str(pid) / "stat").read_text().split()
        return int(fields[21])
    except (FileNotFoundError, IndexError, ValueError, PermissionError):
        return None


def seal_interrupted_attempt(directory: Path, identity: dict[str, Any]) -> None:
    """Preserve an unaccepted attempt and make it ineligible for resume reuse."""
    start_path = directory / "attempt-start.json"
    start = load_json(start_path) if start_path.exists() else {
        "started_at_epoch_s": directory.stat().st_mtime,
    }
    pid = start.get("pid")
    ticks = start.get("process_start_ticks")
    if isinstance(pid, int) and isinstance(ticks, int) and process_start_ticks(pid) == ticks:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 10
        while process_start_ticks(pid) == ticks and time.monotonic() < deadline:
            time.sleep(0.1)
        if process_start_ticks(pid) == ticks:
            os.killpg(pid, signal.SIGKILL)
    paths = [path for path in directory.iterdir() if path.is_file() and path.name != "attempt-manifest.json"]
    artifacts = {path.name: file_identity(path) for path in sorted(paths)}
    started = float(start.get("started_at_epoch_s", directory.stat().st_mtime))
    stopped = max((path.stat().st_mtime for path in paths), default=started)
    atomic_json(directory / "attempt-manifest.json", {
        "schema_version": "issue100-attempt-manifest-v1",
        **identity,
        "exit_status": None,
        "timed_out": False,
        "interrupted_before_acceptance": True,
        "elapsed_s_estimate": max(0.0, stopped - started),
        "external_pressure_failures": [],
        "hard_probe_failure": False,
        "accepted": False,
        "artifacts": artifacts,
    })


def run_with_envelope(
    command: list[str],
    directory: Path,
    identity: dict[str, Any],
    host: Any,
    timeout_s: float = ATTEMPT_TIMEOUT_S,
) -> tuple[int, bool, dict[str, Any]]:
    stdout_path = directory / "stdout.log"
    stderr_path = directory / "stderr.log"
    progress_path = directory / "progress.jsonl"
    before = host.host_snapshot(DEVICES)
    before_pressure = pressure_totals()
    events_path, memory_current_path = cgroup_paths(host)
    before_events = host.scalar_snapshot(events_path) if events_path else {}
    samples: dict[str, Any] = {
        "count": 0,
        "minimum_mem_available_kib": before["meminfo"].get("MemAvailable"),
        "peak_cgroup_memory_current_bytes": scalar_value(memory_current_path),
        "peak_process_rss_kib": 0,
        "peak_process_hwm_kib": 0,
        "peak_process_swap_kib": 0,
        "cpu_affinity_allowed_list": None,
    }
    started = time.monotonic()
    last_heartbeat = started
    timed_out = False
    atomic_json(directory / "attempt-start.json", {
        "schema_version": "issue100-attempt-start-v1",
        "attempt": identity,
        "started_at": utc_now(),
        "started_at_epoch_s": time.time(),
        "command": command,
        "pid": None,
        "process_start_ticks": None,
    })
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(
            command, stdout=stdout, stderr=stderr, preexec_fn=configure_child_process,
        )
        attempt_start = load_json(directory / "attempt-start.json")
        attempt_start.update({
            "pid": process.pid,
            "process_start_ticks": process_start_ticks(process.pid),
            "process_entry": process_entry_snapshot(process.pid, host),
        })
        atomic_json(directory / "attempt-start.json", attempt_start)
        samples["pid"] = process.pid
        while process.poll() is None:
            now = time.monotonic()
            if now - started >= timeout_s:
                timed_out = True
                terminate_process_group(process)
                break
            status_path = Path("/proc") / str(process.pid) / "status"
            status = host.scalar_snapshot(status_path) if status_path.exists() else {}
            meminfo = host.scalar_snapshot(Path("/proc/meminfo"))
            samples["count"] += 1
            available = meminfo.get("MemAvailable")
            if isinstance(available, int):
                prior = samples["minimum_mem_available_kib"]
                samples["minimum_mem_available_kib"] = (
                    available if not isinstance(prior, int) else min(prior, available)
                )
            current = scalar_value(memory_current_path)
            if isinstance(current, int):
                prior = samples["peak_cgroup_memory_current_bytes"]
                samples["peak_cgroup_memory_current_bytes"] = (
                    current if not isinstance(prior, int) else max(prior, current)
                )
            for source, target in (
                ("VmRSS", "peak_process_rss_kib"), ("VmHWM", "peak_process_hwm_kib"),
                ("VmSwap", "peak_process_swap_kib"),
            ):
                value = status.get(source)
                if isinstance(value, int):
                    samples[target] = max(samples[target], value)
            if status.get("Cpus_allowed_list") is not None:
                samples["cpu_affinity_allowed_list"] = status["Cpus_allowed_list"]
            if now - last_heartbeat >= 60:
                print(
                    f"ISSUE100_HEARTBEAT run={identity['run_ordinal']:03d}/228 "
                    f"arm={identity['arm']} item={identity['item_id']} "
                    f"elapsed_s={now-started:.0f} generated={progress_token_count(progress_path)}",
                    flush=True,
                )
                last_heartbeat = now
            time.sleep(0.5)
        returncode = process.returncode if process.returncode is not None else -signal.SIGKILL
        stdout.flush()
        stderr.flush()
        os.fsync(stdout.fileno())
        os.fsync(stderr.fileno())
    after = host.host_snapshot(DEVICES)
    after_pressure = pressure_totals()
    after_events = host.scalar_snapshot(events_path) if events_path else {}
    envelope = {
        "schema_version": "issue100-attempt-envelope-v1",
        "attempt": identity,
        "command": command,
        "exit_status": returncode,
        "timed_out": timed_out,
        "elapsed_s": time.monotonic() - started,
        "samples": samples,
        "before": before,
        "after": after,
        "delta": host.host_delta(before, after),
        "cgroup_memory_events_delta": counter_delta(before_events, after_events),
        "memory_pressure_total_delta_usec": {
            key: after_pressure.get(key, 0) - value for key, value in before_pressure.items()
        },
        "process_entry": attempt_start["process_entry"],
    }
    atomic_json(directory / "envelope.json", envelope)
    return returncode, timed_out, envelope


def pressure_failures(envelope: dict[str, Any]) -> list[str]:
    failures = []
    vmstat = envelope["delta"]["vmstat"]
    for key in ("pswpin", "pswpout", "oom_kill"):
        if vmstat.get(key, 0):
            failures.append(f"{key}={vmstat[key]}")
    reclaim = {
        key: value for key, value in vmstat.items()
        if key.startswith(RECLAIM_PREFIXES) and value != 0
    }
    if reclaim:
        failures.append(f"reclaim/refault={reclaim}")
    cgroup = {
        key: value for key, value in envelope["cgroup_memory_events_delta"].items()
        if key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill") and value
    }
    if cgroup:
        failures.append(f"cgroup={cgroup}")
    if envelope["samples"]["peak_process_swap_kib"]:
        failures.append("process swap")
    return failures


def parse_progress(path: Path, result: dict[str, Any]) -> None:
    rows = []
    with path.open("rb") as source:
        for line in source:
            rows.append(json.loads(line))
    if len(rows) < 3 or rows[0].get("record_type") != "metadata" or \
            rows[-1].get("record_type") != "terminal":
        raise CampaignError("probe progress stream is incomplete")
    token_rows = [row for row in rows if row.get("record_type") == "token"]
    generation = result["generation"]
    if [row["ordinal"] for row in token_rows] != list(range(1, len(token_rows) + 1)) or \
            [row["token_id"] for row in token_rows] != generation["token_ids"] or \
            [row["piece_hex"] for row in token_rows] != generation["piece_hex"]:
        raise CampaignError("progress token stream differs from final result")


def validate_probe_result(
    path: Path,
    progress_path: Path,
    run: dict[str, Any],
    item: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any]:
    result = load_json(path)
    failures = pressure_failures(envelope)
    if result.get("schema_version") != "issue100-gpqa-probe-result-v2" or \
            result.get("status") != "pass":
        failures.append("probe schema/status")
    observed_item = result.get("item", {})
    if observed_item.get("id") != run["item_id"] or not observed_item.get("scored") or \
            observed_item.get("prompt_sha256") != item["prompt_sha256"] or \
            observed_item.get("prompt_tokens") != item["prompt_tokens"] or \
            result.get("arm") != run["arm"] or result.get("seed") != item["generation_seed"]:
        failures.append("run/item/seed identity")
    execution = result.get("execution", {})
    if execution.get("backend") != "CPU" or execution.get("gpu_device_count") != 0 or \
            execution.get("n_gpu_layers") != 0 or execution.get("n_ctx") != N_CTX or \
            execution.get("n_batch") != 1 or execution.get("n_ubatch") != 1 or \
            execution.get("threads") != THREADS or execution.get("load_mode") != "DIRECT_IO" or \
            execution.get("runtime_mode") != "PERFORMANCE" or execution.get("issue_mode") != "BATCHED" or \
            not execution.get("native_io_uring") or \
            execution.get("buffer_registration_error") != 0 or \
            execution.get("async_fallback_reason_mask") != 0 or \
            execution.get("capacity_request_mode") != "AUTO" or \
            execution.get("capacity_request_bytes") != AUTO_CACHE_REQUEST_BYTES:
        failures.append("execution envelope")
    failures.extend(
        f"transport diagnostics: {reason}" for reason in
        transport_diagnostic_failures(execution)
    )
    entry = envelope.get("process_entry", {})
    failures.extend(
        f"process entry: {reason}" for reason in process_entry_failures(entry)
    )
    failures.extend(
        f"transport teardown: {reason}" for reason in
        transport_teardown_failures(execution, envelope, entry)
    )
    probe_entry = result.get("process_entry", {})
    if probe_entry.get("rlimit_memlock_soft_bytes") != entry.get("rlimit_memlock_soft_bytes") or \
            probe_entry.get("rlimit_memlock_hard_bytes") != entry.get("rlimit_memlock_hard_bytes"):
        failures.append("probe/launcher memlock identity")
    protocol = result.get("protocol", {})
    if protocol.get("prefill_routing") != "EXACT" or \
            protocol.get("s2_activation") != "after-complete-prefill-before-first-generated-token-decode" or \
            abs(float(protocol.get("top_p", 0.0)) - 0.95) > 1e-6 or \
            protocol.get("top_p_min_keep") != 1 or \
            protocol.get("temperature") != 1.0 or protocol.get("sampler_seed") != item["generation_seed"] or \
            protocol.get("max_generated") != MAX_GENERATED or protocol.get("stop") not in ("EOG", "TOKEN_CAP"):
        failures.append("generation protocol")
    preflight = result.get("preflight", {})
    initial_cold = preflight.get("initial_cold", {})
    initial_storage = preflight.get("initial_storage", {})
    initial_memory = preflight.get("system_memory", {})
    cache_capacity = result.get("cache", {})
    resolved_slots = cache_capacity.get("capacity_slots")
    resolved_bytes = cache_capacity.get("capacity_bytes")
    if not preflight.get("pass") or preflight.get("process_start_occupancy") != 0 or \
            not preflight.get("first_miss_backing_read") or \
            initial_storage.get("cancelled_reads") != 0 or \
            initial_storage.get("short_reads") != 0 or initial_storage.get("io_errors") != 0 or \
            not isinstance(resolved_slots, int) or resolved_slots < CAPACITY_FLOOR_SLOTS or \
            resolved_bytes != resolved_slots*EXPERT_BUNDLE_BYTES or \
            execution.get("auto_resolved_slots") != resolved_slots or \
            execution.get("auto_resolved_bytes") != resolved_bytes or \
            initial_cold.get("requested_bytes") != resolved_bytes or \
            initial_cold.get("actual_bytes") != resolved_bytes or \
            initial_cold.get("capacity") != resolved_slots or \
            initial_memory.get("requested_pool_bytes") != AUTO_CACHE_REQUEST_BYTES or \
            initial_memory.get("selected_pool_bytes") != resolved_bytes or \
            not initial_memory.get("autofit") or not initial_memory.get("budget_frozen"):
        failures.append("fresh production-AUTO cold-cache preflight")
    if result.get("io", {}).get("total", {}).get("io_errors") != 0:
        failures.append("storage I/O errors")
    generation = result.get("generation", {})
    token_ids = generation.get("token_ids", [])
    pieces = generation.get("piece_hex", [])
    if not isinstance(token_ids, list) or not token_ids or len(token_ids) > MAX_GENERATED or \
            len(token_ids) != len(pieces) or \
            generation.get("generated_tokens_including_eog") != len(token_ids) or \
            bool(generation.get("stopped_eog")) == bool(generation.get("truncated")):
        failures.append("generation output/stop")
    try:
        for value in pieces:
            bytes.fromhex(value)
        finite_number(generation.get("decode_inference_s"), "decode_inference_s")
        finite_number(generation.get("generation_wall_s"), "generation_wall_s")
        finite_number(generation.get("decode_tok_s"), "decode_tok_s")
    except (ValueError, ProtocolError) as error:
        failures.append(str(error))
    routing = result.get("routing", {})
    stats = routing.get("stats", {})
    forwards = generation.get("decode_forward_tokens")
    if run["arm"] == "EXACT":
        if routing.get("enabled") or any(stats.get(key, 0) for key in (
            "ubatches", "layers", "decisions", "changed_decisions", "swaps", "failures"
        )) or stats.get("cumulative_score_regret", 0.0) != 0.0:
            failures.append("EXACT routing activity")
    else:
        if not routing.get("enabled") or routing.get("candidate_count") != 32 or \
                routing.get("max_swaps") != 2 or \
                abs(float(routing.get("max_score_regret", 0.0)) - 0.007303759455680847) > 1e-12 or \
                stats.get("ubatches") != forwards or stats.get("layers") != forwards*92 or \
                stats.get("decisions") != forwards*92 or stats.get("failures") != 0:
            failures.append("S2 routing configuration/coverage")
    safety = result.get("safety", {})
    failures.extend(
        f"terminal transport diagnostics: {reason}" for reason in
        transport_diagnostic_failures(safety.get("transport", {}))
    )
    terminal_memory = safety.get("system_memory", {})
    if safety.get("status") != "pass" or safety.get("vm_swap_kib") != 0 or \
            any(safety.get("terminal_references", {}).values()) or \
            terminal_memory.get("requested_pool_bytes") != AUTO_CACHE_REQUEST_BYTES or \
            terminal_memory.get("selected_pool_bytes") != resolved_bytes or \
            not terminal_memory.get("autofit") or not terminal_memory.get("budget_frozen") or \
            terminal_memory.get("selected_pool_slots") != resolved_slots or \
            not terminal_memory.get("stage") or terminal_memory.get("pressure_rejection_reason") or \
            terminal_memory.get("pressure_rejections") != 0 or \
            terminal_memory.get("pressure_circuit_open"):
        failures.append("terminal safety/resources")
    failures.extend(
        f"system-memory diagnostics: {reason}" for reason in
        system_memory_diagnostic_failures(terminal_memory, resolved_slots, resolved_bytes)
    )
    if failures:
        raise CampaignError("probe validation failed: " + "; ".join(failures))
    parse_progress(progress_path, result)
    return result


def score_result(result: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    generation = result["generation"]
    all_pieces = [bytes.fromhex(value) for value in generation["piece_hex"]]
    raw_bytes = b"".join(all_pieces)
    content_pieces = all_pieces[:-1] if generation["stopped_eog"] else all_pieces
    content = b"".join(content_pieces)
    boundary_start = content.find(RESPONSE_BOUNDARY)
    boundary_end = boundary_start + len(RESPONSE_BOUNDARY) if boundary_start >= 0 else -1
    response = content[boundary_end:] if boundary_end >= 0 else b""
    match = re.search(ANSWER_PATTERN, response) if response else None
    extracted = match.group(1).decode("ascii").upper() if match else None
    truncated = bool(generation["truncated"])
    invalid = boundary_start < 0 or not response or extracted is None
    correct = (
        not truncated and not invalid and extracted == item["correct_answer_letter"]
    )
    outcome = "truncated" if truncated else "invalid" if invalid else "correct" if correct else "incorrect"

    reasoning_tokens = 0
    transition_tokens = 0
    response_tokens = 0
    if boundary_start >= 0:
        offset = 0
        for piece in content_pieces:
            piece_end = offset + len(piece)
            if piece_end <= boundary_start:
                reasoning_tokens += 1
            elif offset >= boundary_end:
                response_tokens += 1
            else:
                transition_tokens += 1
            offset = piece_end
    else:
        reasoning_tokens = len(content_pieces)
    return {
        "raw_bytes": raw_bytes,
        "content_bytes": content,
        "reasoning_bytes": content[:boundary_start] if boundary_start >= 0 else content,
        "transition_bytes": RESPONSE_BOUNDARY if boundary_start >= 0 else b"",
        "response_bytes": response,
        "raw_output_sha256": sha256_bytes(raw_bytes),
        "content_sha256": sha256_bytes(content),
        "response_sha256": sha256_bytes(response),
        "extracted_answer": extracted,
        "correct_answer": item["correct_answer_letter"],
        "correct": correct,
        "invalid": invalid,
        "malformed": False,
        "truncated": truncated,
        "outcome": outcome,
        "reasoning_tokens": reasoning_tokens,
        "transition_tokens": transition_tokens,
        "response_tokens": response_tokens,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("rb") as source:
        for ordinal, line in enumerate(source, 1):
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise CampaignError(f"invalid JSONL at {path}:{ordinal}: {error}") from error
    return rows


def structured_hard_probe_failure(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        return load_json(path).get("status") in HARD_PROBE_STATUSES
    except (OSError, ValueError, TypeError):
        return False


def jsonl_prefix_sha256(path: Path, count: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    with path.open("rb") as source:
        for line in source:
            if observed == count:
                break
            digest.update(line)
            observed += 1
    if observed != count:
        raise CampaignError("accepted-run prefix is incomplete")
    return digest.hexdigest()


def validate_existing_runs(
    path: Path,
    identity: dict[str, Any],
    authorization: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    accepted_prefix_runs = int(authorization.get("accepted_prefix_runs", 0))
    prefix_segments = authorization.get("accepted_prefix_identities", [])
    if accepted_prefix_runs:
        if accepted_prefix_runs > len(rows) or not isinstance(prefix_segments, list) or \
                jsonl_prefix_sha256(path, accepted_prefix_runs) != \
                authorization.get("accepted_prefix_sha256"):
            raise CampaignError("accepted-run recovery prefix drift")
        covered = []
        for segment in prefix_segments:
            if not isinstance(segment, dict) or not isinstance(segment.get("identity"), dict):
                raise CampaignError("accepted-run recovery identity segment is invalid")
            first = segment.get("first_run")
            last = segment.get("last_run")
            if not isinstance(first, int) or not isinstance(last, int) or \
                    first < 1 or last < first:
                raise CampaignError("accepted-run recovery identity range is invalid")
            covered.extend(range(first, last + 1))
        if covered != list(range(1, accepted_prefix_runs + 1)):
            raise CampaignError("accepted-run recovery identity coverage is invalid")
    seen = set()
    for expected_ordinal, row in enumerate(rows, 1):
        validate_checksum(row)
        if row.get("schema_version") != "issue100-accepted-run-v1" or \
                row.get("run_ordinal") != expected_ordinal:
            raise CampaignError("accepted run order/schema drift")
        key = (row.get("run_ordinal"), row.get("item_id"), row.get("arm"))
        if key in seen:
            raise CampaignError("duplicate accepted run")
        seen.add(key)
        if expected_ordinal <= accepted_prefix_runs:
            expected_identity = next(
                segment["identity"] for segment in prefix_segments
                if segment["first_run"] <= expected_ordinal <= segment["last_run"]
            )
        else:
            expected_identity = identity
        for field, value in expected_identity.items():
            if row.get(field) != value:
                raise CampaignError(f"accepted run identity drift: {field}")
        manifest = Path(row["attempt_manifest_path"])
        if not manifest.is_file() or sha256_file(manifest) != row["attempt_manifest_sha256"]:
            raise CampaignError("accepted attempt manifest drift")
    return rows


def pair_record(pair_ordinal: int, exact: dict[str, Any], s2: dict[str, Any]) -> dict[str, Any]:
    if exact["item_id"] != s2["item_id"] or exact["arm"] != "EXACT" or s2["arm"] != "S2_P50":
        raise CampaignError("pair identity mismatch")
    if exact.get("first_generated_token_id") != s2.get("first_generated_token_id"):
        raise CampaignError("paired first sampled token differs before S2 generated decode")
    exact_correct = bool(exact["correct"])
    s2_correct = bool(s2["correct"])
    pair_class = (
        "both-correct" if exact_correct and s2_correct else
        "EXACT-only" if exact_correct else
        "S2-only" if s2_correct else "both-wrong"
    )
    return bind_checksum({
        "schema_version": "issue100-pair-v1",
        "timestamp": utc_now(),
        "campaign_sha256": CAMPAIGN_SHA256,
        "pair_ordinal": pair_ordinal,
        "item_id": exact["item_id"],
        "exact_run_checksum": exact["artifact_checksum"],
        "s2_run_checksum": s2["artifact_checksum"],
        "first_generated_token_id": exact["first_generated_token_id"],
        "exact_correct": exact_correct,
        "s2_correct": s2_correct,
        "pair_class": pair_class,
        "accuracy_delta": int(s2_correct) - int(exact_correct),
        "exact_auto_slots": exact["auto_resolved_slots"],
        "s2_auto_slots": s2["auto_resolved_slots"],
        "auto_slot_delta": s2["auto_resolved_slots"] - exact["auto_resolved_slots"],
        "relative_auto_slot_delta": (
            (s2["auto_resolved_slots"] - exact["auto_resolved_slots"])/exact["auto_resolved_slots"]
        ),
        "exact_auto_bytes": exact["auto_resolved_bytes"],
        "s2_auto_bytes": s2["auto_resolved_bytes"],
    })


def reconcile_pairs(root: Path, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = root / "pairs.jsonl"
    pairs = load_jsonl(path)
    for ordinal, pair in enumerate(pairs, 1):
        validate_checksum(pair)
        if pair.get("schema_version") != "issue100-pair-v1" or pair.get("pair_ordinal") != ordinal:
            raise CampaignError("pair JSONL order/schema drift")
        expected = pair_record(ordinal, runs[(ordinal - 1)*2], runs[(ordinal - 1)*2 + 1])
        for key in (
            "campaign_sha256", "item_id", "exact_run_checksum", "s2_run_checksum",
            "first_generated_token_id", "exact_correct", "s2_correct", "pair_class", "accuracy_delta",
            "exact_auto_slots", "s2_auto_slots", "auto_slot_delta",
            "relative_auto_slot_delta", "exact_auto_bytes", "s2_auto_bytes",
        ):
            if pair.get(key) != expected.get(key):
                raise CampaignError(f"pair evidence drift: {key}")
    completed_pairs = min(30, len(runs)//2)
    while len(pairs) < completed_pairs:
        ordinal = len(pairs) + 1
        record = pair_record(ordinal, runs[(ordinal - 1)*2], runs[(ordinal - 1)*2 + 1])
        append_canonical_jsonl(path, record)
        pairs.append(record)
    if len(pairs) > completed_pairs:
        raise CampaignError("pair evidence exists without two accepted arms")
    return pairs


def percentile(values: list[float], probability: float) -> float:
    if not values:
        raise CampaignError("percentile requires values")
    ordered = sorted(values)
    rank = max(1, int((probability*len(ordered) + 0.999999999999)))
    return ordered[min(rank, len(ordered)) - 1]


def checkpoint(root: Path, runs: list[dict[str, Any]], pairs: list[dict[str, Any]], status: str) -> None:
    exact = [row for row in runs if row["arm"] == "EXACT"]
    s2 = [row for row in runs if row["arm"] == "S2_P50"]
    decode_tps = [float(row["decode_tok_s"]) for row in s2]
    generated = [int(row["generated_tokens"]) for row in s2]
    exact_slots = [int(row["auto_resolved_slots"]) for row in exact]
    s2_slots = [int(row["auto_resolved_slots"]) for row in s2]
    pair_slot_deltas = [int(row["auto_slot_delta"]) for row in pairs]
    value = {
        "schema_version": "issue100-cumulative-checkpoint-v1",
        "status": status,
        "updated_at": utc_now(),
        "campaign_sha256": CAMPAIGN_SHA256,
        "accepted_runs": len(runs),
        "completed_pairs": len(pairs),
        "exact_completed": len(exact),
        "exact_correct": sum(row["correct"] for row in exact),
        "s2_completed": len(s2),
        "s2_correct": sum(row["correct"] for row in s2),
        "s2_accuracy": sum(row["correct"] for row in s2)/len(s2) if s2 else None,
        "paired_accuracy_delta": (
            sum(row["accuracy_delta"] for row in pairs)/len(pairs) if pairs else None
        ),
        "pair_classes": {
            label: sum(row["pair_class"] == label for row in pairs)
            for label in ("both-correct", "both-wrong", "EXACT-only", "S2-only")
        },
        "s2_decode_tok_s_mean": statistics.fmean(decode_tps) if decode_tps else None,
        "s2_decode_tok_s_median": statistics.median(decode_tps) if decode_tps else None,
        "s2_generated_tokens_median": statistics.median(generated) if generated else None,
        "exact_auto_slots": {
            "count": len(exact_slots),
            "min": min(exact_slots) if exact_slots else None,
            "max": max(exact_slots) if exact_slots else None,
            "mean": statistics.fmean(exact_slots) if exact_slots else None,
        },
        "s2_auto_slots": {
            "count": len(s2_slots),
            "min": min(s2_slots) if s2_slots else None,
            "max": max(s2_slots) if s2_slots else None,
            "mean": statistics.fmean(s2_slots) if s2_slots else None,
        },
        "paired_auto_slot_delta": {
            "count": len(pair_slot_deltas),
            "min": min(pair_slot_deltas) if pair_slot_deltas else None,
            "max": max(pair_slot_deltas) if pair_slot_deltas else None,
            "mean": statistics.fmean(pair_slot_deltas) if pair_slot_deltas else None,
        },
        "last_run_checksum": runs[-1]["artifact_checksum"] if runs else None,
        "last_pair_checksum": pairs[-1]["artifact_checksum"] if pairs else None,
    }
    atomic_json(root / "checkpoint.json", value)
    atomic_json(root / "pair-summary.json", {
        key: value[key] for key in (
            "schema_version", "updated_at", "campaign_sha256", "completed_pairs",
            "paired_accuracy_delta", "pair_classes", "exact_auto_slots", "s2_auto_slots",
            "paired_auto_slot_delta", "last_pair_checksum",
        )
    })


def cumulative_attempt_seconds(root: Path) -> float:
    total = 0.0
    for path in root.glob("attempts/*/attempt-*/attempt-manifest.json"):
        value = load_json(path)
        envelope = path.parent / "envelope.json"
        if envelope.exists():
            elapsed = load_json(envelope).get("elapsed_s")
        else:
            elapsed = value.get("elapsed_s_estimate")
        total += finite_number(elapsed, f"{path} elapsed_s")
    return total


def validate_recovery_attempt_lineage(root: Path, authorization: dict[str, Any]) -> list[str]:
    run_roots = list((root / "attempts").glob(f"run-{RECOVERY_RUN_ORDINAL:03d}-*"))
    if len(run_roots) != 1:
        raise CampaignError("recovery attempt lineage root is ambiguous")
    hashes = []
    for attempt_ordinal in range(1, RECOVERY_ATTEMPT_FIRST):
        path = run_roots[0] / f"attempt-{attempt_ordinal:02d}" / "attempt-manifest.json"
        if not path.is_file():
            raise CampaignError("recovery attempt lineage is incomplete")
        manifest = load_json(path)
        if manifest.get("run_ordinal") != RECOVERY_RUN_ORDINAL or \
                manifest.get("attempt_ordinal") != attempt_ordinal or manifest.get("accepted"):
            raise CampaignError("recovery attempt identity drift")
        hashes.append(sha256_file(path))
    expected_hashes = authorization.get("prior_attempt_manifest_sha256s")
    lineage_sha256 = sha256_bytes(("\n".join(hashes) + "\n").encode("ascii"))
    if hashes != expected_hashes or lineage_sha256 != authorization.get("attempt_lineage_sha256"):
        raise CampaignError("recovery attempt lineage drift")
    return hashes


def persist_campaign_control(path: Path, control: dict[str, Any]) -> None:
    epochs = control.get("recovery_epochs", [])
    if epochs:
        current = epochs[-1]
        current["status"] = control.get("status")
        current["updated_at"] = utc_now()
        for key in ("reason", "failed_run", "failed_attempt", "accepted_runs", "completed_pairs"):
            if key in control:
                current[key] = control[key]
            else:
                current.pop(key, None)
    atomic_json(path, control)


def validate_authorization(
    path: Path,
    *,
    preregistration: Path,
    protected_plan: Path,
    binary: Path,
) -> dict[str, Any]:
    value = load_json(path)
    validate_checksum(value)
    required = {
        "schema_version": "issue100-execution-authorization-v5",
        "verdict": "PASS",
        "safe_to_start_scored_inference": True,
        "serves_as_final_review": False,
        "nested_commit": NESTED_BASELINE,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "campaign_sha256": CAMPAIGN_SHA256,
        "protected_plan_sha256": sha256_file(protected_plan),
        "binary_sha256": sha256_file(binary),
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "auto_admission_sha256": AUTO_ADMISSION_SHA256,
        "capacity_request_mode": "AUTO",
        "capacity_request_bytes": AUTO_CACHE_REQUEST_BYTES,
        "capacity_floor_slots": CAPACITY_FLOOR_SLOTS,
        "capacity_floor_bytes": CAPACITY_FLOOR_BYTES,
        "memlock_limit_bytes": MEMLOCK_LIMIT_BYTES,
        "previous_attempt_timeout_s": PREVIOUS_ATTEMPT_TIMEOUT_S,
        "attempt_timeout_s": ATTEMPT_TIMEOUT_S,
        "campaign_cumulative_attempt_budget_s": CUMULATIVE_ATTEMPT_BUDGET_S,
        "max_restarts": MAX_RESTARTS,
        "non_scored_conformance": "PASS",
        "non_scored_conformance_project_commit": PREVIOUS_PROJECT_COMMIT,
        "non_scored_conformance_reused": True,
        "runtime_binary_unchanged": True,
        "reviewed_base_project_commit": PREVIOUS_PROJECT_COMMIT,
        "successful_path_delta": "ATTEMPT_WATCHDOG_AND_CONTROL_ONLY",
        "successful_path_equivalence": "PASS",
        "recovery_epoch": RECOVERY_EPOCH,
        "recovery_run_ordinal": RECOVERY_RUN_ORDINAL,
        "recovery_attempt_first": RECOVERY_ATTEMPT_FIRST,
        "recovery_attempt_last": RECOVERY_ATTEMPT_LAST,
        "accepted_prefix_runs": 2,
        "previous_execution_authorization_sha256": PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
    }
    for key, expected in required.items():
        if value.get(key) != expected:
            raise CampaignError(f"execution authorization mismatch: {key}")
    if sha256_file(preregistration) != PREREGISTRATION_SHA256:
        raise CampaignError("public preregistration identity mismatch")
    previous_identity = value.get("previous_identity", {})
    expected_previous_identity = {
        "campaign_sha256": CAMPAIGN_SHA256,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "project_commit": PREVIOUS_PROJECT_COMMIT,
        "nested_commit": PREVIOUS_NESTED_COMMIT,
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "adapter_binary_sha256": PREVIOUS_BINARY_SHA256,
        "protected_plan_sha256": sha256_file(protected_plan),
        "execution_authorization_sha256": PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
        "auto_admission_sha256": AUTO_ADMISSION_SHA256,
        "capacity_request_mode": "AUTO",
        "capacity_request_bytes": AUTO_CACHE_REQUEST_BYTES,
        "capacity_floor_slots": CAPACITY_FLOOR_SLOTS,
        "capacity_floor_bytes": CAPACITY_FLOOR_BYTES,
        "memlock_limit_bytes": MEMLOCK_LIMIT_BYTES,
        "scoring_identity": "issue100-response-boundary-first-regex-v1",
        "recovery_epoch": RECOVERY_EPOCH - 1,
    }
    if previous_identity != expected_previous_identity:
        raise CampaignError("authorization previous-target identity drift")
    for key in ("recovery_amendment_url", "independent_review_url"):
        if not value.get(key):
            raise CampaignError(f"authorization lacks published {key}")
    for key in (
        "accepted_prefix_sha256", "attempt_lineage_sha256", "previous_campaign_control_sha256",
        "recovery_amendment_sha256", "independent_review_sha256",
        "non_scored_conformance_sha256",
    ):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(value.get(key, ""))):
            raise CampaignError(f"authorization has invalid {key}")
    if value.get("clean_reboot_used") not in (True, False):
        raise CampaignError("authorization clean-reboot disposition is invalid")
    prior_attempt_hashes = value.get("prior_attempt_manifest_sha256s")
    if not isinstance(prior_attempt_hashes, list) or \
            len(prior_attempt_hashes) != RECOVERY_ATTEMPT_FIRST - 1 or any(
                not re.fullmatch(r"[0-9a-fA-F]{64}", str(digest))
                for digest in prior_attempt_hashes):
        raise CampaignError("authorization prior-attempt lineage is invalid")
    if value.get("clean_reboot_used") and not re.fullmatch(
            r"[0-9a-fA-F]{64}", str(value.get("reboot_evidence_sha256", ""))):
        raise CampaignError("authorization reboot evidence identity is invalid")
    return value


def bounded_attempt_timeout(remaining_attempt_s: float) -> float:
    """Apply the frozen recovery-v5 watchdog without enlarging the campaign budget."""
    return min(float(ATTEMPT_TIMEOUT_S), remaining_attempt_s)


def build_run_record(
    run: dict[str, Any],
    item: dict[str, Any],
    result: dict[str, Any],
    score: dict[str, Any],
    attempt_ordinal: int,
    attempt_manifest: Path,
    identity: dict[str, Any],
    campaign_started: float,
) -> dict[str, Any]:
    generation = result["generation"]
    cache = result["cache"]["total"]
    io = result["io"]["total"]
    routing = result["routing"]["stats"]
    timing = result["timing"]
    return bind_checksum({
        "schema_version": "issue100-accepted-run-v1",
        "timestamp": utc_now(),
        **identity,
        **run,
        "category": item["domain"],
        "subdomain": item["subdomain"],
        "attempt_ordinal": attempt_ordinal,
        "seed": item["generation_seed"],
        "repeat": 0,
        "prompt_sha256": item["prompt_sha256"],
        "prompt_tokens": item["prompt_tokens"],
        "generated_tokens": generation["generated_tokens_including_eog"],
        "first_generated_token_id": generation["token_ids"][0],
        "reasoning_tokens": score["reasoning_tokens"],
        "transition_tokens": score["transition_tokens"],
        "response_tokens": score["response_tokens"],
        "raw_output_sha256": score["raw_output_sha256"],
        "content_sha256": score["content_sha256"],
        "response_sha256": score["response_sha256"],
        "extracted_answer": score["extracted_answer"],
        "correct_answer": score["correct_answer"],
        "correct": score["correct"],
        "invalid": score["invalid"],
        "malformed": score["malformed"],
        "truncated": score["truncated"],
        "outcome": score["outcome"],
        "prefill_wall_s": timing["prefill_wall_s"],
        "decode_wall_s": generation["decode_inference_s"],
        "generation_wall_s": generation["generation_wall_s"],
        "total_wall_s": timing["process_wall_s"],
        "decode_tok_s": generation["decode_tok_s"],
        "end_to_end_generated_tok_s": (
            generation["generated_tokens_including_eog"]/timing["process_wall_s"]
            if timing["process_wall_s"] > 0 else 0.0
        ),
        "cache_hits": cache["hits"],
        "cache_misses": cache["misses"],
        "cache_loads": io["backing_loads"],
        "backing_bytes": io["backing_bytes"],
        "realized_swaps": routing["swaps"],
        "changed_decisions": routing["changed_decisions"],
        "cumulative_corrected_regret": routing["cumulative_score_regret"],
        "capacity_request_mode": result["execution"]["capacity_request_mode"],
        "auto_resolved_slots": result["cache"]["capacity_slots"],
        "auto_resolved_bytes": result["cache"]["capacity_bytes"],
        "autofit": result["preflight"]["system_memory"]["autofit"],
        "resource_status": result["safety"]["status"],
        "attempt_manifest_path": str(attempt_manifest.resolve()),
        "attempt_manifest_sha256": sha256_file(attempt_manifest),
        "campaign_elapsed_s": time.monotonic() - campaign_started,
    })


def print_run_progress(record: dict[str, Any], runs: list[dict[str, Any]]) -> None:
    if record["stage"] == "A":
        prefix = f"PAIR {record['pair_ordinal']:02d}/30 {record['arm']}"
        if record["arm"] == "S2_P50":
            prefix += f" | S2 {record['s2_ordinal']:03d}/198"
    else:
        prefix = f"S2 {record['s2_ordinal']:03d}/198"
    print(
        f"[{prefix}] outcome={record['outcome']} correct={int(record['correct'])} "
        f"out={record['generated_tokens']} decode={record['decode_tok_s']:.4f} tok/s "
        f"wall={record['total_wall_s']/60:.1f}m auto_slots={record['auto_resolved_slots']} status=PASS",
        flush=True,
    )
    if record["stage"] == "B":
        s2 = [row for row in runs if row["arm"] == "S2_P50"]
        tps = [row["decode_tok_s"] for row in s2]
        walls = [row["total_wall_s"] for row in s2]
        remaining = 198 - len(s2)
        print(
            f"ISSUE100_S2_PROGRESS completed={len(s2)}/198 correct={sum(x['correct'] for x in s2)} "
            f"accuracy={sum(x['correct'] for x in s2)/len(s2):.6f} "
            f"tps_mean={statistics.fmean(tps):.6f} tps_median={statistics.median(tps):.6f} "
            f"auto_slots_min={min(x['auto_resolved_slots'] for x in s2)} "
            f"auto_slots_max={max(x['auto_resolved_slots'] for x in s2)} "
            f"eta_s={remaining*statistics.fmean(walls):.0f} resource=PASS",
            flush=True,
        )


def print_pair_progress(pair: dict[str, Any], runs: list[dict[str, Any]], pairs: list[dict[str, Any]]) -> None:
    exact = [row for row in runs if row["stage"] == "A" and row["arm"] == "EXACT"]
    s2 = [row for row in runs if row["stage"] == "A" and row["arm"] == "S2_P50"]
    exact_tps = statistics.fmean(row["decode_tok_s"] for row in exact)
    s2_tps = statistics.fmean(row["decode_tok_s"] for row in s2)
    pair_walls = [
        runs[(ordinal - 1)*2]["total_wall_s"] + runs[(ordinal - 1)*2 + 1]["total_wall_s"]
        for ordinal in range(1, len(pairs) + 1)
    ]
    print(
        f"ISSUE100_PAIR completed={len(pairs)}/30 class={pair['pair_class']} "
        f"exact={sum(row['correct'] for row in exact)}/{len(exact)} "
        f"s2={sum(row['correct'] for row in s2)}/{len(s2)} "
        f"delta={sum(row['accuracy_delta'] for row in pairs)/len(pairs):+.6f} "
        f"exact_auto_slots={pair['exact_auto_slots']} s2_auto_slots={pair['s2_auto_slots']} "
        f"auto_slot_delta={pair['auto_slot_delta']:+d} "
        f"exact_tps_mean={exact_tps:.6f} s2_tps_mean={s2_tps:.6f} "
        f"tps_ratio={s2_tps/exact_tps:.6f} "
        f"eta_pair30_s={(30-len(pairs))*statistics.fmean(pair_walls):.0f}",
        flush=True,
    )


def build_probe_command(
    binary: Path,
    model: Path,
    input_path: Path,
    result_path: Path,
    progress_path: Path,
    arm: str,
    seed: int,
) -> list[str]:
    """Build one direct production-AUTO benchmark-process invocation."""
    return [
        str(binary), "--model", str(model), "--input", str(input_path),
        "--output", str(result_path), "--progress", str(progress_path),
        "--arm", arm, "--seed", str(seed),
        "--max-generated", str(MAX_GENERATED), "--n-ctx", str(N_CTX),
        "--threads", str(THREADS), "--issue-mode", "BATCHED",
    ]


def attempt_window(run_ordinal: int) -> tuple[int, int]:
    if run_ordinal == RECOVERY_RUN_ORDINAL:
        return RECOVERY_ATTEMPT_FIRST, RECOVERY_ATTEMPT_LAST
    return 1, MAX_RESTARTS + 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--protected-plan", type=Path, required=True)
    parser.add_argument("--execution-authorization", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    args = parser.parse_args()

    require_frozen_memlock()

    repo_root = args.repo_root.resolve(strict=True)
    auto_admission_path = (repo_root / PUBLIC_AUTO_ADMISSION).resolve(strict=True)
    if sha256_file(auto_admission_path) != AUTO_ADMISSION_SHA256:
        raise CampaignError("AUTO admission amendment identity drift")
    binary = args.binary.resolve(strict=True)
    model = args.model.resolve(strict=True)
    protected_plan_path = args.protected_plan.resolve(strict=True)
    authorization = validate_authorization(
        args.execution_authorization.resolve(strict=True),
        preregistration=args.preregistration.resolve(strict=True),
        protected_plan=protected_plan_path,
        binary=binary,
    )
    repository = require_frozen_runtime_identity(
        repo_root, authorized_project_commit=authorization["project_commit"],
    )
    protected_plan = load_json(protected_plan_path)
    if protected_plan.get("schema_version") != "issue100-protected-execution-plan-v1" or \
            protected_plan.get("campaign_sha256") != CAMPAIGN_SHA256 or \
            len(protected_plan.get("runs", [])) != 228:
        raise CampaignError("protected execution plan drift")
    items_root = Path(protected_plan["items_root"]).resolve(strict=True)
    if not model.is_file() or model.name != "kimi-k3-bf16-00001-of-00033.gguf":
        raise CampaignError("frozen K3 model first shard unavailable")

    root = args.output_root.resolve()
    (root / "attempts").mkdir(parents=True, exist_ok=True)
    identity = {
        "campaign_sha256": CAMPAIGN_SHA256,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "project_commit": repository["project_commit"],
        "nested_commit": repository["nested_commit"],
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "adapter_binary_sha256": sha256_file(binary),
        "protected_plan_sha256": sha256_file(protected_plan_path),
        "execution_authorization_sha256": sha256_file(args.execution_authorization),
        "auto_admission_sha256": AUTO_ADMISSION_SHA256,
        "capacity_request_mode": "AUTO",
        "capacity_request_bytes": AUTO_CACHE_REQUEST_BYTES,
        "capacity_floor_slots": CAPACITY_FLOOR_SLOTS,
        "capacity_floor_bytes": CAPACITY_FLOOR_BYTES,
        "memlock_limit_bytes": MEMLOCK_LIMIT_BYTES,
        "attempt_timeout_s": ATTEMPT_TIMEOUT_S,
        "campaign_cumulative_attempt_budget_s": CUMULATIVE_ATTEMPT_BUDGET_S,
        "max_restarts": MAX_RESTARTS,
        "scoring_identity": "issue100-response-boundary-first-regex-v1",
        "recovery_epoch": authorization["recovery_epoch"],
    }
    control_path = root / "campaign-control.json"
    runs_path = root / "runs.jsonl"
    runs = validate_existing_runs(runs_path, identity, authorization)
    prior_attempt_hashes = validate_recovery_attempt_lineage(root, authorization)
    if control_path.exists():
        control = load_json(control_path)
        if control.get("project_commit") == PREVIOUS_PROJECT_COMMIT:
            if sha256_file(control_path) != authorization.get("previous_campaign_control_sha256") or \
                    control.get("status") != "halted" or \
                    control.get("failed_run") != RECOVERY_RUN_ORDINAL or \
                    control.get("failed_attempt") != RECOVERY_ATTEMPT_FIRST - 1:
                raise CampaignError("pre-recovery campaign-control state drift")
            for key, expected in authorization["previous_identity"].items():
                if control.get(key) != expected:
                    raise CampaignError(f"pre-recovery campaign-control identity drift: {key}")
            previous_control_sha256 = sha256_file(control_path)
            previous_epochs = control.get("recovery_epochs")
            if not isinstance(previous_epochs, list) or \
                    len(previous_epochs) != RECOVERY_EPOCH - 1:
                raise CampaignError("pre-recovery campaign-control epoch drift")
            previous_epochs[-1] = dict(previous_epochs[-1])
            previous_epochs[-1].update({
                "accepted_runs": len(runs),
                "campaign_control_sha256": previous_control_sha256,
                "attempt_manifest_sha256s": prior_attempt_hashes,
            })
            control.update(identity)
            control.update({
                "schema_version": "issue100-campaign-control-v3",
                "status": "running",
                "recovery_started_at": utc_now(),
                "recovery_epochs": [
                    *previous_epochs,
                    {
                        "epoch": RECOVERY_EPOCH,
                        "status": "running",
                        "project_commit": identity["project_commit"],
                        "nested_commit": identity["nested_commit"],
                        "adapter_binary_sha256": identity["adapter_binary_sha256"],
                        "execution_authorization_sha256": identity["execution_authorization_sha256"],
                        "memlock_limit_bytes": identity["memlock_limit_bytes"],
                        "previous_attempt_timeout_s": PREVIOUS_ATTEMPT_TIMEOUT_S,
                        "attempt_timeout_s": ATTEMPT_TIMEOUT_S,
                        "campaign_cumulative_attempt_budget_s": CUMULATIVE_ATTEMPT_BUDGET_S,
                        "max_restarts": MAX_RESTARTS,
                        "recovery_run_ordinal": RECOVERY_RUN_ORDINAL,
                        "attempt_first": RECOVERY_ATTEMPT_FIRST,
                        "attempt_last": RECOVERY_ATTEMPT_LAST,
                    },
                ],
            })
            for key in ("reason", "failed_run", "failed_attempt"):
                control.pop(key, None)
            persist_campaign_control(control_path, control)
        else:
            for key, expected in identity.items():
                if control.get(key) != expected:
                    raise CampaignError(f"campaign-control resume identity drift: {key}")
            if control.get("schema_version") != "issue100-campaign-control-v3" or \
                    len(control.get("recovery_epochs", [])) != RECOVERY_EPOCH:
                raise CampaignError("campaign-control recovery epoch drift")
    else:
        raise CampaignError("recovery target requires the preserved campaign-control lineage")

    campaign_started = time.monotonic()
    pairs = reconcile_pairs(root, runs)
    checkpoint(root, runs, pairs, "running")
    host = load_host_helpers(repo_root)

    for run in protected_plan["runs"][len(runs):]:
        expected_ordinal = len(runs) + 1
        if run.get("run_ordinal") != expected_ordinal:
            raise CampaignError("next protected run ordinal drift")
        item_path = items_root / f"{run['item_id']}.json"
        item = load_json(item_path)
        if item.get("schema_version") != "issue100-protected-item-v1" or \
                item.get("record_id") != run["item_id"] or \
                sha256_bytes(item["rendered_prompt"].encode("utf-8")) != item["prompt_sha256"]:
            raise CampaignError("protected item identity drift")
        if cumulative_attempt_seconds(root) >= CUMULATIVE_ATTEMPT_BUDGET_S:
            control.update({"status": "halted", "reason": "cumulative-attempt-budget"})
            persist_campaign_control(control_path, control)
            raise CampaignError("cumulative 50-day attempt budget exhausted")

        slug = f"run-{expected_ordinal:03d}-{run['item_id']}-{run['arm'].lower()}"
        run_attempt_root = root / "attempts" / slug
        run_attempt_root.mkdir(parents=True, exist_ok=True)
        accepted = None
        attempt_first, attempt_last = attempt_window(expected_ordinal)
        for attempt_ordinal in range(attempt_first, attempt_last + 1):
            directory = run_attempt_root / f"attempt-{attempt_ordinal:02d}"
            if directory.exists():
                if (directory / "attempt-manifest.json").exists():
                    prior = load_json(directory / "attempt-manifest.json")
                    if prior.get("accepted"):
                        raise CampaignError("accepted attempt exists without accepted run record")
                    continue
                seal_interrupted_attempt(directory, {
                    **run, "attempt_ordinal": attempt_ordinal,
                    "campaign_sha256": CAMPAIGN_SHA256,
                    "recovery_epoch": RECOVERY_EPOCH,
                })
                continue
            directory.mkdir(parents=False)
            consumed_attempt_s = cumulative_attempt_seconds(root)
            remaining_attempt_s = CUMULATIVE_ATTEMPT_BUDGET_S - consumed_attempt_s
            if remaining_attempt_s <= 0:
                seal_interrupted_attempt(directory, {
                    **run, "attempt_ordinal": attempt_ordinal,
                    "campaign_sha256": CAMPAIGN_SHA256,
                    "recovery_epoch": RECOVERY_EPOCH,
                })
                control.update({"status": "halted", "reason": "cumulative-attempt-budget"})
                persist_campaign_control(control_path, control)
                raise CampaignError("cumulative 50-day attempt budget exhausted")
            attempt_timeout_s = bounded_attempt_timeout(remaining_attempt_s)
            probe_input = {
                "schema_version": "issue100-probe-input-v1",
                "item_id": item["record_id"],
                "rendered_prompt": item["rendered_prompt"],
                "prompt_sha256": item["prompt_sha256"],
                "prompt_tokens": item["prompt_tokens"],
                "scored": True,
            }
            atomic_json(directory / "input.json", probe_input)
            result_path = directory / "probe-result.json"
            progress_path = directory / "progress.jsonl"
            command = build_probe_command(
                binary, model, directory / "input.json", result_path, progress_path,
                run["arm"], item["generation_seed"],
            )
            attempt_identity = {
                **run, "attempt_ordinal": attempt_ordinal,
                "campaign_sha256": CAMPAIGN_SHA256,
                "recovery_epoch": RECOVERY_EPOCH,
            }
            returncode, timed_out, envelope = run_with_envelope(
                command, directory, attempt_identity, host, timeout_s=attempt_timeout_s,
            )
            artifacts = {}
            for name in (
                "attempt-start.json", "input.json", "probe-result.json", "progress.jsonl",
                "stdout.log", "stderr.log", "envelope.json",
            ):
                path = directory / name
                if path.exists():
                    fsync_file(path)
                    artifacts[name] = file_identity(path)
            external_pressure = pressure_failures(envelope)
            probe_error = (directory / "stderr.log").read_text(errors="replace")
            hard_probe_failure = structured_hard_probe_failure(result_path) or any(
                marker.lower() in probe_error.lower() for marker in HARD_PROBE_MARKERS
            )
            attempt_manifest_value = {
                "schema_version": "issue100-attempt-manifest-v1",
                **attempt_identity,
                "exit_status": returncode,
                "timed_out": timed_out,
                "external_pressure_failures": external_pressure,
                "hard_probe_failure": hard_probe_failure,
                "accepted": False,
                "artifacts": artifacts,
            }
            if timed_out or external_pressure or hard_probe_failure:
                atomic_json(directory / "attempt-manifest.json", attempt_manifest_value)
                control.update({
                    "status": "halted", "failed_run": expected_ordinal,
                    "failed_attempt": attempt_ordinal,
                    "reason": (
                        "cumulative-attempt-budget" if timed_out and attempt_timeout_s < ATTEMPT_TIMEOUT_S else
                        "timeout" if timed_out else
                        "external-pressure" if external_pressure else "hard-probe-failure"
                    ),
                })
                persist_campaign_control(control_path, control)
                raise CampaignError(control["reason"])
            if returncode != 0 or not result_path.exists() or not progress_path.exists():
                atomic_json(directory / "attempt-manifest.json", attempt_manifest_value)
                if attempt_ordinal < attempt_last:
                    print(
                        f"ISSUE100_RETRY run={expected_ordinal:03d} attempt={attempt_ordinal} "
                        f"exit={returncode}", flush=True,
                    )
                    continue
                control.update({
                    "status": "halted", "failed_run": expected_ordinal,
                    "failed_attempt": attempt_ordinal, "reason": "restart-budget",
                })
                persist_campaign_control(control_path, control)
                raise CampaignError("non-semantic partial failure restart budget exhausted")

            result = validate_probe_result(result_path, progress_path, run, item, envelope)
            score = score_result(result, item)
            score_evidence = {
                "schema_version": "issue100-protected-score-evidence-v1",
                "item_id": run["item_id"],
                "arm": run["arm"],
                "seed": item["generation_seed"],
                "raw_generated_bytes_hex": score["raw_bytes"].hex(),
                "content_before_eog_hex": score["content_bytes"].hex(),
                "reasoning_bytes_hex": score["reasoning_bytes"].hex(),
                "transition_bytes_hex": score["transition_bytes"].hex(),
                "response_bytes_hex": score["response_bytes"].hex(),
                "raw_output_sha256": score["raw_output_sha256"],
                "content_sha256": score["content_sha256"],
                "response_sha256": score["response_sha256"],
                "extracted_answer": score["extracted_answer"],
                "correct_answer": score["correct_answer"],
                "correct": score["correct"],
                "invalid": score["invalid"],
                "malformed": score["malformed"],
                "truncated": score["truncated"],
                "outcome": score["outcome"],
                "reasoning_tokens": score["reasoning_tokens"],
                "transition_tokens": score["transition_tokens"],
                "response_tokens": score["response_tokens"],
            }
            atomic_json(directory / "score-evidence.json", score_evidence)
            attempt_manifest_value["artifacts"]["score-evidence.json"] = file_identity(
                directory / "score-evidence.json"
            )
            attempt_manifest_value["accepted"] = True
            attempt_manifest_value["score_audit"] = {
                key: score[key] for key in (
                    "raw_output_sha256", "content_sha256", "response_sha256",
                    "extracted_answer", "correct_answer", "correct", "invalid",
                    "malformed", "truncated", "outcome", "reasoning_tokens",
                    "transition_tokens", "response_tokens",
                )
            }
            atomic_json(directory / "attempt-manifest.json", attempt_manifest_value)
            attempt_manifest = directory / "attempt-manifest.json"
            accepted = build_run_record(
                run, item, result, score, attempt_ordinal, attempt_manifest,
                identity, campaign_started,
            )
            append_canonical_jsonl(runs_path, accepted)
            runs.append(accepted)
            checkpoint(root, runs, pairs, "running")
            print_run_progress(accepted, runs)
            if run["stage"] == "A" and run["arm"] == "S2_P50":
                pairs = reconcile_pairs(root, runs)
                checkpoint(root, runs, pairs, "running")
                print_pair_progress(pairs[-1], runs, pairs)
            break
        if accepted is None:
            raise CampaignError("internal retry loop ended without acceptance")

    if len(runs) != 228 or len(pairs) != 30 or \
            sum(row["arm"] == "EXACT" for row in runs) != 30 or \
            sum(row["arm"] == "S2_P50" for row in runs) != 198:
        raise CampaignError("terminal campaign cardinality mismatch")
    checkpoint(root, runs, pairs, "complete")
    control.update({
        "status": "complete", "completed_at": utc_now(),
        "accepted_runs": len(runs), "completed_pairs": len(pairs),
        "cumulative_attempt_wall_s": cumulative_attempt_seconds(root),
    })
    persist_campaign_control(control_path, control)
    print("ISSUE100_CAMPAIGN status=complete runs=228 pairs=30 s2=198", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 campaign: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

#!/usr/bin/env python3
"""Run one bounded full-model Colibrì/Kimi-K3 endpoint measurement."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import resource
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_real_routing import (  # noqa: E402
    COLIBRI_COMMIT,
    DEFAULT_PROMPT,
    MODEL_REVISION,
    ROUTED_LAYERS,
    TOP_K,
    normalize_route,
)


EXPERT_BYTES = 17_547_264
MAX_RSS_BYTES = 161_639_786_086
NON_CACHE_RSS_BYTES = 39_041_900_544
MAX_RUNTIME_SECONDS = 7_200
PERF_EVENTS = (
    "cycles:u,instructions:u,cache-misses:u,branches:u,branch-misses:u,"
    "context-switches,cpu-migrations,major-faults,minor-faults"
)


def slots_for_capacity_bytes(capacity_bytes: int) -> int:
    return capacity_bytes // (ROUTED_LAYERS * EXPERT_BYTES)


def derive_max_safe_slots(ceiling_bytes: int, non_cache_rss_bytes: int) -> int:
    if ceiling_bytes <= non_cache_rss_bytes:
        return 0
    return slots_for_capacity_bytes(ceiling_bytes - non_cache_rss_bytes)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": sha256_file(path)}


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        fields = value.strip().split()
        if fields and fields[0].isdigit():
            values[key] = int(fields[0]) * (1024 if len(fields) > 1 and fields[1] == "kB" else 1)
    return values


def cgroup_path() -> Path:
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            return Path("/sys/fs/cgroup") / line.split("::", 1)[1].lstrip("/")
    raise RuntimeError("unified cgroup v2 path unavailable")


def cgroup_values(base: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("memory.current", "memory.peak", "memory.swap.current", "memory.max", "memory.swap.max"):
        path = base / name
        if path.is_file():
            value = path.read_text().strip()
            result[name] = int(value) if value.isdigit() else value
    events = base / "memory.events"
    if events.is_file():
        result["memory.events"] = {
            key: int(value) for key, value in (line.split() for line in events.read_text().splitlines())
        }
    return result


def block_stat(path: Path) -> dict[str, int]:
    values = [int(value) for value in path.read_text().split()]
    return {
        "read_operations": values[0],
        "read_sectors": values[2],
        "read_ticks_ms": values[3],
        "in_flight": values[8],
        "io_ticks_ms": values[9],
        "weighted_ticks_ms": values[10],
    }


def block_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    result = {key: after[key] - before[key] for key in before if key != "in_flight"}
    result["read_bytes"] = result["read_sectors"] * 512
    return result


def proc_snapshot(pid: int) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": pid}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            if key in {"VmRSS", "VmHWM", "VmSwap", "RssAnon", "RssFile", "RssShmem"}:
                result[key] = int(value.strip().split()[0]) * 1024
            elif key in {"Threads", "voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"}:
                result[key] = int(value.strip())
        for line in Path(f"/proc/{pid}/io").read_text().splitlines():
            key, value = line.split(":", 1)
            result[f"io_{key}"] = int(value)
        stat = Path(f"/proc/{pid}/stat").read_text().split()
        result.update({"minor_faults": int(stat[9]), "major_faults": int(stat[11]),
                       "user_ticks": int(stat[13]), "system_ticks": int(stat[14])})
        smaps = Path(f"/proc/{pid}/smaps_rollup")
        if smaps.is_file():
            for line in smaps.read_text().splitlines():
                key, separator, value = line.partition(":")
                if separator and key in {"Rss", "Pss", "Pss_Anon", "Pss_File", "Swap", "SwapPss"}:
                    result[f"smaps_{key}"] = int(value.strip().split()[0]) * 1024
    except (FileNotFoundError, PermissionError, ProcessLookupError, IndexError):
        result["unavailable"] = True
    return result


def descendants(parent: int) -> list[int]:
    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().split()
            parents[int(entry.name)] = int(fields[3])
        except (FileNotFoundError, PermissionError, ProcessLookupError, IndexError, ValueError):
            continue
    found: list[int] = []
    frontier = [parent]
    while frontier:
        current = frontier.pop()
        children = [pid for pid, ppid in parents.items() if ppid == current]
        found.extend(children)
        frontier.extend(children)
    return found


def find_model_pid(parent: int, binary: Path) -> int | None:
    for pid in [parent, *descendants(parent)]:
        try:
            if Path(f"/proc/{pid}/exe").resolve() == binary:
                return pid
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    return None


def cpu_frequency_summary() -> dict[str, float] | None:
    values = []
    for path in sorted(Path("/sys/devices/system/cpu/cpufreq").glob("policy*/scaling_cur_freq")):
        try:
            values.append(int(path.read_text()))
        except (FileNotFoundError, PermissionError, ValueError):
            pass
    if not values:
        return None
    return {"min_khz": min(values), "mean_khz": statistics.fmean(values), "max_khz": max(values)}


def parse_endpoint(path: Path) -> dict[str, Any]:
    stacks: dict[int, list[dict[str, Any]]] = defaultdict(list)
    durations: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: list[dict[str, Any]] = []
    tokens: list[int] = []
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream, delimiter="\t"):
            row["ts_ns"] = int(row["ts_ns"])
            row["tid"] = int(row["tid"])
            row["forward"] = int(row["forward"])
            row["layer"] = int(row["layer"])
            if row["event"] == "B":
                stacks[row["tid"]].append(row)
            elif row["event"] == "E":
                if not stacks[row["tid"]]:
                    raise ValueError("endpoint scope end without begin")
                begin = stacks[row["tid"]].pop()
                if begin["name"] != row["name"]:
                    raise ValueError("endpoint scopes are not properly nested")
                durations[begin["name"]].append({
                    "phase": begin["phase"], "forward": begin["forward"], "layer": begin["layer"],
                    "start_ns": begin["ts_ns"], "end_ns": row["ts_ns"],
                    "duration_seconds": (row["ts_ns"] - begin["ts_ns"]) / 1e9,
                })
            elif row["event"] == "S":
                stats.append({**row, **{f"v{i}": int(row[f"v{i}"]) for i in range(10)}})
            elif row["event"] == "T":
                tokens.append(int(row["v1"]))
    if any(stacks.values()):
        raise ValueError("endpoint scope stack was not drained")
    decode_forwards = [item for item in durations["forward"] if item["phase"] == "decode"]
    forward_stats = [item for item in stats if item["name"] == "forward" and item["phase"] == "decode"]
    layer_stats = [item for item in stats if item["name"] == "layer" and item["phase"] == "decode"]
    run_stats = [item for item in stats if item["name"] == "run"]
    if len(run_stats) != 1:
        raise ValueError("endpoint telemetry does not contain exactly one final run row")
    return {
        "durations": durations,
        "decode_forwards": decode_forwards,
        "forward_stats": forward_stats,
        "layer_stats": layer_stats,
        "run_stats": run_stats[0],
        "tokens": tokens,
    }


def distribution(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"count": 0}
    def percentile(p: float) -> float:
        index = max(0, min(len(ordered) - 1, math.ceil(p * len(ordered)) - 1))
        return ordered[index]
    return {"count": len(values), "mean": statistics.fmean(values), "p50": percentile(0.50),
            "p95": percentile(0.95), "p99": percentile(0.99), "max": ordered[-1]}


def parse_perf(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not path.is_file():
        return result
    for line in path.read_text(errors="replace").splitlines():
        fields = line.split(",")
        if len(fields) < 3:
            continue
        raw, _, event = fields[:3]
        value = raw.strip().replace(" ", "")
        if not value or value.startswith("<"):
            result[event] = {"status": "unavailable", "raw": raw.strip()}
        else:
            try:
                result[event] = {"status": "available", "value": int(float(value))}
            except ValueError:
                result[event] = {"status": "unavailable", "raw": raw.strip()}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--build-manifest", type=Path, required=True)
    parser.add_argument("--snapshot-verification", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--capacity-label", required=True)
    parser.add_argument("--requested-capacity-gib", type=float, required=True)
    parser.add_argument("--slots-per-layer", type=int, required=True)
    parser.add_argument("--block-stat", type=Path, action="append", required=True)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--ngen", type=int, default=256)
    parser.add_argument("--minimum-complete-forwards", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=int, default=MAX_RUNTIME_SECONDS)
    parser.add_argument("--drop-caches", action="store_true")
    parser.add_argument("--trace-marker-bridge", action="store_true")
    parser.add_argument("--disable-perf-stat", action="store_true")
    args = parser.parse_args()

    if not 1 <= args.slots_per_layer <= 896:
        raise ValueError("slots per layer are outside the engine's legal range")
    if not 1 <= args.timeout_seconds <= MAX_RUNTIME_SECONDS:
        raise ValueError("runtime ceiling exceeds the accepted bound")
    binary = args.binary.resolve()
    model = args.model_dir.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    build_manifest = json.loads(args.build_manifest.read_text())
    snapshot = json.loads(args.snapshot_verification.read_text())
    if build_manifest["status"] != "PASS" or build_manifest["base_commit"] != COLIBRI_COMMIT:
        raise ValueError("instrumented engine build identity mismatch")
    if build_manifest["instrumented_binary"]["sha256"] != sha256_file(binary):
        raise ValueError("instrumented engine binary mismatch")
    if snapshot["status"] != "PASS" or snapshot["revision"] != MODEL_REVISION:
        raise ValueError("Kimi-K3 snapshot identity mismatch")
    if Path(snapshot["snapshot"]).resolve() != model:
        raise ValueError("model path differs from the verified snapshot")
    configured_top_k = int(json.loads((model / "config.json").read_text())["text_config"]["num_experts_per_token"])
    if configured_top_k != TOP_K:
        raise ValueError("model top-k is not 16")

    usable_cache_bytes = args.slots_per_layer * ROUTED_LAYERS * EXPERT_BYTES
    requested_bytes = int(args.requested_capacity_gib * (1 << 30))
    if usable_cache_bytes > requested_bytes and args.capacity_label != "MAX_SAFE":
        raise ValueError("legal cache allocation exceeds the requested capacity")
    projected_rss = NON_CACHE_RSS_BYTES + usable_cache_bytes
    if projected_rss > MAX_RSS_BYTES:
        raise ValueError("projected process RSS crosses the accepted ceiling")
    next_rss = projected_rss + ROUTED_LAYERS * EXPERT_BYTES
    if args.capacity_label == "MAX_SAFE" and next_rss <= MAX_RSS_BYTES:
        raise ValueError("MAX_SAFE did not select the greatest legal whole-slot capacity")

    before_mem = meminfo()
    if before_mem.get("SwapTotal", 0) - before_mem.get("SwapFree", 0) != 0:
        raise RuntimeError("preflight swap usage is nonzero")
    if before_mem["MemAvailable"] < projected_rss + 8 * (1 << 30):
        raise RuntimeError("preflight lacks the declared process ceiling plus 8 GiB host reserve")
    group = cgroup_path()
    before_cgroup = cgroup_values(group)
    if args.drop_caches:
        subprocess.run(["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)

    route_path = output / "route-trace.txt"
    normalized_path = output / "normalized-route.tsv"
    endpoint_path = output / "endpoint-trace.tsv"
    stdout_path = output / "stdout.bin"
    stderr_path = output / "stderr.log"
    stderr_events_path = output / "stderr-events.json"
    stdout_events_path = output / "stdout-events.json"
    samples_path = output / "resource-samples.json"
    perf_path = output / "perf-stat.csv"
    summary_path = output / "run.json"
    marker_log = output / "trace-marker-bridge.log"

    env = os.environ.copy()
    for name in ("CACHE_ROUTE", "COLI_USAGE", "K3_TRACE", "K3_X0", "K3_LOGITS", "K3_VK", "K3_VK_GB"):
        env.pop(name, None)
    budget_decimal_gb = (usable_cache_bytes + 4096) / 1e9
    env.update({
        "COLI_TEMP": "0", "K3_BITS": "4", "K3_MLA_BITS": "8", "K3_HEAD_BITS": "8",
        "K3_EXPERT_GB": f"{budget_decimal_gb:.9f}", "K3_DIRECT": "1", "K3_IDOT": "1",
        "K3_PIPE": "1", "K3_LOAD_THREADS": "4", "K3_MAXT": "8192", "K3_TOPP": "0",
        "K3_CHUNK": "32", "K3_THINK": "0", "ROUTE_TRACE": str(route_path),
        "K3_ENDPOINT_TRACE": str(endpoint_path),
    })

    marker_write = -1
    if args.trace_marker_bridge:
        marker_write = os.open("/sys/kernel/tracing/trace_marker", os.O_WRONLY)
        env["K3_ENDPOINT_MARKER_FD"] = str(marker_write)

    command = [str(binary), str(model), args.prompt, "--ngen", str(args.ngen)]
    perf_command = ["perf", "stat", "-x,", "-o", str(perf_path), "-e", PERF_EVENTS, "--", *command]
    execution_command = command if args.disable_perf_stat else perf_command
    before_block = {str(path): block_stat(path) for path in args.block_stat}
    before_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    stderr_events: list[dict[str, Any]] = []
    stdout_events: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    model_pid: int | None = None
    timed_out = False

    with stdout_path.open("xb") as stdout_file, stderr_path.open("xb") as stderr_file:
        process = subprocess.Popen(
            execution_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            pass_fds=((marker_write,) if marker_write >= 0 else ()), start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None

        def read_stderr() -> None:
            while True:
                line = process.stderr.readline()
                if not line:
                    return
                elapsed = time.monotonic() - started
                stderr_file.write(line); stderr_file.flush()
                stderr_events.append({"elapsed_seconds": elapsed, "line": line.decode("utf-8", "replace").rstrip("\n")})
                sys.stderr.buffer.write(line); sys.stderr.buffer.flush()

        def read_stdout() -> None:
            while True:
                block = process.stdout.read(4096)
                if not block:
                    return
                elapsed = time.monotonic() - started
                stdout_file.write(block); stdout_file.flush()
                stdout_events.append({"elapsed_seconds": elapsed, "bytes": len(block)})

        stderr_reader = threading.Thread(target=read_stderr)
        stdout_reader = threading.Thread(target=read_stdout)
        stderr_reader.start(); stdout_reader.start()
        while process.poll() is None:
            elapsed = time.monotonic() - started
            if model_pid is None:
                model_pid = find_model_pid(process.pid, binary)
            snapshot_row: dict[str, Any] = {
                "elapsed_seconds": elapsed, "meminfo": meminfo(), "cgroup": cgroup_values(group),
                "cpu_frequency": cpu_frequency_summary(),
                "block_devices": {str(path): block_stat(path) for path in args.block_stat},
            }
            if model_pid is not None:
                snapshot_row["process"] = proc_snapshot(model_pid)
            samples.append(snapshot_row)
            if elapsed > args.timeout_seconds:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                break
            time.sleep(1)
        returncode = process.wait()
        stderr_reader.join(); stdout_reader.join()

    if marker_write >= 0:
        os.close(marker_write); marker_write = -1

    elapsed = time.monotonic() - started
    after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    after_block = {str(path): block_stat(path) for path in args.block_stat}
    after_cgroup = cgroup_values(group)
    stderr_events_path.write_text(json.dumps(stderr_events, indent=2, sort_keys=True) + "\n")
    stdout_events_path.write_text(json.dumps(stdout_events, indent=2, sort_keys=True) + "\n")
    samples_path.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n")

    failures: list[str] = []
    if returncode != 0 or timed_out:
        failures.append("process failed or reached the accepted runtime ceiling")
    stderr_text = stderr_path.read_text(errors="replace")
    init = re.search(r"\[K3\] init done in ([0-9.]+)s \| ([0-9]+) layers \| expert cache ([0-9]+)/layer .* \| RSS ([0-9.]+) GB", stderr_text)
    decode = re.search(r"\[K3\] decode ([0-9]+) tokens in ([0-9.]+)s \(([0-9.]+) tok/s\) \| expert hit ([0-9.]+)% \(([0-9]+)/([0-9]+)\) \| ([0-9.]+) GB streamed", stderr_text)
    prefill = re.search(r"\[K3\] prefill done in ([0-9.]+)s \(([0-9.]+) tok/s\)", stderr_text)
    prompt = re.search(r"\[K3\] prompt: ([0-9]+) tokens \| ngen ([0-9]+) \| temp ([0-9.]+)", stderr_text)
    component = re.search(r"\[K3\] time: attn ([0-9.]+)s moe ([0-9.]+)s \(eload ([0-9.]+)s\) head ([0-9.]+)s \| RSS ([0-9.]+) GB", stderr_text)
    if not all((init, decode, prefill, prompt, component)):
        failures.append("required engine metrics are missing")

    route: dict[str, Any] = {}
    endpoint: dict[str, Any] = {}
    try:
        prompt_tokens = int(prompt.group(1)) if prompt else 0
        route = normalize_route(route_path, normalized_path, 0, prompt_tokens, 32)
        endpoint = parse_endpoint(endpoint_path)
        if route["complete_decode_forwards"] < args.minimum_complete_forwards:
            failures.append("route trace contains too few complete decode forwards")
        if len(endpoint["decode_forwards"]) < args.minimum_complete_forwards:
            failures.append("endpoint telemetry contains too few complete decode forwards")
        if len(endpoint["layer_stats"]) != len(endpoint["decode_forwards"]) * ROUTED_LAYERS:
            failures.append("endpoint layer telemetry is incomplete")
        if len(endpoint["tokens"]) != int(decode.group(1)) if decode else True:
            failures.append("token telemetry count differs from engine decode count")
        run_stats = endpoint["run_stats"]
        if run_stats["v7"] != 0:
            failures.append("direct expert reads fell back to the buffered full-expert path")
        if run_stats["v6"] == 0:
            failures.append("no direct expert reads were observed")
        if run_stats["v3"] > args.slots_per_layer * ROUTED_LAYERS:
            failures.append("actual cache occupancy exceeded the fixed legal allocation")
    except (FileNotFoundError, KeyError, ValueError) as error:
        failures.append(str(error))

    process_samples = [row["process"] for row in samples if "process" in row and not row["process"].get("unavailable")]
    max_rss = max((int(row.get("VmHWM", 0)) for row in process_samples), default=0)
    max_swap = max((int(row.get("VmSwap", 0)) for row in process_samples), default=0)
    min_available = min((int(row["meminfo"].get("MemAvailable", 0)) for row in samples), default=0)
    if max_rss > MAX_RSS_BYTES:
        failures.append("measured process RSS crossed the accepted ceiling")
    if max_swap != 0 or any(int(row["meminfo"].get("SwapTotal", 0)) - int(row["meminfo"].get("SwapFree", 0)) for row in samples):
        failures.append("swap was used during the authoritative run")
    before_events = before_cgroup.get("memory.events", {})
    after_events = after_cgroup.get("memory.events", {})
    event_delta = {key: int(after_events.get(key, 0)) - int(before_events.get(key, 0)) for key in after_events}
    if any(event_delta.get(key, 0) for key in ("oom", "oom_kill", "max")):
        failures.append("cgroup memory/OOM ceiling event changed")
    if "context full" in stderr_text or "[K3] TOPP=" in stderr_text:
        failures.append("context or top-p changed the requested decode")

    runtime: dict[str, Any] = {}
    if all((init, decode, prefill, prompt, component)):
        assert init and decode and prefill and prompt and component
        decode_latencies = [item["duration_seconds"] for item in endpoint.get("decode_forwards", [])]
        first_output = stdout_events[0]["elapsed_seconds"] if stdout_events else None
        runtime = {
            "init_seconds": float(init.group(1)), "layers": int(init.group(2)),
            "cache_slots_per_layer": int(init.group(3)), "init_reported_rss_gb": float(init.group(4)),
            "prompt_tokens": int(prompt.group(1)), "requested_decode_tokens": int(prompt.group(2)),
            "temperature": float(prompt.group(3)), "prefill_seconds": float(prefill.group(1)),
            "prefill_tokens_per_second": float(prefill.group(2)), "generated_tokens": int(decode.group(1)),
            "decode_seconds": float(decode.group(2)), "actual_decode_tokens_per_second": float(decode.group(3)),
            "expert_hit_percent": float(decode.group(4)), "expert_hits": int(decode.group(5)),
            "expert_accesses": int(decode.group(6)), "expert_streamed_gb_decimal": float(decode.group(7)),
            "component_seconds": {"attention": float(component.group(1)), "moe": float(component.group(2)),
                                  "exposed_expert_load_wait": float(component.group(3)), "head": float(component.group(4))},
            "final_reported_rss_gb": float(component.group(5)), "process_start_to_first_output_seconds": first_output,
            "model_ready_to_first_output_seconds": first_output - float(init.group(1)) if first_output is not None else None,
            "decode_forward_latency_seconds": distribution(decode_latencies),
            "first_32_decode_forward_latency_seconds": distribution(decode_latencies[:32]),
            "remaining_decode_forward_latency_seconds": distribution(decode_latencies[32:]),
        }
        if runtime["cache_slots_per_layer"] != args.slots_per_layer:
            failures.append("engine cache capacity differs from the fixed requested allocation")
        if runtime["generated_tokens"] < args.minimum_complete_forwards:
            failures.append("engine generated too few complete decode tokens")

    final_proc = process_samples[-1] if process_samples else {}
    proc_io_max = {key: max((int(row.get(key, 0)) for row in process_samples), default=0)
                   for key in ("io_read_bytes", "io_write_bytes", "io_rchar", "io_wchar")}
    token_digest = hashlib.sha256("\n".join(str(value) for value in endpoint.get("tokens", [])).encode()).hexdigest()
    run_env = {key: env[key] for key in sorted(env) if key.startswith("K3_") or key in {"COLI_TEMP", "ROUTE_TRACE"}}
    artifacts = {path.name: identity(path) for path in (
        stdout_path, stderr_path, stderr_events_path, stdout_events_path, samples_path, perf_path,
        route_path, normalized_path, endpoint_path, marker_log,
    ) if path.is_file()}
    document = {
        "schema_version": "phase12-nvme-colibri-endpoint-run-v1",
        "status": "PASS" if not failures else "FAIL",
        "disposition": "accepted" if not failures else "inconclusive",
        "run_id": args.run_id,
        "identity": {"colibri_commit": COLIBRI_COMMIT, "model_revision": MODEL_REVISION,
                     "binary": identity(binary), "build_manifest": identity(args.build_manifest.resolve()),
                     "snapshot_verification": identity(args.snapshot_verification.resolve()),
                     "prompt": args.prompt, "prompt_utf8_sha256": hashlib.sha256(args.prompt.encode()).hexdigest(),
                     "top_k": TOP_K, "K3_TOPP": "0", "sampling": "greedy"},
        "capacity": {"label": args.capacity_label, "requested_gib": args.requested_capacity_gib,
                     "requested_bytes": requested_bytes, "slots_per_layer": args.slots_per_layer,
                     "routed_layers": ROUTED_LAYERS, "expert_bytes": EXPERT_BYTES,
                     "usable_cache_bytes": usable_cache_bytes, "usable_cache_gib": usable_cache_bytes / (1 << 30),
                     "environment_budget_decimal_gb": budget_decimal_gb,
                     "projected_process_rss_bytes": projected_rss, "accepted_process_rss_ceiling_bytes": MAX_RSS_BYTES},
        "command": execution_command, "environment": run_env, "cache_state": "OS_COLD_REQUESTED_AND_DROPPED" if args.drop_caches else "UNCHANGED",
        "trace_marker_bridge": args.trace_marker_bridge,
        "trace_marker_transport": "direct_tracefs_fd" if args.trace_marker_bridge else "disabled",
        "perf_stat_enabled": not args.disable_perf_stat,
        "runtime_ceiling_seconds": args.timeout_seconds,
        "returncode": returncode, "timed_out": timed_out, "runtime": runtime,
        "routing": {**route, "normalized_route": artifacts.get("normalized-route.tsv")},
        "token_ids_sha256": token_digest,
        "cache": {"final_run_counters": endpoint.get("run_stats"), "decode_forward_counters": endpoint.get("forward_stats", [])},
        "process_resources": {"wall_seconds": elapsed,
            "user_cpu_seconds": after_usage.ru_utime - before_usage.ru_utime,
            "system_cpu_seconds": after_usage.ru_stime - before_usage.ru_stime,
            "average_cpu_cores": ((after_usage.ru_utime - before_usage.ru_utime + after_usage.ru_stime - before_usage.ru_stime) / elapsed if elapsed else 0),
            "maximum_rss_bytes": max_rss, "maximum_swap_bytes": max_swap,
            "maximum_threads": max((int(row.get("Threads", 0)) for row in process_samples), default=0),
            "minimum_mem_available_bytes": min_available, "proc_io_maxima": proc_io_max,
            "final_process_snapshot": final_proc, "cgroup_event_delta": event_delta,
            "resource_sample_interval_seconds": 1, "resource_sample_count": len(samples),
            "perf_stat": parse_perf(perf_path), "rapl": {"status": "unavailable", "reason": "/sys/class/powercap exposes no energy_uj counter"}},
        "block_devices": [{"stat_path": name, **block_delta(after_block[name], before_block[name])} for name in sorted(before_block)],
        "raw_artifacts": artifacts,
        "failures": failures,
        "claim_boundary": "actual CPU full-model endpoint evidence; native Colibrì per-layer cache, not project global LRU and no project TPS/H2D/CUDA claim",
    }
    summary_path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    stop_fd = os.environ.get("LLAMA_PERFETTO_STOP_FD")
    if stop_fd:
        os.write(int(stop_fd), b"\x01")
        os.close(int(stop_fd))
    print(json.dumps({"status": document["status"], "run_id": args.run_id,
                      "decode_tps": runtime.get("actual_decode_tokens_per_second"),
                      "max_rss_bytes": max_rss, "failures": failures}, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

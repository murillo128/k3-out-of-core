#!/usr/bin/env python3
"""Run and capture the bounded same-machine Colibrì Kimi-K3 reference."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import resource
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

COLIBRI_COMMIT = "b085b48888a88d9a1c00b151a9979774b72cdbfd"
MODEL_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
TIMEOUT_SECONDS = 7_200
PROMPT = "Say OK"
NGEN = 8
ROUTED_LAYERS = 92
TOP_K = 16


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def proc_values(pid: int) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith(("VmRSS:", "VmHWM:", "Threads:")):
                key, value = line.split(":", 1)
                result[key] = int(value.strip().split()[0]) * (1024 if key != "Threads" else 1)
        for line in Path(f"/proc/{pid}/io").read_text().splitlines():
            key, value = line.split(":", 1)
            result[f"io_{key}"] = int(value)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        pass
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


def delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    return {key: after[key] - before[key] for key in before if key != "in_flight"}


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def match_float(pattern: str, text: str, group: int = 1) -> float:
    match = re.search(pattern, text)
    if not match:
        raise ValueError(f"missing metric: {pattern}")
    return float(match.group(group))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--colibri-source", type=Path, required=True)
    parser.add_argument("--block-stat", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--drop-caches", action="store_true")
    args = parser.parse_args()
    binary = args.binary.resolve()
    model_dir = args.model_dir.resolve()
    source = args.colibri_source.resolve()
    block_path = args.block_stat.resolve()
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if commit != COLIBRI_COMMIT:
        raise ValueError("Colibrì revision mismatch")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
        raise ValueError("Colibrì source is dirty")
    if args.drop_caches:
        subprocess.run(
            ["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"],
            check=True,
        )

    env = os.environ.copy()
    env.update({
        "COLI_TEMP": "0",
        "K3_BITS": "4",
        "K3_MLA_BITS": "8",
        "K3_HEAD_BITS": "8",
        "K3_EXPERT_GB": "8",
        "K3_DIRECT": "1",
        "K3_IDOT": "1",
        "K3_PIPE": "1",
        "K3_LOAD_THREADS": "4",
        # Leave room for the final generated token's full forward pass. The
        # upstream default (prompt + ngen) stops immediately after sampling
        # that token and therefore understates per-forward decode time.
        "K3_MAXT": "8192",
        "K3_TOPP": "0",
        "K3_CHUNK": "32",
        "K3_THINK": "0",
    })
    run_env = {key: env[key] for key in sorted(env) if key.startswith("K3_") or key == "COLI_TEMP"}
    command = [str(binary), str(model_dir), PROMPT, "--ngen", str(NGEN)]
    before_block = block_stat(block_path)
    before_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    assert process.stdout is not None and process.stderr is not None
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    stderr_events: list[dict[str, object]] = []
    first_stdout_seconds: list[float] = []

    def read_stdout() -> None:
        while True:
            block = process.stdout.read(1)
            if not block:
                return
            if not first_stdout_seconds:
                first_stdout_seconds.append(time.monotonic() - started)
            stdout_parts.append(block)
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()

    def read_stderr() -> None:
        while True:
            line = process.stderr.readline()
            if not line:
                return
            elapsed = time.monotonic() - started
            stderr_parts.append(line)
            stderr_events.append({"elapsed_seconds": elapsed, "line": line.decode("utf-8", "replace").rstrip("\n")})
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    threads = [threading.Thread(target=read_stdout), threading.Thread(target=read_stderr)]
    for thread in threads:
        thread.start()
    samples: list[dict[str, object]] = []
    timed_out = False
    while process.poll() is None:
        elapsed = time.monotonic() - started
        values = proc_values(process.pid)
        samples.append({"elapsed_seconds": elapsed, **values})
        if elapsed > TIMEOUT_SECONDS:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            break
        time.sleep(0.25)
    returncode = process.wait()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    after_block = block_stat(block_path)
    stdout = b"".join(stdout_parts)
    stderr = b"".join(stderr_parts)
    stdout_path = raw / "stdout.bin"
    stderr_path = raw / "stderr.log"
    events_path = raw / "stderr-events.json"
    samples_path = raw / "resource-samples.json"
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    events_path.write_text(json.dumps(stderr_events, indent=2, sort_keys=True) + "\n")
    samples_path.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n")

    stderr_text = stderr.decode("utf-8", "replace")
    init_match = re.search(
        r"\[K3\] init done in ([0-9.]+)s \| ([0-9]+) layers \| expert cache ([0-9]+)/layer .* \| RSS ([0-9.]+) GB",
        stderr_text,
    )
    prompt_match = re.search(r"\[K3\] prompt: ([0-9]+) tokens \| ngen ([0-9]+) \| temp ([0-9.]+)", stderr_text)
    decode_match = re.search(
        r"\[K3\] decode ([0-9]+) tokens in ([0-9.]+)s \(([0-9.]+) tok/s\) \| expert hit ([0-9.]+)% \(([0-9]+)/([0-9]+)\) \| ([0-9.]+) GB streamed",
        stderr_text,
    )
    time_match = re.search(
        r"\[K3\] time: attn ([0-9.]+)s moe ([0-9.]+)s \(eload ([0-9.]+)s\) head ([0-9.]+)s \| RSS ([0-9.]+) GB",
        stderr_text,
    )
    parse_ok = bool(init_match and prompt_match and decode_match and time_match)
    failures: list[str] = []
    if returncode != 0 or timed_out:
        failures.append("process failed or exceeded runtime ceiling")
    if not parse_ok:
        failures.append("required Colibrì metrics missing")

    metrics: dict[str, object] = {}
    token_latencies: list[float] = []
    if parse_ok:
        assert init_match and prompt_match and decode_match and time_match
        prompt_tokens = int(prompt_match.group(1))
        generated_tokens = int(decode_match.group(1))
        expert_cache_accesses = int(decode_match.group(6))
        config = json.loads((model_dir / "config.json").read_text())
        configured_top_k = int(config["text_config"]["num_experts_per_token"])
        if int(init_match.group(2)) != 93:
            failures.append("full 93-layer model was not loaded")
        if int(prompt_match.group(2)) != NGEN or generated_tokens != NGEN:
            failures.append("requested full decode did not complete")
        if configured_top_k != TOP_K or run_env["K3_TOPP"] != "0" or "[K3] TOPP=" in stderr_text:
            failures.append("top-16 routing was pruned or configuration changed")
        init_event = next((event for event in stderr_events if "[K3] init done" in str(event["line"])), None)
        token_event_seconds = [
            float(event["elapsed_seconds"]) for event in stderr_events
            if re.search(r"\[tok [0-9]+:", str(event["line"]))
        ]
        if first_stdout_seconds and token_event_seconds:
            previous = first_stdout_seconds[0]
            for timestamp in token_event_seconds:
                token_latencies.append(timestamp - previous)
                previous = timestamp
        metrics = {
            "init_seconds": float(init_match.group(1)),
            "layers": int(init_match.group(2)),
            "expert_cache_slots_per_layer": int(init_match.group(3)),
            "init_reported_rss_gb": float(init_match.group(4)),
            "prompt_tokens": prompt_tokens,
            "prefill_seconds": match_float(r"\[K3\] prefill done in ([0-9.]+)s", stderr_text),
            "prefill_tokens_per_second": match_float(r"prefill done in [0-9.]+s \(([0-9.]+) tok/s\)", stderr_text),
            "process_start_to_first_output_seconds": first_stdout_seconds[0] if first_stdout_seconds else None,
            "model_ready_to_first_output_seconds": (
                first_stdout_seconds[0] - float(init_event["elapsed_seconds"])
                if first_stdout_seconds and init_event else None
            ),
            "generated_tokens": generated_tokens,
            "decode_seconds": float(decode_match.group(2)),
            "decode_tokens_per_second": float(decode_match.group(3)),
            "expert_hit_percent": float(decode_match.group(4)),
            "expert_hits": int(decode_match.group(5)),
            "configured_experts_per_token": configured_top_k,
            "expert_cache_accesses": expert_cache_accesses,
            "expert_streamed_gb_decimal": float(decode_match.group(7)),
            "component_seconds": {
                "attention": float(time_match.group(1)),
                "moe": float(time_match.group(2)),
                "expert_load": float(time_match.group(3)),
                "head": float(time_match.group(4)),
            },
            "final_reported_rss_gb": float(time_match.group(5)),
            "decode_token_latency_seconds": {
                "values": token_latencies,
                "p50": percentile(token_latencies, 0.50),
                "p95": percentile(token_latencies, 0.95),
                "p99": percentile(token_latencies, 0.99),
                "mean": statistics.mean(token_latencies) if token_latencies else math.nan,
            },
        }
    process_io: dict[str, int] = {}
    if samples:
        for key in ("io_read_bytes", "io_write_bytes", "io_rchar", "io_wchar"):
            process_io[key] = max(int(sample.get(key, 0)) for sample in samples)
    process_resources = {
        "wall_seconds": elapsed,
        "user_cpu_seconds": after_usage.ru_utime - before_usage.ru_utime,
        "system_cpu_seconds": after_usage.ru_stime - before_usage.ru_stime,
        "average_cpu_cores": (
            (after_usage.ru_utime - before_usage.ru_utime + after_usage.ru_stime - before_usage.ru_stime) / elapsed
            if elapsed else 0.0
        ),
        "max_rss_bytes": int(after_usage.ru_maxrss * 1024),
        "voluntary_context_switches": after_usage.ru_nvcsw - before_usage.ru_nvcsw,
        "involuntary_context_switches": after_usage.ru_nivcsw - before_usage.ru_nivcsw,
        "maximum_threads": max((int(sample.get("Threads", 0)) for sample in samples), default=0),
        "proc_io_maxima": process_io,
    }
    device_delta = delta(after_block, before_block)
    device_delta["read_bytes"] = device_delta["read_sectors"] * 512
    passed = not failures
    artifacts = {}
    for path in (stdout_path, stderr_path, events_path, samples_path):
        artifacts[path.name] = {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}
    document = {
        "schema_version": "phase12-nvme-colibri-reference-v1",
        "status": "PASS" if passed else "FAIL",
        "disposition": "accepted" if passed else "blocked",
        "scope": "same-machine Colibrì Kimi-K3 direct-source full-model reference",
        "colibri_commit": COLIBRI_COMMIT,
        "model_revision": MODEL_REVISION,
        "model_format": "publisher source checkpoint; routed experts native MXFP4",
        "binary": {"path": str(binary), "size": binary.stat().st_size, "sha256": sha256_file(binary)},
        "command": command,
        "environment": run_env,
        "cache_state": "OS_COLD_REQUESTED via sync plus drop_caches before process start",
        "runtime_ceiling_seconds": TIMEOUT_SECONDS,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout_utf8": stdout.decode("utf-8", "replace"),
        "metrics": metrics,
        "process_resources": process_resources,
        "block_device": {"stat_path": str(block_path), **device_delta},
        "raw_artifacts": artifacts,
        "failures": failures,
        "interpretation": (
            "the pinned full model completed a top-16 direct-source reference run"
            if passed else "the full-model reference did not satisfy its execution contract"
        ),
        "comparison_boundary": "actual Colibrì token inference; do not compare raw TPS directly to synthetic storage-only throughput",
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print("\n" + json.dumps({
        "status": document["status"], "returncode": returncode, "wall_seconds": elapsed,
        "metrics": metrics, "failures": failures,
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture a bounded real Kimi-K3 routing trace from the pinned Colibrì engine."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


COLIBRI_COMMIT = "b085b48888a88d9a1c00b151a9979774b72cdbfd"
MODEL_REVISION = "9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
ROUTED_LAYERS = 92
TOP_K = 16
MAX_RUNTIME_SECONDS = 7_200
DEFAULT_PROMPT = (
    "Continue this numbered sequence through item 400 without commentary or stopping: "
    "1 cache, 2 cache, 3 cache, 4 cache,"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


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


def block_delta(after: dict[str, int], before: dict[str, int]) -> dict[str, int]:
    result = {key: after[key] - before[key] for key in before if key != "in_flight"}
    result["read_bytes"] = result["read_sectors"] * 512
    return result


def parse_route_line(line: str, ordinal: int) -> dict[str, Any]:
    fields = line.split()
    if len(fields) != 3 + TOP_K:
        raise ValueError(f"route line {ordinal} does not contain exact top-{TOP_K}")
    call, row, layer = (int(value) for value in fields[:3])
    ids: list[int] = []
    gates: list[float] = []
    for value in fields[3:]:
        expert, separator, gate = value.partition(":")
        if not separator:
            raise ValueError(f"route line {ordinal} has malformed expert field")
        ids.append(int(expert))
        gates.append(float(gate))
    if len(set(ids)) != TOP_K or any(expert < 0 or expert >= 896 for expert in ids):
        raise ValueError(f"route line {ordinal} has duplicate or invalid expert ids")
    return {"call": call, "row": row, "layer": layer, "experts": ids, "gates": gates}


def parse_route_cycles(path: Path) -> list[list[dict[str, Any]]]:
    lines = [parse_route_line(line, ordinal) for ordinal, line in enumerate(path.read_text().splitlines(), 1) if line]
    if not lines:
        raise ValueError("routing trace is empty")
    cycles: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_layer = -1
    for item in lines:
        layer = int(item["layer"])
        if current and layer < previous_layer:
            cycles.append(current)
            current = []
        if current and layer == previous_layer and int(item["row"]) <= int(current[-1]["row"]):
            raise ValueError("routing rows are not strictly ordered inside a layer")
        current.append(item)
        previous_layer = layer
    cycles.append(current)

    expected_layers: list[int] | None = None
    for cycle_ordinal, cycle in enumerate(cycles):
        grouped: dict[int, list[int]] = {}
        ordered_layers: list[int] = []
        for item in cycle:
            layer = int(item["layer"])
            if layer not in grouped:
                grouped[layer] = []
                ordered_layers.append(layer)
            grouped[layer].append(int(item["row"]))
        if ordered_layers != sorted(ordered_layers) or len(ordered_layers) != ROUTED_LAYERS:
            raise ValueError(f"cycle {cycle_ordinal} does not contain the exact routed-layer sequence")
        if expected_layers is None:
            expected_layers = ordered_layers
        elif ordered_layers != expected_layers:
            raise ValueError(f"cycle {cycle_ordinal} changed routed-layer identity")
        row_counts = {len(rows) for rows in grouped.values()}
        if len(row_counts) != 1:
            raise ValueError(f"cycle {cycle_ordinal} has inconsistent batch rows")
        for rows in grouped.values():
            if rows != list(range(len(rows))):
                raise ValueError(f"cycle {cycle_ordinal} has noncanonical row ordinals")
    return cycles


def normalize_route(
    route_path: Path,
    output_path: Path,
    request_id: int,
    prompt_tokens: int,
    chunk: int,
) -> dict[str, Any]:
    cycles = parse_route_cycles(route_path)
    expected_prefill_rows: list[int] = []
    remaining = prompt_tokens
    while remaining:
        expected_prefill_rows.append(min(chunk, remaining))
        remaining -= expected_prefill_rows[-1]
    if len(cycles) < len(expected_prefill_rows):
        raise ValueError("routing trace ended before prefill completed")

    cycle_rows: list[int] = []
    for cycle in cycles:
        first_layer = int(cycle[0]["layer"])
        cycle_rows.append(sum(1 for item in cycle if int(item["layer"]) == first_layer))
    if cycle_rows[:len(expected_prefill_rows)] != expected_prefill_rows:
        raise ValueError("routing trace prefill batch geometry changed")
    if any(rows != 1 for rows in cycle_rows[len(expected_prefill_rows):]):
        raise ValueError("decode routing cycle is not one complete token forward")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    demand_count = 0
    prompt_offset = 0
    decode_token = 0
    with output_path.open("w") as output:
        output.write("request\tphase\ttoken\tlayer\trank\texpert_id\n")
        for cycle_ordinal, cycle in enumerate(cycles):
            if cycle_ordinal < len(expected_prefill_rows):
                phase = "PREFILL"
                token_base = prompt_offset
                prompt_offset += expected_prefill_rows[cycle_ordinal]
            else:
                phase = "DECODE"
                token_base = decode_token
                decode_token += 1
            # Normalize the engine's batched layer-major prefill log to the
            # contract's request/token/layer/rank order. The raw trace remains
            # archived unchanged beside this derived corpus.
            for item in sorted(cycle, key=lambda value: (int(value["row"]), int(value["layer"]))):
                token = token_base + int(item["row"])
                for rank, expert in enumerate(item["experts"]):
                    output.write(f"{request_id}\t{phase}\t{token}\t{item['layer']}\t{rank}\t{expert}\n")
                    demand_count += 1
    return {
        "cycles": len(cycles),
        "prefill_cycles": len(expected_prefill_rows),
        "complete_decode_forwards": decode_token,
        "routed_layers": sorted({int(item["layer"]) for item in cycles[0]}),
        "top_k": TOP_K,
        "normalized_demands": demand_count,
        "raw_call_values": sorted({int(item["call"]) for cycle in cycles for item in cycle}),
        "normalization": (
            "verified ascending 92-layer cycles; prefill batch rows come from prompt token count and K3_CHUNK; "
            "rows are normalized to request/token/layer/rank order; remaining one-row cycles are complete "
            "decode-token forwards"
        ),
    }


def match(pattern: str, text: str) -> re.Match[str]:
    result = re.search(pattern, text)
    if result is None:
        raise ValueError(f"missing runtime metric: {pattern}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--colibri-source", type=Path, required=True)
    parser.add_argument("--snapshot-verification", type=Path, required=True)
    parser.add_argument("--block-stat", type=Path, action="append", required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--request-id", type=int, default=0)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--ngen", type=int, default=256)
    parser.add_argument("--minimum-complete-forwards", type=int, default=256)
    parser.add_argument("--timeout-seconds", type=int, default=MAX_RUNTIME_SECONDS)
    parser.add_argument("--drop-caches", action="store_true")
    args = parser.parse_args()
    if args.ngen < 1 or args.minimum_complete_forwards < 1:
        raise ValueError("decode counts must be positive")
    if not 1 <= args.timeout_seconds <= MAX_RUNTIME_SECONDS:
        raise ValueError("runtime ceiling may not exceed the accepted 7200-second ceiling")

    binary = args.binary.resolve()
    model_dir = args.model_dir.resolve()
    source = args.colibri_source.resolve()
    snapshot_verification = json.loads(args.snapshot_verification.read_text())
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    route_path = raw / "route-trace.txt"
    normalized_path = raw / "normalized-route.tsv"
    stdout_path = raw / "stdout.bin"
    stderr_path = raw / "stderr.log"
    events_path = raw / "stderr-events.json"
    samples_path = raw / "resource-samples.json"

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    if commit != COLIBRI_COMMIT:
        raise ValueError("Colibrì revision mismatch")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=source, text=True).strip():
        raise ValueError("Colibrì source is dirty")
    if snapshot_verification["status"] != "PASS" or snapshot_verification["revision"] != MODEL_REVISION:
        raise ValueError("Kimi-K3 snapshot verification mismatch")
    if Path(snapshot_verification["snapshot"]).resolve() != model_dir:
        raise ValueError("model directory is not the verified snapshot")
    config = json.loads((model_dir / "config.json").read_text())
    configured_top_k = int(config["text_config"]["num_experts_per_token"])
    if configured_top_k != TOP_K:
        raise ValueError("model does not configure exact top-16 routing")
    if args.drop_caches:
        subprocess.run(["sudo", "-n", "sh", "-c", "sync; echo 3 > /proc/sys/vm/drop_caches"], check=True)

    env = os.environ.copy()
    for name in ("CACHE_ROUTE", "COLI_USAGE", "K3_TRACE", "K3_X0", "K3_LOGITS"):
        env.pop(name, None)
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
        "K3_MAXT": "8192",
        "K3_TOPP": "0",
        "K3_CHUNK": "32",
        "K3_THINK": "0",
        "ROUTE_TRACE": str(route_path),
    })
    run_env = {
        key: env[key]
        for key in sorted(env)
        if key.startswith("K3_") or key in ("COLI_TEMP", "ROUTE_TRACE")
    }
    command = [str(binary), str(model_dir), args.prompt, "--ngen", str(args.ngen)]
    before_block = {str(path): block_stat(path) for path in args.block_stat}
    before_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.monotonic()
    stderr_events: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    timed_out = False

    with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
        process = subprocess.Popen(command, stdout=stdout_file, stderr=subprocess.PIPE, env=env)
        assert process.stderr is not None

        def read_stderr() -> None:
            while True:
                line = process.stderr.readline()
                if not line:
                    return
                elapsed = time.monotonic() - started
                stderr_file.write(line)
                stderr_file.flush()
                stderr_events.append({"elapsed_seconds": elapsed, "line": line.decode("utf-8", "replace").rstrip("\n")})
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()

        reader = threading.Thread(target=read_stderr)
        reader.start()
        while process.poll() is None:
            elapsed = time.monotonic() - started
            samples.append({"elapsed_seconds": elapsed, **proc_values(process.pid)})
            if elapsed > args.timeout_seconds:
                timed_out = True
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            time.sleep(1)
        returncode = process.wait()
        reader.join()

    elapsed = time.monotonic() - started
    after_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    after_block = {str(path): block_stat(path) for path in args.block_stat}
    events_path.write_text(json.dumps(stderr_events, indent=2, sort_keys=True) + "\n")
    samples_path.write_text(json.dumps(samples, indent=2, sort_keys=True) + "\n")
    stderr_text = stderr_path.read_text(errors="replace")
    failures: list[str] = []
    if returncode != 0 or timed_out:
        failures.append("process failed or exceeded the accepted runtime ceiling")

    runtime: dict[str, Any] = {}
    route: dict[str, Any] = {}
    try:
        prompt_match = match(r"\[K3\] prompt: ([0-9]+) tokens \| ngen ([0-9]+) \| temp ([0-9.]+)", stderr_text)
        decode_match = match(
            r"\[K3\] decode ([0-9]+) tokens in ([0-9.]+)s \(([0-9.]+) tok/s\) \| "
            r"expert hit ([0-9.]+)% \(([0-9]+)/([0-9]+)\) \| ([0-9.]+) GB streamed",
            stderr_text,
        )
        init_match = match(
            r"\[K3\] init done in ([0-9.]+)s \| ([0-9]+) layers \| expert cache ([0-9]+)/layer .* \| RSS ([0-9.]+) GB",
            stderr_text,
        )
        prompt_tokens = int(prompt_match.group(1))
        runtime = {
            "init_seconds": float(init_match.group(1)),
            "model_layers": int(init_match.group(2)),
            "expert_cache_slots_per_layer": int(init_match.group(3)),
            "init_reported_rss_gb": float(init_match.group(4)),
            "prompt_tokens": prompt_tokens,
            "requested_decode_tokens": int(prompt_match.group(2)),
            "temperature": float(prompt_match.group(3)),
            "prefill_seconds": float(match(r"\[K3\] prefill done in ([0-9.]+)s", stderr_text).group(1)),
            "generated_tokens": int(decode_match.group(1)),
            "decode_seconds": float(decode_match.group(2)),
            "actual_decode_tokens_per_second": float(decode_match.group(3)),
            "colibri_expert_hit_percent": float(decode_match.group(4)),
            "colibri_expert_hits": int(decode_match.group(5)),
            "colibri_expert_accesses": int(decode_match.group(6)),
            "colibri_expert_streamed_gb_decimal": float(decode_match.group(7)),
        }
        route = normalize_route(route_path, normalized_path, args.request_id, prompt_tokens, int(env["K3_CHUNK"]))
        if int(init_match.group(2)) != 93 or configured_top_k != TOP_K or env["K3_TOPP"] != "0":
            failures.append("model topology or exact full-top-16 routing changed")
        if "[K3] TOPP=" in stderr_text or "context full" in stderr_text:
            failures.append("routing was pruned or the context ceiling interrupted the capture")
        if route["complete_decode_forwards"] < args.minimum_complete_forwards:
            failures.append(
                f"only {route['complete_decode_forwards']} complete decode forwards were captured; "
                f"minimum is {args.minimum_complete_forwards}"
            )
    except (KeyError, ValueError) as error:
        failures.append(str(error))

    process_resources = {
        "wall_seconds": elapsed,
        "user_cpu_seconds": after_usage.ru_utime - before_usage.ru_utime,
        "system_cpu_seconds": after_usage.ru_stime - before_usage.ru_stime,
        "average_cpu_cores": (
            (after_usage.ru_utime - before_usage.ru_utime + after_usage.ru_stime - before_usage.ru_stime) / elapsed
            if elapsed else 0.0
        ),
        "maximum_rss_bytes": max((int(sample.get("VmHWM", 0)) for sample in samples), default=0),
        "maximum_threads": max((int(sample.get("Threads", 0)) for sample in samples), default=0),
        "proc_io_maxima": {
            key: max((int(sample.get(key, 0)) for sample in samples), default=0)
            for key in ("io_read_bytes", "io_write_bytes", "io_rchar", "io_wchar")
        },
        "resource_sample_interval_seconds": 1,
        "resource_sample_count": len(samples),
    }
    devices = [
        {"stat_path": name, **block_delta(after_block[name], before_block[name])}
        for name in sorted(before_block)
    ]
    artifacts = {
        path.name: identity(path)
        for path in (stdout_path, stderr_path, events_path, samples_path, route_path, normalized_path)
        if path.is_file()
    }
    passed = not failures
    document = {
        "schema_version": "phase12-nvme-real-routing-capture-v1",
        "status": "PASS" if passed else "FAIL",
        "disposition": "accepted" if passed else "inconclusive",
        "scope": "CPU-only real Kimi-K3 full-top-16 routing capture for offline project-cache replay",
        "request_id": args.request_id,
        "prompt": args.prompt,
        "prompt_utf8_sha256": hashlib.sha256(args.prompt.encode()).hexdigest(),
        "colibri_commit": COLIBRI_COMMIT,
        "model_revision": MODEL_REVISION,
        "snapshot_verification": identity(args.snapshot_verification.resolve()),
        "binary": identity(binary),
        "command": command,
        "environment": run_env,
        "cache_state": "OS_COLD_REQUESTED via sync plus drop_caches before process start" if args.drop_caches else "UNCHANGED",
        "runtime_ceiling_seconds": args.timeout_seconds,
        "minimum_complete_decode_forwards": args.minimum_complete_forwards,
        "returncode": returncode,
        "timed_out": timed_out,
        "runtime": runtime,
        "routing_trace": route,
        "process_resources": process_resources,
        "block_devices": devices,
        "raw_artifacts": artifacts,
        "instrumentation_boundary": (
            "the pinned engine's existing ROUTE_TRACE fprintf path observes selected ids and gates after exact top-k; "
            "it does not alter routing, selection, arithmetic, cache behavior, or production defaults"
        ),
        "failures": failures,
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"],
        "wall_seconds": elapsed,
        "complete_decode_forwards": route.get("complete_decode_forwards"),
        "routing_sha256": artifacts.get("normalized-route.tsv", {}).get("sha256"),
        "failures": failures,
    }, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

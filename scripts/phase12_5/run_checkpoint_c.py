#!/usr/bin/env python3
"""Run one adjacent untraced/traced Phase 12.5 Checkpoint C case."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from common import ROOT, file_identity, write_json


MODEL = Path("/workspace/models/DeepSeek-V4-Flash-85ce4196-UD-Q3_K_XL/DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf")
PROMPT = "Explain why a careful measurement should distinguish observed facts from assumptions."
RENDERED_PROMPT = "<｜begin▁of▁sentence｜><｜User｜>" + PROMPT + "<｜Assistant｜><think>"
EXPECTED_PROVIDER_IDS = [
    2581, 1309, 304, 8470, 3939, 16372, 11226, 1531, 23656, 7199, 9616, 538,
    22283, 16, 1162, 344, 260, 11264, 12047, 295, 9356, 22499, 16, 455,
]
PROVIDER_CASES = {
    "provider-positional-selected": {"cold_bytes": 17179869184, "transport": "POSITIONAL", "trace_capacity": 65536},
    "provider-buffered-io-uring": {"cold_bytes": 17179869184, "transport": "BUFFERED", "trace_capacity": 0},
    "provider-cold64-positional": {"cold_bytes": 68719476736, "transport": "POSITIONAL", "trace_capacity": 0},
}
EVIDENCE_ENVIRONMENT = {"LLAMA_PERFETTO_EVIDENCE_IDENTITY": "1"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires values")
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999))
    return ordered[index]


def cgroup_events() -> dict[str, int]:
    cgroup = Path("/sys/fs/cgroup")
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            cgroup /= line.split("::", 1)[1].lstrip("/")
            break
    return {key: int(value) for key, value in
        (line.split() for line in (cgroup / "memory.events").read_text().splitlines())}


def meminfo() -> dict[str, int]:
    result = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        result[key] = int(value.strip().split()[0]) * 1024
    return result


def gpu() -> dict[str, int]:
    command = ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total",
        "--format=csv,noheader,nounits"]
    completed = subprocess.run(command, text=True, capture_output=True, check=True)
    values = [int(value.strip()) for value in completed.stdout.splitlines()[0].split(",")]
    return {"used_mib": values[0], "free_mib": values[1], "total_mib": values[2]}


def resource_snapshot() -> dict[str, Any]:
    memory = meminfo()
    return {"monotonic_ns": time.monotonic_ns(), "mem_available_bytes": memory["MemAvailable"],
        "swap_free_bytes": memory["SwapFree"], "cgroup_memory_events": cgroup_events(), "gpu": gpu()}


def revision(path: Path) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, text=True,
        capture_output=True, check=True).stdout.strip()


def provider_command(binary: Path, output: Path, definition: dict[str, Any]) -> list[str]:
    command = [str(binary), "--model", str(MODEL), "--output", str(output), "--mode", "cold",
        "--prompt", RENDERED_PROMPT, "--hot-policy", "LRU", "--cold-policy", "LRU", "--scope", "GLOBAL",
        "--admission", "ALWAYS", "--miss-policy", "PROMOTE_AND_GPU", "--hot-slots", "268",
        "--cold-bytes", str(definition["cold_bytes"]), "--ring-bytes", "67173120",
        "--queue-depth", "0"]
    if definition["trace_capacity"]:
        command += ["--trace-capacity", str(definition["trace_capacity"])]
    return command + ["--n-ctx", "4096", "--n-batch", "128", "--n-ubatch", "128",
        "--max-generate", "24", "--background", "0", "--observe-routes", "1",
        "--transport", definition["transport"], "--config-source", "NULL"]


def control_command(binary: Path, cpu_moe: bool) -> list[str]:
    command = [str(binary), "-m", str(MODEL), "-c", "4096", "-n", "24", "-b", "128", "-ub", "128",
        "-t", "19", "-tb", "19", "-p", PROMPT, "--temp", "0", "--seed", "1", "--no-warmup",
        "--fit", "on"]
    if cpu_moe:
        command.append("--cpu-moe")
    return command + ["--perf", "--simple-io", "--no-display-prompt", "-st"]


def run_untraced(command: list[str], stdout: Path, stderr: Path, timeout: int,
        extra_env: dict[str, str] | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("LLAMA_PERFETTO_CAPTURE", None)
    env.pop("LLAMA_PERFETTO_EVIDENCE_IDENTITY", None)
    env.update(extra_env or {})
    before = resource_snapshot()
    started = time.monotonic_ns()
    with stdout.open("xb") as out, stderr.open("xb") as err:
        completed = subprocess.run(command, stdout=out, stderr=err, env=env, timeout=timeout, check=False)
        out.flush(); os.fsync(out.fileno())
        err.flush(); os.fsync(err.fileno())
    after = resource_snapshot()
    return {"command": command, "returncode": completed.returncode, "started_monotonic_ns": started,
        "completed_monotonic_ns": time.monotonic_ns(), "before": before, "after": after,
        "stdout": file_identity(stdout), "stderr": file_identity(stderr)}


def provider_summary(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    latencies = value["latency_us"]
    decode = latencies[1:]
    selected = {key: value.get(key) for key in
        ("prompt_ids", "generated_ids", "generated_text", "logits_fnv64", "routes")}
    identity = hashlib.sha256(json.dumps(selected, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode()).hexdigest()
    diagnostics = value["async_io"]["diagnostics"]
    if diagnostics["positional_reads_forced"] and not diagnostics["io_uring_enabled"]:
        transport = "POSITIONAL"
    elif diagnostics["io_uring_enabled"] and diagnostics["direct_read_operations"]:
        transport = "DIRECT_IO"
    elif diagnostics["io_uring_enabled"]:
        transport = "BUFFERED"
    else:
        transport = "UNRESOLVED"
    return {"file": file_identity(path), "status": value.get("status"), "identity_sha256": identity,
        "generated_ids": value["generated_ids"], "generated_text_sha256": sha256_text(value["generated_text"]),
        "latency_us": latencies, "decode": {"samples": len(decode),
            "throughput_tps": len(decode) * 1_000_000 / sum(decode),
            "p50_us": percentile(decode, .50), "p95_us": percentile(decode, .95),
            "p99_us": percentile(decode, .99), "maximum_us": max(decode)},
        "transport_requested": value["transport_requested"], "transport_actual": transport,
        "capacities": value["capacities"], "async_io": diagnostics, "mechanism": value["mechanism"],
        "storage": value["storage"], "lifecycle": value["lifecycle"],
        "cpu_user_time_us": value["cpu_user_time_us"], "cpu_system_time_us": value["cpu_system_time_us"],
        "peak_rss_kib": value["peak_rss_kib"], "route_records": len(value["routes"])}


def control_summary(stdout: Path, stderr: Path) -> dict[str, Any]:
    value = stdout.read_text(errors="replace")
    generated = re.search(r"\[Start thinking\]\s*(.*?)\s*\[ Prompt:", value, re.DOTALL)
    perf = re.search(r"\[ Prompt:\s*([0-9.]+) t/s \| Generation:\s*([0-9.]+) t/s \]", value)
    if not generated or not perf:
        raise ValueError("could not extract control generation/performance")
    markers = re.findall(r"^LLAMA_PERFETTO_EVIDENCE_IDENTITY (\{.*\})$",
        stderr.read_text(errors="replace"), re.MULTILINE)
    if len(markers) != 1:
        raise ValueError(f"expected one control identity marker, found {len(markers)}")
    identity = json.loads(markers[0])
    generated_ids = identity.get("generated_ids")
    logits_fnv64 = identity.get("logits_fnv64")
    nonfinite_logits = identity.get("nonfinite_logits")
    if not isinstance(generated_ids, list) or not isinstance(logits_fnv64, list) or \
            len(generated_ids) != 24 or len(logits_fnv64) != len(generated_ids) or \
            not isinstance(nonfinite_logits, int):
        raise ValueError("control identity marker is incomplete")
    text = generated.group(1).strip()
    return {"stdout": file_identity(stdout), "generated_text": text, "generated_text_sha256": sha256_text(text),
        "generated_ids": generated_ids, "logits_fnv64": logits_fnv64,
        "nonfinite_logits": nonfinite_logits,
        "prompt_tps_displayed": float(perf.group(1)), "generation_tps_displayed": float(perf.group(2))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=tuple(PROVIDER_CASES) + ("fit-control", "cpu-moe-control"), required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--llama-cli", type=Path, required=True)
    parser.add_argument("--perfetto", type=Path, required=True)
    parser.add_argument("--trace-processor", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()

    name = f"{args.case}-r{args.repeat}"
    output = args.output_root / name
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    is_provider = args.case in PROVIDER_CASES
    untraced_workload = output / "untraced-workload.json"
    traced_workload = output / "workload.json"
    if is_provider:
        command_untraced = provider_command(args.probe, untraced_workload, PROVIDER_CASES[args.case])
        command_traced = provider_command(args.probe, traced_workload, PROVIDER_CASES[args.case])
        case_metadata = {"name": args.case, "repeat": args.repeat, **PROVIDER_CASES[args.case],
            "hot_slots": 268, "ring_bytes": 67173120, "queue_depth": 0, "max_generate": 24}
    else:
        command_untraced = control_command(args.llama_cli, args.case == "cpu-moe-control")
        command_traced = list(command_untraced)
        case_metadata = {"name": args.case, "repeat": args.repeat, "max_generate": 24,
            "fit": True, "cpu_moe": args.case == "cpu-moe-control"}
    case_metadata["project_revision"] = revision(ROOT)
    case_metadata["nested_revision"] = revision(ROOT / "llama.cpp")
    case_metadata["model_revision"] = "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1"

    evidence_environment = {} if is_provider else EVIDENCE_ENVIRONMENT
    untraced = run_untraced(command_untraced, output / "untraced.stdout", output / "untraced.stderr",
        args.timeout_seconds, evidence_environment)
    if untraced["returncode"] != 0:
        raise RuntimeError(f"untraced workload exited {untraced['returncode']}")
    write_json(output / "untraced-run.json", untraced)
    write_json(output / "command.json", {"command": command_traced,
        "environment": evidence_environment})
    write_json(output / "case.json", case_metadata)

    capture_command = [sys.executable, str(ROOT / "scripts/phase12_5/capture_perfetto.py"),
        "--perfetto", str(args.perfetto), "--trace-processor", str(args.trace_processor),
        "--config", str(args.config), "--trace", str(output / "trace.pftrace"),
        "--command-json", str(output / "command.json"), "--metadata", str(output / "capture.json"),
        "--stdout", str(output / "traced.stdout"), "--stderr", str(output / "traced.stderr"),
        "--perfetto-log", str(output / "perfetto.log"), "--case-metadata-json", str(output / "case.json"),
        "--timeout-seconds", str(args.timeout_seconds)]
    before_capture = resource_snapshot()
    subprocess.run(capture_command, check=True)
    after_capture = resource_snapshot()
    verify_command = [sys.executable, str(ROOT / "scripts/phase12_5/verify_perfetto.py"),
        "--trace-processor", str(args.trace_processor), "--trace", str(output / "trace.pftrace"),
        "--capture-metadata", str(output / "capture.json"), "--profile", "provider" if is_provider else "tiny",
        "--output", str(output / "verification.json")]
    subprocess.run(verify_command, check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts/phase12_5/analyze_perfetto.py"),
        "--trace-processor", str(args.trace_processor), "--trace", str(output / "trace.pftrace"),
        "--verification", str(output / "verification.json"), "--case-name", name,
        "--output", str(output / "query-output.json")], check=True)

    if is_provider:
        first = provider_summary(untraced_workload)
        second = provider_summary(traced_workload)
        exact = first["identity_sha256"] == second["identity_sha256"]
        expected = second["generated_ids"] == EXPECTED_PROVIDER_IDS
        throughput_shift = second["decode"]["throughput_tps"] / first["decode"]["throughput_tps"] - 1
        p95_shift = second["decode"]["p95_us"] / first["decode"]["p95_us"] - 1
        transport_exact = second["transport_actual"] == PROVIDER_CASES[args.case]["transport"]
        io_uring_exact = args.case != "provider-buffered-io-uring" or (
            second["async_io"]["io_uring_enabled"] and
            second["async_io"]["synchronous_fallback_operations"] == 0)
        comparison = {"kind": "provider", "untraced": first, "traced": second,
            "exact_identity_match": exact, "expected_generated_ids": expected,
            "transport_exact": transport_exact, "native_io_uring_no_fallback": io_uring_exact,
            "throughput_shift_fraction": throughput_shift, "p95_shift_fraction": p95_shift,
            "selected_perturbation_gate": args.case != "provider-positional-selected" or (
                throughput_shift >= -.15 and p95_shift <= .20)}
    else:
        first = control_summary(output / "untraced.stdout", output / "untraced.stderr")
        second = control_summary(output / "traced.stdout", output / "traced.stderr")
        exact_text = first["generated_text_sha256"] == second["generated_text_sha256"]
        exact_ids = first["generated_ids"] == second["generated_ids"]
        exact_logits = first["logits_fnv64"] == second["logits_fnv64"]
        expected_ids = second["generated_ids"] == EXPECTED_PROVIDER_IDS
        finite_logits = first["nonfinite_logits"] == 0 and second["nonfinite_logits"] == 0
        comparison = {"kind": "control", "untraced": first, "traced": second,
            "exact_text_match": exact_text, "exact_generated_ids_match": exact_ids,
            "exact_logits_identity_match": exact_logits, "expected_generated_ids": expected_ids,
            "finite_logits": finite_logits,
            "exact_identity_match": exact_text and exact_ids and exact_logits and expected_ids and finite_logits}
    event_delta = {key: after_capture["cgroup_memory_events"].get(key, 0) -
        before_capture["cgroup_memory_events"].get(key, 0) for key in
        sorted(set(before_capture["cgroup_memory_events"]) | set(after_capture["cgroup_memory_events"]))}
    comparison["capture_resources"] = {"before": before_capture, "after": after_capture,
        "cgroup_memory_event_delta": event_delta}
    comparison["status"] = "pass" if (comparison["exact_identity_match"] and
        all(event_delta.get(key, 0) == 0 for key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"))
        and (not is_provider or all((expected, transport_exact, io_uring_exact,
            comparison["selected_perturbation_gate"])))) else "fail"
    write_json(output / "comparison.json", comparison)
    print(json.dumps({"status": comparison["status"], "case": name,
        "trace": file_identity(output / "trace.pftrace")}, sort_keys=True))
    return 0 if comparison["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

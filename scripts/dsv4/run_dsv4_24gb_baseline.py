#!/usr/bin/env python3
"""Run one interleaved conventional DeepSeek-V4 confirmation baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import resource
import subprocess
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_dsv4_24gb_resume import (  # noqa: E402
    MIN_DISK_AVAILABLE,
    MIN_MEM_AVAILABLE,
    MODEL,
    cgroup_memory_events,
    disk_available,
    gpu_sample,
    identity,
    meminfo,
    smaps_rollup,
    write_json,
)


CLI = Path("/workspace/builds/k3-issue49-cuda/bin/llama-cli")
USER_PROMPT = "Explain why a careful measurement should distinguish observed facts from assumptions."


def parse_performance(output: str, max_generate: int) -> dict[str, float | int | str | None]:
    result: dict[str, float | int | None] = {
        "prompt_tokens": None, "prompt_tps": None, "generated_tokens": None, "generation_tps": None,
    }
    prompt = re.search(r"prompt eval time\s*=.*?/\s*(\d+) tokens .*?([0-9.]+) tokens per second", output)
    generation = re.search(r"eval time\s*=.*?/\s*(\d+) runs.*?([0-9.]+) tokens per second", output)
    if prompt:
        result["prompt_tokens"] = int(prompt.group(1))
        result["prompt_tps"] = float(prompt.group(2))
    if generation:
        result["generated_tokens"] = int(generation.group(1))
        result["generation_tps"] = float(generation.group(2))
    summary = re.search(r"\[ Prompt:\s*([0-9.]+) t/s \| Generation:\s*([0-9.]+) t/s \]", output)
    if summary:
        result["prompt_tps"] = float(summary.group(1))
        result["generation_tps"] = float(summary.group(2))
        result["generated_tokens"] = max_generate
    if "[Start thinking]" in output and "[ Prompt:" in output:
        generated = output.split("[Start thinking]", 1)[1].split("[ Prompt:", 1)[0].strip()
        result["generated_text"] = generated
        result["generated_text_sha256"] = hashlib.sha256(generated.encode()).hexdigest()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--placement", choices=("fit", "cpu_moe"), required=True)
    parser.add_argument("--max-generate", type=int, default=8)
    parser.add_argument("--sample-interval", type=float, default=0.5)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = args.output_dir / f"{args.name}.stdout.txt"
    stderr_path = args.output_dir / f"{args.name}.stderr.txt"
    record_path = args.output_dir / f"{args.name}.record.json"
    command = [
        str(CLI), "-m", str(MODEL), "-c", "4096", "-n", str(args.max_generate), "-b", "128", "-ub", "128",
        "-t", "19", "-tb", "19", "-p", USER_PROMPT, "--temp", "0", "--seed", "1", "--no-warmup",
        "--fit", "on",
    ]
    if args.placement == "cpu_moe":
        command.append("--cpu-moe")
    command.extend(["--perf", "--simple-io", "--no-display-prompt", "-st"])

    before_memory = meminfo()
    before_disk = disk_available(args.output_dir)
    cgroup_path, cgroup_before = cgroup_memory_events()
    usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    if before_memory["MemAvailable"] < MIN_MEM_AVAILABLE or before_disk < MIN_DISK_AVAILABLE:
        record = {"schema_version": "dsv4-24gb-resume-baseline-v1", "name": args.name,
                  "status": "preflight_rejected", "placement": args.placement,
                  "mem_available_bytes": before_memory["MemAvailable"], "disk_available_bytes": before_disk}
        write_json(record_path, record)
        print(json.dumps(record, sort_keys=True))
        return 1

    samples = []
    breaches: list[str] = []
    started_ns = time.monotonic_ns()
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        while process.poll() is None:
            memory = meminfo()
            sample = {
                "elapsed_ms": (time.monotonic_ns() - started_ns) // 1_000_000,
                "mem_available_bytes": memory["MemAvailable"], "disk_available_bytes": disk_available(args.output_dir),
                "gpu": gpu_sample(), "smaps": smaps_rollup(process.pid),
            }
            samples.append(sample)
            if sample["mem_available_bytes"] < MIN_MEM_AVAILABLE:
                breaches.append("MemAvailable below 16 GiB")
            if sample["disk_available_bytes"] < MIN_DISK_AVAILABLE:
                breaches.append("filesystem availability below 55 GiB")
            if sample["smaps"].get("Swap", 0) != 0:
                breaches.append("process swap became nonzero")
            if breaches:
                process.terminate()
                break
            time.sleep(args.sample_interval)
        exit_code = process.wait()
    ended_ns = time.monotonic_ns()
    usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    after_memory = meminfo()
    after_disk = disk_available(args.output_dir)
    after_cgroup_path, cgroup_after = cgroup_memory_events()
    event_delta = {key: cgroup_after.get(key, 0) - cgroup_before.get(key, 0)
                   for key in sorted(set(cgroup_before) | set(cgroup_after))}
    smaps_samples = [sample["smaps"] for sample in samples if sample["smaps"]]
    peak_pss = max(smaps_samples, key=lambda item: item.get("Pss", 0), default={})
    gpu_samples = [sample["gpu"] for sample in samples if sample["gpu"]]
    stdout_text = stdout_path.read_text(errors="replace")
    stderr_text = stderr_path.read_text(errors="replace")
    performance = parse_performance(stdout_text + "\n" + stderr_text, args.max_generate)
    gates = {
        "exit_zero": exit_code == 0, "watchdog_not_breached": not breaches,
        "mem_available": min((item["mem_available_bytes"] for item in samples), default=after_memory["MemAvailable"]) >= MIN_MEM_AVAILABLE,
        "disk_reserve": min((item["disk_available_bytes"] for item in samples), default=after_disk) >= MIN_DISK_AVAILABLE,
        "swap_zero": all(item.get("Swap", 0) == 0 for item in smaps_samples),
        "cgroup_pressure_zero": all(event_delta.get(key, 0) == 0 for key in
                                    ("low", "high", "max", "oom", "oom_kill", "oom_group_kill")),
        "performance_present": performance["generation_tps"] is not None,
        "output_present": stdout_path.stat().st_size > 0,
    }
    record = {
        "schema_version": "dsv4-24gb-resume-baseline-v1", "name": args.name,
        "status": "pass" if all(gates.values()) else "fail", "placement": args.placement,
        "command": command, "exit_code": exit_code, "watchdog_breaches": sorted(set(breaches)),
        "wall_time_us": (ended_ns - started_ns) // 1000, "performance": performance,
        "resource_usage": {
            "cpu_user_time_us": int((usage_after.ru_utime - usage_before.ru_utime) * 1_000_000),
            "cpu_system_time_us": int((usage_after.ru_stime - usage_before.ru_stime) * 1_000_000),
            "peak_rss_kib": usage_after.ru_maxrss, "major_faults": usage_after.ru_majflt - usage_before.ru_majflt,
            "minor_faults": usage_after.ru_minflt - usage_before.ru_minflt,
            "input_blocks": usage_after.ru_inblock - usage_before.ru_inblock,
            "minimum_mem_available_bytes": min((item["mem_available_bytes"] for item in samples), default=after_memory["MemAvailable"]),
            "minimum_disk_available_bytes": min((item["disk_available_bytes"] for item in samples), default=after_disk),
            "minimum_gpu_free_mib": min((item["free_mib"] for item in gpu_samples), default=None),
            "peak_gpu_used_mib": max((item["used_mib"] for item in gpu_samples), default=None),
            "peak_pss_bytes": peak_pss.get("Pss"), "peak_pss_anon_bytes": peak_pss.get("Pss_Anon"),
            "peak_pss_file_bytes_at_peak_pss": peak_pss.get("Pss_File"),
            "cgroup_path": cgroup_path if cgroup_path == after_cgroup_path else [cgroup_path, after_cgroup_path],
            "cgroup_memory_event_delta": event_delta, "sample_count": len(samples),
        },
        "stdout": identity(stdout_path), "stderr": identity(stderr_path), "gates": gates,
    }
    write_json(record_path, record)
    print(json.dumps({"status": record["status"], "record": str(record_path), "gates": gates}, sort_keys=True))
    return 0 if record["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

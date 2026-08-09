#!/usr/bin/env python3
"""Render compact issue 69 perf profiles and quantify CPU attribution buckets."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess

from common import file_identity, write_json


HEADER = re.compile(r"^\s*(?P<comm>.+?)\s+(?P<pid>\d+)/(?:\s*)?(?P<tid>\d+)\s+")
STORAGE_WORKER = ("worker_main", "pread64", "pread")
BUCKETS = (
    ("transfer_ring_stage_memcpy", ("transfer_ring::stage", "llm_expert_transfer_ring::stage", "memcpy", "memmove")),
    ("file_read_page_cache", ("pread", "read_bundle", "vfs_read", "filemap", "copy_page_to_iter", "do_iter_read")),
    ("cold_cache_policy_victim", ("cold_expert_cache", "k3.cache.cold", "cold_policy")),
    ("hot_cache_policy_victim", ("hot_cache", "k3.cache.hot", "hot_policy", "select_hot")),
    ("scheduler_async_bookkeeping", ("expert_scheduler", "async_transport", "wait_any_read", "submit_read_plan")),
    ("futex_scheduler_wait", ("futex", "__schedule", "schedule_timeout", "pthread_cond")),
    ("other_provider_cpu", ("expert_weight_provider", "expert-transfer-ring", "expert_storage")),
)


def run_to_file(command: list[str], output: Path, input_path: Path | None = None) -> None:
    with output.open("wb") as destination:
        with input_path.open("rb") if input_path else open("/dev/null", "rb") as source:
            completed = subprocess.run(
                command, stdin=source if input_path else None, stdout=destination,
                stderr=subprocess.PIPE, check=False,
            )
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stderr.decode()}")


def sample_blocks(path: Path) -> list[str]:
    return [block + "\n\n" for block in path.read_text(errors="replace").split("\n\n") if block.strip()]


def select_blocks(blocks: list[str], pid: int, selector: str) -> list[str]:
    selected: list[str] = []
    for block in blocks:
        first = block.splitlines()[0]
        match = HEADER.match(first)
        if not match or int(match.group("pid")) != pid:
            continue
        tid = int(match.group("tid"))
        if (selector == "process" or
                selector == "main" and tid == pid or
                selector == "storage" and any(pattern in block for pattern in STORAGE_WORKER)):
            selected.append(block)
    return selected


def read_folded(path: Path) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for line in path.read_text().splitlines():
        stack, separator, count = line.rpartition(" ")
        if not separator:
            raise ValueError(f"invalid folded stack in {path}")
        result.append((stack, int(count)))
    return result


def attribution(path: Path) -> dict[str, object]:
    folded = read_folded(path)
    total = sum(count for _, count in folded)
    buckets = Counter()
    leaves = Counter()
    inclusive = Counter()
    for stack, count in folded:
        lowered = stack.lower()
        bucket = "other"
        for name, patterns in BUCKETS:
            if any(pattern in lowered for pattern in patterns):
                bucket = name
                break
        buckets[bucket] += count
        frames = stack.split(";")
        leaves[frames[-1]] += count
        for frame in set(frames):
            inclusive[frame] += count
    return {
        "samples": total,
        "buckets": {
            name: {"samples": buckets[name], "fraction": buckets[name] / total if total else 0.0}
            for name in [item[0] for item in BUCKETS] + ["other"]
        },
        "top_leaf_functions": [
            {"function": name, "samples": count, "fraction": count / total if total else 0.0}
            for name, count in leaves.most_common(20)
        ],
        "top_inclusive_functions": [
            {"function": name, "samples": count, "fraction": count / total if total else 0.0}
            for name, count in inclusive.most_common(30)
        ],
    }


def parse_stat(path: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        result[row["event"]] = {
            "value": float(row["counter-value"]), "unit": row["unit"],
            "event_runtime": row["event-runtime"], "percent_running": row["pcnt-running"],
        }
    return result


def render_one(args: argparse.Namespace, cell: str) -> None:
    raw = args.raw_dir / cell
    output = args.output_dir / cell.lower()
    output.mkdir(parents=True)
    capture = json.loads((raw / "capture.json").read_text())
    pid = int(capture["probe_pid"])
    script = raw / "perf.script"
    run_to_file([
        str(args.perf), "script", "--input", str(raw / "perf.data"), "--demangle",
        "--fields", "comm,pid,tid,time,event,ip,sym,dso",
    ], script)
    blocks = sample_blocks(script)
    artifacts: dict[str, dict[str, object]] = {}
    for selector in ("process", "main", "storage"):
        selected = raw / f"{selector}.perf"
        selected.write_text("".join(select_blocks(blocks, pid, selector)))
        folded = output / f"{selector}.folded"
        run_to_file([str(args.flamegraph / "stackcollapse-perf.pl"), "--tid", str(selected)], folded)
        svg = output / f"{selector}.svg"
        run_to_file([
            str(args.flamegraph / "flamegraph.pl"),
            "--title", f"Issue 69 {cell} {selector} on-CPU", "--countname", "samples", str(folded),
        ], svg)
        artifacts[selector] = {
            "folded": file_identity(folded), "svg": file_identity(svg),
            "attribution": attribution(folded),
        }
    write_json(output / "summary.json", {
        "schema_version": "issue69-perf-attribution-v1", "status": "pass", "cell": cell,
        "selection": {
            "process_pid": pid, "main_tid": pid,
            "storage_worker_rule": "sample stack contains worker_main or pread",
            "storage_worker_reliable":
                artifacts["storage"]["attribution"]["samples"] > 0 and
                artifacts["storage"]["attribution"]["buckets"]["file_read_page_cache"]["fraction"] > 0.5,
        },
        "capture": file_identity(raw / "capture.json"),
        "workload": file_identity(raw / "workload.json"),
        "build": capture["build"],
        "perf_data": file_identity(raw / "perf.data"),
        "perf_stat": parse_stat(raw / "perf-stat.jsonl"),
        "flamegraph_revision": subprocess.check_output(
            ["git", "-C", str(args.flamegraph), "rev-parse", "HEAD"], text=True).strip(),
        "artifacts": artifacts,
    })


def differential(args: argparse.Namespace) -> None:
    left = args.output_dir / "s0" / "process.folded"
    right = args.output_dir / "a1" / "process.folded"
    if not left.is_file() or not right.is_file():
        return
    folded = args.output_dir / "s0-to-a1-differential.folded"
    run_to_file([str(args.flamegraph / "difffolded.pl"), "-n", str(left), str(right)], folded)
    svg = args.output_dir / "s0-to-a1-differential.svg"
    run_to_file([
        str(args.flamegraph / "flamegraph.pl"), "--negate",
        "--title", "Issue 69 S0 to A1 normalized on-CPU differential", str(folded),
    ], svg)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cells", default="S0,S1,A1")
    parser.add_argument("--perf", type=Path, default=Path("/usr/bin/perf"))
    parser.add_argument("--flamegraph", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)
    for required in (args.perf, args.flamegraph / "stackcollapse-perf.pl", args.flamegraph / "flamegraph.pl"):
        if not required.is_file():
            raise FileNotFoundError(required)
    args.output_dir.mkdir(parents=True)
    for cell in [item.strip() for item in args.cells.split(",") if item.strip()]:
        render_one(args, cell)
    differential(args)


if __name__ == "__main__":
    main()

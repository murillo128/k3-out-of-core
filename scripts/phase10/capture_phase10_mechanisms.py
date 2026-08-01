#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path
from typing import Any

from prefetch_common import Phase10Error, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_record(stdout: bytes, prefix: str) -> dict[str, int]:
    matches = [line for line in stdout.decode("utf-8").splitlines() if line.startswith(prefix + "\t")]
    if len(matches) != 1:
        raise Phase10Error(f"expected exactly one {prefix} record")
    result: dict[str, int] = {}
    for field in matches[0].split("\t")[1:]:
        key, separator, value = field.partition("=")
        if not separator or not key or not value.isdecimal() or key in result:
            raise Phase10Error(f"invalid {prefix} field: {field}")
        result[key] = int(value)
    return result


def run_twice(binary: Path, prefixes: tuple[str, ...]) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    runs: list[dict[str, Any]] = []
    records: dict[str, dict[str, int]] | None = None
    for _ in range(2):
        command = [str(binary)]
        completed = subprocess.run(command, check=False, capture_output=True)
        current = {prefix: parse_record(completed.stdout, prefix) for prefix in prefixes} \
            if completed.returncode == 0 else {}
        runs.append({
            "command": command,
            "exit_code": completed.returncode,
            "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        })
        if completed.returncode != 0:
            raise Phase10Error(f"mechanism test failed: {binary}")
        if records is not None and current != records:
            raise Phase10Error(f"mechanism summary was not byte-stable: {binary}")
        records = current
    if runs[0]["stdout_sha256"] != runs[1]["stdout_sha256"] or records is None:
        raise Phase10Error(f"mechanism stdout was not byte-identical: {binary}")
    return runs, records


def require_sha(name: str, value: str) -> None:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise Phase10Error(f"{name} must be a lowercase commit SHA")


def capture(args: argparse.Namespace) -> None:
    require_sha("project_head", args.project_head)
    require_sha("nested_head", args.nested_head)
    hot = Path(args.hot_test).resolve()
    scheduler = Path(args.scheduler_test).resolve()
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    hot_runs, hot_records = run_twice(hot, ("PHASE10_STATIC_SEED", "PHASE10_EXACT_ISSUE_AHEAD"))
    scheduler_runs, scheduler_records = run_twice(scheduler, ("PHASE10_SCHEDULER_LIFECYCLE",))
    issue = hot_records["PHASE10_EXACT_ISSUE_AHEAD"]
    seed = hot_records["PHASE10_STATIC_SEED"]
    lifecycle = scheduler_records["PHASE10_SCHEDULER_LIFECYCLE"]
    common = {
        "project_head": args.project_head,
        "nested_head": args.nested_head,
    }
    write_json(output / "exact-issue-ahead.json", {
        "schema_version": "phase10-exact-issue-ahead-v1",
        **common,
        "binary_sha256": sha256_file(hot),
        "runs": hot_runs,
        "parallel": {
            "demand_misses": issue.get("misses"),
            "scheduler_enqueued_before_first_take": issue.get("enqueued_before_take"),
            "storage_reads_submitted_before_first_wait": issue.get("parallel_submitted_before_wait"),
            "demand_ready_before_use": issue.get("parallel_ready_before_use"),
        },
        "serial_control": {
            "storage_reads_submitted_before_first_wait": issue.get("serial_submitted_before_wait"),
        },
        "route_identity_preserved": issue.get("routes_equal") == 1,
        "all_demand_enqueued_before_take": issue.get("enqueued_before_take") == issue.get("misses"),
        "all_storage_submitted_before_wait": issue.get("parallel_submitted_before_wait") == issue.get("misses"),
        "all_demand_ready_before_use": issue.get("parallel_ready_before_use") == issue.get("misses"),
        "serial_control_distinguishes_issue_ahead": issue.get("serial_submitted_before_wait", 0) < issue.get("misses", 0),
    })
    write_json(output / "static-seeding.json", {
        "schema_version": "phase10-static-seeding-v1",
        **common,
        "binary_sha256": sha256_file(hot),
        "runs": hot_runs,
        "seed_entries": seed.get("entries"),
        "storage_bytes": seed.get("storage_bytes"),
        "h2d_bytes": seed.get("h2d_bytes"),
        "ordinary_lru_after_startup": seed.get("ordinary_lru") == 1,
        "partial_failure_rolled_back": seed.get("failure_rolled_back") == 1,
        "scheduler_drained_after_failure": seed.get("scheduler_drained") == 1,
        "source_path": "GGUF_TO_COLD_TO_RING_TO_HOT",
    })
    write_json(output / "scheduler-lifecycle.json", {
        "schema_version": "phase10-scheduler-lifecycle-v1",
        **common,
        "binary_sha256": sha256_file(scheduler),
        "runs": scheduler_runs,
        "speculative_budget_rejections": lifecycle.get("budget_rejections"),
        "same_generation_demand_promotions": lifecycle.get("demand_promotions"),
        "queued_speculative_cancellations": lifecycle.get("queued_speculative_cancellations"),
        "demand_preemptions": lifecycle.get("demand_preemptions"),
        "retry_uses_new_generation": lifecycle.get("retry_new_generation") == 1,
        "submitted_shutdown_drained": lifecycle.get("submitted_shutdown_drained") == 1,
        "zero_final_speculative_charges": lifecycle.get("zero_final_charges") == 1,
    })


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hot-test", required=True)
    parser.add_argument("--scheduler-test", required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output-dir", required=True)
    try:
        capture(parser.parse_args())
        return 0
    except (OSError, Phase10Error) as error:
        parser.error(str(error))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

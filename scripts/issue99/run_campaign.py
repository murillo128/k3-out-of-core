#!/usr/bin/env python3
"""Run the preregistered issue-99 campaign serially with fail-closed evidence handling."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from protocol import (
    CORPUS_PATH, EVIDENCE_ROOT, FROZEN_BINARY, LOW_BRIDGE_CACHE_BYTES,
    MODEL_PATH, N_CTX, THREADS, atomic_json, file_identity, reference_identity,
)


class CampaignError(RuntimeError):
    pass


def load(path: Path) -> dict[str, Any]:
    with path.open() as source:
        return json.load(source)


def load_host_helpers() -> Any:
    path = Path("scripts/phase13_6/run_cpu_demand_pairs.py").resolve()
    spec = importlib.util.spec_from_file_location("issue99_host_helpers", path)
    if spec is None or spec.loader is None:
        raise CampaignError("unable to load frozen host telemetry helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HOST = load_host_helpers()
DEVICES = ["nvme0n1", "nvme1n1", "nvme2n1"]
RECLAIM_PREFIXES = ("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_")


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


def cgroup_paths() -> tuple[Path | None, Path | None]:
    events = HOST.current_cgroup_memory_events()
    if events is None:
        return None, None
    return events, events.parent / "memory.current"


def process_status(pid: int) -> dict[str, int | str]:
    path = Path("/proc") / str(pid) / "status"
    return HOST.scalar_snapshot(path) if path.exists() else {}


def counter_delta(before: dict[str, int | str], after: dict[str, int | str]) -> dict[str, int]:
    return {
        key: int(after.get(key, 0)) - int(value)
        for key, value in before.items()
        if isinstance(value, int) and isinstance(after.get(key, 0), int)
    }


def run_with_envelope(command: list[str], directory: Path, identity: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    stdout_path = directory / "stdout.log"
    stderr_path = directory / "stderr.log"
    before = HOST.host_snapshot(DEVICES)
    before_pressure = pressure_totals()
    events_path, memory_current_path = cgroup_paths()
    before_events = HOST.scalar_snapshot(events_path) if events_path else {}
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
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        samples["pid"] = process.pid
        while process.poll() is None:
            status = process_status(process.pid)
            meminfo = HOST.scalar_snapshot(Path("/proc/meminfo"))
            samples["count"] += 1
            available = meminfo.get("MemAvailable")
            if isinstance(available, int):
                prior = samples["minimum_mem_available_kib"]
                samples["minimum_mem_available_kib"] = available if not isinstance(prior, int) else min(prior, available)
            current = scalar_value(memory_current_path)
            if isinstance(current, int):
                prior = samples["peak_cgroup_memory_current_bytes"]
                samples["peak_cgroup_memory_current_bytes"] = current if not isinstance(prior, int) else max(prior, current)
            for source, target in (("VmRSS", "peak_process_rss_kib"), ("VmHWM", "peak_process_hwm_kib"),
                                   ("VmSwap", "peak_process_swap_kib")):
                value = status.get(source)
                if isinstance(value, int):
                    samples[target] = max(samples[target], value)
            if status.get("Cpus_allowed_list") is not None:
                samples["cpu_affinity_allowed_list"] = status["Cpus_allowed_list"]
            time.sleep(0.5)
        returncode = process.returncode
    after = HOST.host_snapshot(DEVICES)
    after_pressure = pressure_totals()
    after_events = HOST.scalar_snapshot(events_path) if events_path else {}
    envelope = {
        "schema_version": "issue99-quality-cell-envelope-v1",
        "cell": identity,
        "command": command,
        "exit_status": returncode,
        "elapsed_s": time.monotonic() - started,
        "samples": samples,
        "before": before,
        "after": after,
        "delta": HOST.host_delta(before, after),
        "cgroup_memory_events_delta": counter_delta(before_events, after_events),
        "memory_pressure_total_delta_usec": {
            key: after_pressure.get(key, 0) - value for key, value in before_pressure.items()
        },
    }
    atomic_json(directory / "envelope.json", envelope)
    return returncode, envelope


def pressure_failures(envelope: dict[str, Any]) -> list[str]:
    failures = []
    vmstat = envelope["delta"]["vmstat"]
    for key in ("pswpin", "pswpout", "oom_kill"):
        if vmstat.get(key, 0):
            failures.append(f"{key}={vmstat[key]}")
    reclaim = {key: value for key, value in vmstat.items()
               if key.startswith(RECLAIM_PREFIXES) and value != 0}
    if reclaim:
        failures.append(f"reclaim/refault={reclaim}")
    cgroup = {key: value for key, value in envelope["cgroup_memory_events_delta"].items()
              if key in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill") and value}
    if cgroup:
        failures.append(f"cgroup={cgroup}")
    if envelope["samples"]["peak_process_swap_kib"]:
        failures.append("process swap")
    return failures


def validate_result(
    result_path: Path,
    route_path: Path,
    trace_path: Path,
    cell: dict[str, Any],
    capacity_bytes: int,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    result = load(result_path)
    failures = pressure_failures(envelope)
    if result.get("schema_version") != "issue99-quality-cell-v1" or result.get("status") != "pass":
        failures.append("result status/schema")
    if result.get("case", {}).get("id") != cell["case_id"] or result.get("policy") != cell["policy"] or \
            result.get("intervention") != cell["intervention"]:
        failures.append("cell identity")
    execution = result.get("execution", {})
    if execution.get("backend") != "CPU" or execution.get("gpu_device_count") != 0 or \
            execution.get("n_gpu_layers") != 0 or execution.get("n_ctx") != N_CTX or \
            execution.get("threads") != THREADS or execution.get("load_mode") != "DIRECT_IO" or \
            execution.get("runtime_mode") != "PERFORMANCE" or execution.get("current_layer_issue_mode") != "BATCHED":
        failures.append("execution envelope")
    preflight = result.get("preflight", {})
    cold = preflight.get("initial_cold", {})
    if not preflight.get("pass") or preflight.get("process_start_occupancy") != 0 or \
            not preflight.get("first_miss_backing_read") or cold.get("actual_bytes") != capacity_bytes or \
            cold.get("requested_bytes") != capacity_bytes:
        failures.append("fresh explicit cold-cache preflight")
    achieved = result.get("reference", {}).get("achieved_horizon", 0)
    records = achieved * 92
    if achieved <= 0 or achieved > cell["horizon"] or result.get("observer", {}).get("records") != records:
        failures.append("route coverage")
    trace = result.get("quality_trace", {})
    if trace.get("moe_records") != records or trace.get("hidden_records") != records or \
            trace.get("logits_records") != achieved or trace.get("failures") != 0:
        failures.append("tensor coverage")
    if result.get("resources", {}).get("vm_swap_kib") != 0 or \
            any(result.get("resources", {}).get("terminal_references", {}).values()):
        failures.append("terminal resources")
    if not route_path.is_file() or not trace_path.is_file() or route_path.stat().st_size == 0 or trace_path.stat().st_size == 0:
        failures.append("missing raw output")
    if failures:
        raise CampaignError("cell validation failed: " + "; ".join(failures))
    return result


def resident_bytes(path: Path) -> int | None:
    try:
        value = json.loads(subprocess.run(
            ["fincore", "-J", "-b", str(path)], check=True, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout)
        rows = value.get("fincore", [])
        return int(rows[0]["res"]) if rows else 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


def advise_output(
    path: Path,
    role: str,
    root: Path,
    allowlist: list[dict[str, Any]],
    known_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise CampaignError(f"refusing fadvise outside issue99 campaign root: {resolved}")
    identity = known_identity or file_identity(resolved)
    stat = resolved.stat()
    if identity["device"] != stat.st_dev or identity["inode"] != stat.st_ino or identity["size_bytes"] != stat.st_size:
        raise CampaignError("output identity changed before fadvise")
    before = resident_bytes(resolved)
    descriptor = os.open(resolved, os.O_RDONLY)
    try:
        os.fsync(descriptor)
        os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
    finally:
        os.close(descriptor)
    after = resident_bytes(resolved)
    event = {
        **identity,
        "role": role,
        "why_not_next_k3_input": "closed issue99-generated output; next K3 process does not consume this file",
        "resident_bytes_before": before,
        "resident_bytes_after": after,
        "advice": "POSIX_FADV_DONTNEED",
    }
    allowlist.append(event)
    atomic_json(root / "control/output-cache-allowlist.json", {
        "schema_version": "issue99-output-cache-allowlist-v1",
        "events": allowlist,
    })
    return identity


def make_reference(result: dict[str, Any], horizon: int, path: Path, parent: str | None = None) -> dict[str, Any]:
    targets = list(map(int, result["reference"]["target_ids"][:horizon]))
    seed = int(result["reference"]["seed_token"])
    identity = reference_identity(result["case"]["id"], horizon, seed, targets)
    value = {
        "schema_version": "issue99-reference-sequence-v1",
        "case_id": result["case"]["id"],
        "horizon_limit": horizon,
        "seed_token": seed,
        "target_ids": targets,
        "achieved_horizon": len(targets),
        "reference_identity": identity,
        "root_reference_identity": parent or identity,
        "source": "frozen high-cache issue99 EXACT free-generation arm",
    }
    atomic_json(path, value)
    return value


def persistent_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def cell_slug(cell: dict[str, Any]) -> str:
    suffix = "free" if cell["intervention"] == "FREE_TRAJECTORY" else "fixed"
    return f"{cell['order']:03d}-{cell['case_id']}-{cell['cache_regime']}-{cell['policy'].lower()}-{suffix}"


def dependencies_for(cell: dict[str, Any]) -> int:
    if cell["cohort"] == "broad":
        return 2
    if cell["cohort"] == "bridge" and cell["cache_regime"] == "high-cache":
        return 4
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--checkpoint-a-review", type=Path, required=True)
    parser.add_argument("--core-membership", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=EVIDENCE_ROOT / "campaign")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    prereg = load(args.preregistration)
    review = load(args.checkpoint_a_review)
    if prereg.get("status") != "frozen-outcome-blind" or review.get("verdict") != "PASS" or \
            review.get("preregistration_sha256") != file_identity(args.preregistration)["sha256"]:
        raise CampaignError("Checkpoint-A PASS does not bind the preregistration")
    root = args.output_root.resolve()
    (root / "cells").mkdir(parents=True, exist_ok=True)
    (root / "pairs").mkdir(parents=True, exist_ok=True)
    (root / "references").mkdir(parents=True, exist_ok=True)
    (root / "control").mkdir(parents=True, exist_ok=True)
    allowlist_path = root / "control/output-cache-allowlist.json"
    allowlist = load(allowlist_path).get("events", []) if allowlist_path.exists() else []
    progress_path = root / "control/progress.json"
    progress = load(progress_path) if progress_path.exists() else {
        "schema_version": "issue99-campaign-progress-v1", "status": "running", "completed": []}
    completed = set(progress["completed"])
    exact_by_group: dict[tuple[str, str], dict[str, Any]] = {}
    changed_completed: dict[tuple[str, str], int] = {}
    max_persistent = int(prereg["resource_limits"]["maximum_persistent_issue99_evidence_bytes"])
    for cell in prereg["cells"]:
        slug = cell_slug(cell)
        group = (cell["cohort"], cell["case_id"])
        directory = root / "cells" / slug
        if slug in completed:
            manifest = load(directory / "manifest.json")
            if manifest.get("status") != "pass":
                raise CampaignError(f"completed manifest is not pass: {slug}")
            if cell["policy"] == "EXACT":
                exact_by_group[group] = manifest
            else:
                changed_completed[group] = changed_completed.get(group, 0) + 1
            continue
        if persistent_bytes(root) >= max_persistent:
            raise CampaignError("preregistered persistent evidence limit reached")
        directory.mkdir(parents=False, exist_ok=False)
        result_path = directory / "result.json"
        route_path = directory / "routes.jsonl"
        trace_path = directory / "quality.p13q"
        capacity_bytes = prereg["capacity"]["issue99_cache_bytes"] \
            if cell["cache_regime"] == "high-cache" else LOW_BRIDGE_CACHE_BYTES
        reference_path: Path | None = None
        reference_root_identity: str
        if cell["policy"] == "EXACT" and cell["intervention"] == "FREE_TRAJECTORY":
            reference_root_identity = "generated-by-current-exact-cell"
        else:
            high_reference = root / "references" / f"{cell['case_id']}-high-{1024 if cell['cohort'] != 'broad' else 512}.json"
            if not high_reference.exists():
                raise CampaignError(f"high-cache reference is unavailable: {high_reference}")
            high_value = load(high_reference)
            reference_root_identity = high_value["root_reference_identity"]
            if cell["cache_regime"] == "96-gib-bridge":
                reference_path = root / "references" / f"{cell['case_id']}-low-input-512.json"
                if not reference_path.exists():
                    exact_manifest = exact_by_group.get(("bridge", cell["case_id"]))
                    if exact_manifest is None:
                        # Read the already-completed high bridge EXACT result when crossing cohort groups.
                        high_exact = next(row for row in prereg["cells"] if row["cohort"] == "bridge" and
                                          row["case_id"] == cell["case_id"] and row["policy"] == "EXACT")
                        high_result = load(root / "cells" / cell_slug(high_exact) / "result.json")
                    else:
                        high_result = load(Path(exact_manifest["artifacts"]["result"]["canonical_path"]))
                    make_reference(high_result, 512, reference_path, high_value["root_reference_identity"])
            else:
                reference_path = high_reference
        command_line = [
            str(FROZEN_BINARY), "--model", str(MODEL_PATH), "--prompt-corpus", str(CORPUS_PATH),
            "--case-id", cell["case_id"], "--output", str(result_path), "--route-output", str(route_path),
            "--quality-trace-output", str(trace_path), "--policy", cell["policy"],
            "--intervention", cell["intervention"], "--cold-cache-bytes", str(capacity_bytes),
            "--horizon", str(cell["horizon"]), "--issue-mode", "BATCHED", "--threads", str(THREADS),
            "--n-ctx", str(N_CTX),
        ]
        if reference_path is not None:
            command_line.extend(("--reference-sequence", str(reference_path)))
        returncode, envelope = run_with_envelope(command_line, directory, cell)
        if returncode != 0 or not result_path.exists():
            progress.update({"status": "failed", "failed_cell": slug})
            atomic_json(progress_path, progress)
            raise CampaignError(f"K3 cell failed without retry: {slug} exit={returncode}")
        result = validate_result(result_path, route_path, trace_path, cell, capacity_bytes, envelope)
        if cell["policy"] == "EXACT" and cell["intervention"] == "FREE_TRAJECTORY":
            reference_path = root / "references" / f"{cell['case_id']}-high-{cell['horizon']}.json"
            reference = make_reference(result, cell["horizon"], reference_path)
            reference_root_identity = reference["root_reference_identity"]
        artifacts = {
            "result": file_identity(result_path), "routes": file_identity(route_path),
            "quality_trace": file_identity(trace_path), "envelope": file_identity(directory / "envelope.json"),
            "stdout": file_identity(directory / "stdout.log"), "stderr": file_identity(directory / "stderr.log"),
        }
        if reference_path is not None:
            artifacts["reference_input_or_capture"] = file_identity(reference_path)
        pair_summary = None
        if cell["policy"] != "EXACT":
            exact_manifest = exact_by_group.get(group)
            if exact_manifest is None:
                raise CampaignError(f"same-regime EXACT comparator unavailable for {slug}")
            pair_id = slug
            analyzer = [
                args.python, str(Path(__file__).with_name("analyze_pair.py")),
                "--pair-id", pair_id,
                "--exact-result", exact_manifest["artifacts"]["result"]["canonical_path"],
                "--changed-result", str(result_path),
                "--exact-trace", exact_manifest["artifacts"]["quality_trace"]["canonical_path"],
                "--changed-trace", str(trace_path),
                "--exact-routes", exact_manifest["artifacts"]["routes"]["canonical_path"],
                "--changed-routes", str(route_path),
                "--evidence-class", cell["intervention"], "--cache-regime", cell["cache_regime"],
                "--reference-identity", reference_root_identity,
                "--core-membership", str(args.core_membership), "--output-dir", str(root / "pairs"),
            ]
            subprocess.run(analyzer, check=True)
            pair_summary_path = root / "pairs" / f"{pair_id}.summary.json"
            pair_summary = load(pair_summary_path)
            if pair_summary.get("status") != "pass":
                raise CampaignError("pair scalarization failed validation")
            for output in pair_summary["outputs"].values():
                output_path = Path(output)
                advise_output(output_path, "persistent-paired-scalar-dataset", root, allowlist)
            advise_output(pair_summary_path, "persistent-pair-summary", root, allowlist)
            advise_output(route_path, "persistent-changed-route-stream", root, allowlist, artifacts["routes"])
            exact_route = Path(exact_manifest["artifacts"]["routes"]["canonical_path"])
            advise_output(exact_route, "persistent-exact-route-stream", root, allowlist,
                          exact_manifest["artifacts"]["routes"])
            trace_identity = artifacts["quality_trace"]
            advise_output(trace_path, "ephemeral-changed-tensor-trace", root, allowlist, trace_identity)
            trace_path.unlink()
            artifacts["quality_trace"]["deleted_after_scalarization"] = True
            exact_trace = Path(exact_manifest["artifacts"]["quality_trace"]["canonical_path"])
            advise_output(exact_trace, "ephemeral-exact-tensor-trace-retained-for-next-pair", root, allowlist,
                          exact_manifest["artifacts"]["quality_trace"])
            changed_completed[group] = changed_completed.get(group, 0) + 1
        else:
            advise_output(trace_path, "ephemeral-exact-tensor-trace-retained-for-pairing", root, allowlist,
                          artifacts["quality_trace"])
        manifest = {
            "schema_version": "issue99-quality-cell-manifest-v1", "status": "pass", "slug": slug,
            "cell": cell, "reference_root_identity": reference_root_identity,
            "artifacts": artifacts, "pair_summary": pair_summary,
        }
        atomic_json(directory / "manifest.json", manifest)
        exact_by_group[group] = manifest if cell["policy"] == "EXACT" else exact_by_group[group]
        completed.add(slug)
        progress.update({"status": "running", "completed": sorted(completed), "last_completed": slug})
        atomic_json(progress_path, progress)
        required = dependencies_for(cell)
        if cell["policy"] != "EXACT" and changed_completed[group] == required:
            exact_manifest = exact_by_group[group]
            exact_trace = Path(exact_manifest["artifacts"]["quality_trace"]["canonical_path"])
            if exact_trace.exists():
                advise_output(exact_trace, "ephemeral-exact-tensor-trace-final-release", root, allowlist,
                              exact_manifest["artifacts"]["quality_trace"])
                exact_trace.unlink()
                release = {
                    "schema_version": "issue99-raw-trace-release-v1", "group": list(group),
                    "sha256": exact_manifest["artifacts"]["quality_trace"]["sha256"], "deleted": True,
                    "reason": "all preregistered same-regime pair scalarizations completed",
                }
                atomic_json(root / "control" / f"trace-release-{group[0]}-{group[1]}.json", release)
        print(f"ISSUE99_CAMPAIGN cell={slug} status=pass completed={len(completed)}/{len(prereg['cells'])}", flush=True)
    progress.update({"status": "complete", "completed": sorted(completed), "completed_cells": len(completed)})
    atomic_json(progress_path, progress)
    print(f"ISSUE99_CAMPAIGN status=complete cells={len(completed)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue99 campaign: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

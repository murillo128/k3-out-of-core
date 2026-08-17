#!/usr/bin/env python3
"""Run and validate the frozen issue-102 Stage-B/B2 observer campaign."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
from typing import Any


EXPECTED_SCHEMA = "phase13-6pg-stage-b-observer-preregistration-v2"
EXPECTED_PREREGISTRATION_SHA256 = (
    "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
)
EXPECTED_RUNTIME_SOURCE_TARGET = "7d7307452a97eec9b30d5028bd9e831a96c73990"
FIRST_RETRY_PROJECT_SHA = "ebda15d9953b72e89b3901a0860ae7309817c529"
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
EXPECTED_HELPER_SHA256 = "a8cd60963c7da3ece8937ba83834435217ac2ec7922de15c50cd5a59743fb392"
EXPECTED_RUNNER_SHA256 = "0e09960035666f15bfc82cef2a8dd81358f744a848f3f1f633d27d420afeca92"
EXPECTED_CAPTURE_COUNT = 44
EXPECTED_ROUTED_LAYERS = 92
EXPECTED_SELECTED_COUNT = 16
EXPECTED_CANDIDATE_COUNT = 32
EXPECTED_DECODE_FORWARDS = 64


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def git_revision(repo: pathlib.Path, *extra: str) -> str:
    return subprocess.check_output(
        ("git", "-C", str(repo), *extra), text=True,
    ).strip()


def verify_frozen_inputs(
    preregistration_path: pathlib.Path,
    preregistration: dict[str, Any],
    repo: pathlib.Path,
    project_sha: str,
) -> None:
    if sha256(preregistration_path) != EXPECTED_PREREGISTRATION_SHA256:
        raise ValueError("observer preregistration identity changed")
    if preregistration["schema_version"] != EXPECTED_SCHEMA:
        raise ValueError("observer preregistration schema changed")
    if preregistration["status"] != "frozen":
        raise ValueError("observer preregistration is not frozen")
    if preregistration["disposition"] != "READY_FOR_STAGE_B_OBSERVER_CAPTURE":
        raise ValueError("observer preregistration is not ready")
    if preregistration["capture_count"] != EXPECTED_CAPTURE_COUNT:
        raise ValueError("observer capture count changed")
    if len(preregistration["capture_plan"]) != EXPECTED_CAPTURE_COUNT:
        raise ValueError("observer capture plan length changed")
    runtime = preregistration["runtime"]
    if runtime["project_source_target"] != EXPECTED_RUNTIME_SOURCE_TARGET:
        raise ValueError("observer runtime source target changed")
    if runtime["nested_llama_cpp"] != EXPECTED_NESTED_SHA:
        raise ValueError("nested llama.cpp target changed")
    if runtime["helper_binary"]["sha256"] != EXPECTED_HELPER_SHA256:
        raise ValueError("observer helper preregistered identity changed")
    if runtime["runner"]["sha256"] != EXPECTED_RUNNER_SHA256:
        raise ValueError("qualification runner preregistered identity changed")
    if git_revision(repo, "rev-parse", "HEAD") != project_sha:
        raise ValueError("project HEAD does not match the requested execution identity")
    if git_revision(repo / "llama.cpp", "rev-parse", "HEAD") != EXPECTED_NESTED_SHA:
        raise ValueError("live nested llama.cpp revision changed")

    identities = (
        preregistration["inputs"]["selection_manifest"],
        preregistration["inputs"]["corpus"],
        preregistration["inputs"]["model_identity"],
        preregistration["inputs"]["build_fingerprint"],
        runtime["helper_source"],
        runtime["helper_binary"],
        runtime["runner"],
    )
    for identity in identities:
        path = pathlib.Path(identity["path"])
        if not path.is_file() or sha256(path) != identity["sha256"]:
            raise ValueError(f"frozen input identity changed: {path}")

    supersession = preregistration["supersession"]
    failed_attempt = supersession["failed_attempt"]
    for key in ("envelope", "stderr", "stdout"):
        identity = failed_attempt[key]
        path = pathlib.Path(identity["path"])
        if not path.is_file() or sha256(path) != identity["sha256"]:
            raise ValueError(f"preserved failed-attempt identity changed: {path}")
    if (pathlib.Path(failed_attempt["output_directory"]) / "result.json").exists():
        raise ValueError("preserved failed attempt unexpectedly has a result payload")

    expected_directories = {
        pathlib.Path(row["output_directory"]).resolve()
        for row in preregistration["capture_plan"]
    }
    failed_directory = pathlib.Path(failed_attempt["output_directory"]).resolve()
    campaign_root = failed_directory.parent
    observed_directories = {path.resolve() for path in campaign_root.glob("run-*") if path.is_dir()}
    unexpected = sorted(observed_directories - expected_directories - {failed_directory})
    if unexpected:
        raise ValueError(f"unregistered observer attempt directories: {unexpected}")
    if len(observed_directories) > supersession["maximum_total_process_attempts"]:
        raise ValueError("maximum observer process-attempt budget exceeded")


def active_helper_pids(binary: pathlib.Path) -> list[int]:
    target = str(binary.resolve()).encode()
    active = []
    for cmdline in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            arguments = cmdline.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if target in arguments:
            active.append(int(cmdline.parent.name))
    return sorted(active)


def require_finite(values: list[int | float], label: str) -> None:
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"non-finite {label}")


def validate_capture(
    plan: dict[str, Any],
    directory: pathlib.Path,
    project_sha: str,
) -> dict[str, Any]:
    required = tuple(directory / name for name in (
        "result.json", "envelope.json", "stdout.log", "stderr.log",
    ))
    if not all(path.is_file() for path in required):
        raise ValueError(f"capture is incomplete and may not be replaced: {directory}")
    result_path, envelope_path, stdout_path, stderr_path = required
    result = json.loads(result_path.read_text())
    envelope = json.loads(envelope_path.read_text())
    prompt_tokens = plan["actual_templated_prompt_tokens"]
    expected_requests = prompt_tokens + EXPECTED_DECODE_FORWARDS
    expected_records = expected_requests * EXPECTED_ROUTED_LAYERS

    expected_project_sha = FIRST_RETRY_PROJECT_SHA if plan["ordinal"] == 1 else project_sha
    vmstat = envelope.get("delta", {}).get("vmstat", {})
    cgroup = envelope.get("delta", {}).get("cgroup_memory_events", {})
    pressure = envelope.get("memory_pressure_total_delta_usec", {})
    unused_nvme = envelope.get("delta", {}).get("nvme", {}).get("nvme2n1", {})
    storage = result.get("measured", {}).get("storage_delta", {})
    asynchronous = result.get("measured", {}).get("async_delta", {})
    scheduler = result.get("measured", {}).get("scheduler_delta", {})
    resources = result.get("resources", {})
    system_memory = resources.get("system_memory", {})
    terminal_references = resources.get("terminal_references", {})
    scalar_checks = {
        "result schema": result.get("schema_version") == "issue102-exact-route-capture-v1",
        "result pass": result.get("status") == "pass" and result.get("exit_status") == 0,
        "case identity": result.get("case", {}).get("id") == plan["case_id"],
        "prompt count": result.get("case", {}).get("consumed_prompt_tokens") == prompt_tokens,
        "decode count": result.get("output", {}).get("generated_token_count") == EXPECTED_DECODE_FORWARDS,
        "EXACT point": result.get("point") == "EXACT",
        "full prompt": result.get("protocol") == "full-prompt",
        "observer provenance": result.get("observer", {}).get("provenance") == "MEASURED_OBSERVER",
        "non-performance": result.get("observer", {}).get("performance_evidence") is False,
        "candidate count": result.get("observer", {}).get("candidate_count") == EXPECTED_CANDIDATE_COUNT,
        "routed layers": result.get("observer", {}).get("routed_layers") == EXPECTED_ROUTED_LAYERS,
        "prefill tokens": result.get("observer", {}).get("prefill_tokens") == prompt_tokens,
        "decode forwards": result.get("observer", {}).get("decode_forwards") == EXPECTED_DECODE_FORWARDS,
        "prefill records": result.get("observer", {}).get("prefill_records") == prompt_tokens * EXPECTED_ROUTED_LAYERS,
        "decode records": result.get("observer", {}).get("decode_records")
        == EXPECTED_DECODE_FORWARDS * EXPECTED_ROUTED_LAYERS,
        "record count": result.get("observer", {}).get("record_count") == expected_records,
        "selected occurrences": result.get("observer", {}).get("selected_occurrence_count")
        == expected_records * EXPECTED_SELECTED_COUNT,
        "candidate occurrences": result.get("observer", {}).get("candidate_occurrence_count")
        == expected_records * EXPECTED_CANDIDATE_COUNT,
        "observer failures": result.get("observer", {}).get("stats", {}).get("failures") == 0,
        "runner pass": envelope.get("exit_status") == 0,
        "campaign": envelope.get("campaign") == "issue102-stage-b-observer",
        "run ordinal": envelope.get("run_ordinal") == plan["ordinal"],
        "EXACT envelope": envelope.get("point") == "EXACT",
        "project identity": envelope.get("identities", {}).get("project") == expected_project_sha,
        "nested identity": envelope.get("identities", {}).get("nested") == EXPECTED_NESTED_SHA,
        "binary identity": envelope.get("identities", {}).get("binary_sha256") == EXPECTED_HELPER_SHA256,
        "runner identity": envelope.get("identities", {}).get("runner_sha256") == EXPECTED_RUNNER_SHA256,
        "result swap": resources.get("vm_swap_kib") == 0,
        "sampled swap": envelope.get("samples", {}).get("peak_process_swap_kib") == 0,
        "host swap": vmstat.get("pswpin") == 0 and vmstat.get("pswpout") == 0,
        "host reclaim": all(
            value == 0 for key, value in vmstat.items()
            if key.startswith(("allocstall_", "pgscan_", "pgsteal_", "workingset_refault_"))
        ),
        "host oom": vmstat.get("oom_kill") == 0,
        "cgroup events": all(value == 0 for value in cgroup.values()),
        "memory pressure": all(value == 0 for value in pressure.values()),
        "unused NVMe": unused_nvme.get("read_bytes") == 0 and unused_nvme.get("read_operations") == 0,
        "storage errors": all(storage.get(key) == 0 for key in ("cancelled_reads", "short_reads", "io_errors")),
        "I/O fallback": all(
            asynchronous.get(key) == 0
            for key in (
                "read_requests_cancelled", "buffered_fallback_operations",
                "synchronous_fallback_operations",
            )
        ),
        "scheduler cleanup": all(
            scheduler.get(key) == 0
            for key in (
                "terminal_failed", "terminal_cancelled", "stale_completions",
                "active_requests", "queued_requests",
            )
        ),
        "terminal references": all(value == 0 for value in terminal_references.values()),
        "terminal scheduler": resources.get("terminal_scheduler_active_requests") == 0
        and resources.get("terminal_scheduler_queued_requests") == 0,
        "pressure circuit": system_memory.get("pressure_rejections") == 0
        and system_memory.get("pressure_circuit_open") is False,
    }
    failed = sorted(label for label, passed in scalar_checks.items() if not passed)
    if failed:
        raise ValueError(f"capture scalar validation failed for {plan['case_id']}: {failed}")

    records = result["observer"].get("records", [])
    if len(records) != expected_records:
        raise ValueError(f"observer record payload length changed for {plan['case_id']}")
    for index, record in enumerate(records):
        request_ordinal = index // EXPECTED_ROUTED_LAYERS + 1
        layer = index % EXPECTED_ROUTED_LAYERS + 1
        phase = "PREFILL" if request_ordinal <= prompt_tokens else "DECODE"
        selected = record.get("selected_experts", [])
        weights = record.get("selected_weights", [])
        candidates = record.get("candidate_experts", [])
        selection_scores = record.get("candidate_selection_scores", [])
        probabilities = record.get("candidate_probabilities", [])
        if (
            record.get("request_ordinal") != request_ordinal
            or record.get("layer") != layer
            or record.get("phase") != phase
            or len(selected) != EXPECTED_SELECTED_COUNT
            or len(weights) != EXPECTED_SELECTED_COUNT
            or len(candidates) != EXPECTED_CANDIDATE_COUNT
            or len(selection_scores) != EXPECTED_CANDIDATE_COUNT
            or len(probabilities) != EXPECTED_CANDIDATE_COUNT
            or selected != candidates[:EXPECTED_SELECTED_COUNT]
            or len(set(candidates)) != EXPECTED_CANDIDATE_COUNT
            or any(expert < 0 or expert >= 896 for expert in candidates)
        ):
            raise ValueError(
                f"observer record invariant failed for {plan['case_id']} at index {index}"
            )
        require_finite(weights, "selected weights")
        require_finite(selection_scores, "candidate selection scores")
        require_finite(probabilities, "candidate probabilities")

    return {
        "ordinal": plan["ordinal"],
        "case_id": plan["case_id"],
        "selection_role": plan["selection_role"],
        "execution_project_sha": expected_project_sha,
        "prompt_tokens": prompt_tokens,
        "generated_tokens": EXPECTED_DECODE_FORWARDS,
        "observer_records": expected_records,
        "selected_occurrences": expected_records * EXPECTED_SELECTED_COUNT,
        "candidate_occurrences": expected_records * EXPECTED_CANDIDATE_COUNT,
        "result": file_identity(result_path),
        "envelope": file_identity(envelope_path),
        "stdout": file_identity(stdout_path),
        "stderr": file_identity(stderr_path),
        "host_safety": {
            "peak_process_swap_kib": envelope["samples"]["peak_process_swap_kib"],
            "memory_pressure_total_delta_usec": envelope["memory_pressure_total_delta_usec"],
            "cgroup_memory_events": envelope["delta"]["cgroup_memory_events"],
            "unused_nvme_read_bytes": envelope["delta"]["nvme"]["nvme2n1"]["read_bytes"],
        },
    }


def progress_document(
    preregistration_path: pathlib.Path,
    project_sha: str,
    captures: list[dict[str, Any]],
    disposition: str,
) -> dict[str, Any]:
    return {
        "schema_version": "phase13-6pg-stage-b-observer-campaign-progress-v1",
        "status": "pass" if len(captures) == EXPECTED_CAPTURE_COUNT else "in_progress",
        "disposition": disposition,
        "provenance": "MEASURED_OBSERVER_NON_PERFORMANCE",
        "preregistration": file_identity(preregistration_path),
        "execution_project_sha": project_sha,
        "runtime_source_target": EXPECTED_RUNTIME_SOURCE_TARGET,
        "nested_llama_cpp": EXPECTED_NESTED_SHA,
        "helper_binary_sha256": EXPECTED_HELPER_SHA256,
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "accepted_capture_count": len(captures),
        "expected_capture_count": EXPECTED_CAPTURE_COUNT,
        "captures": captures,
    }


def main() -> int:
    args = arguments()
    preregistration_path = args.preregistration.resolve()
    repo = args.repo.resolve()
    model = args.model.resolve()
    progress_path = args.progress.resolve()
    preregistration = json.loads(preregistration_path.read_text())
    verify_frozen_inputs(preregistration_path, preregistration, repo, args.project_sha)
    if not model.is_file():
        raise ValueError(f"model shard is unavailable: {model}")

    runtime = preregistration["runtime"]
    binary = pathlib.Path(runtime["helper_binary"]["path"]).resolve()
    runner = pathlib.Path(runtime["runner"]["path"]).resolve()
    corpus = pathlib.Path(preregistration["inputs"]["corpus"]["path"]).resolve()
    model_identity = pathlib.Path(preregistration["inputs"]["model_identity"]["path"]).resolve()
    build_fingerprint = pathlib.Path(preregistration["inputs"]["build_fingerprint"]["path"]).resolve()
    configuration = preregistration["configuration"]
    captures: list[dict[str, Any]] = []

    for plan in preregistration["capture_plan"]:
        verify_frozen_inputs(preregistration_path, preregistration, repo, args.project_sha)
        directory = pathlib.Path(plan["output_directory"]).resolve()
        if directory.exists():
            summary = validate_capture(plan, directory, args.project_sha)
            captures.append(summary)
            write_json(
                progress_path,
                progress_document(
                    preregistration_path, args.project_sha, captures,
                    "READY_FOR_NEXT_CAPTURE",
                ),
            )
            print(json.dumps({
                "status": "accepted_existing",
                "ordinal": plan["ordinal"],
                "case_id": plan["case_id"],
                "result_sha256": summary["result"]["sha256"],
            }, sort_keys=True), flush=True)
            continue

        active = active_helper_pids(binary)
        if active:
            raise RuntimeError(f"observer helper already active: {active}")
        print(json.dumps({
            "status": "starting",
            "ordinal": plan["ordinal"],
            "case_id": plan["case_id"],
        }, sort_keys=True), flush=True)
        command = (
            str(runner),
            "--binary", str(binary),
            "--model", str(model),
            "--prompt-corpus", str(corpus),
            "--case-id", plan["case_id"],
            "--protocol", configuration["protocol"],
            "--output-root", str(directory.parent),
            "--name", directory.name,
            "--campaign", "issue102-stage-b-observer",
            "--run-ordinal", str(plan["ordinal"]),
            "--triplet", str(plan["ordinal"]),
            "--order", str(plan["ordinal"]),
            "--point", configuration["policy"],
            "--cold-cache-bytes", str(configuration["cache_bytes"]),
            "--project-sha", args.project_sha,
            "--nested-sha", EXPECTED_NESTED_SHA,
            "--model-identity", str(model_identity),
            "--build-fingerprint", str(build_fingerprint),
            "--decode-forwards", str(configuration["decode_forwards"]),
            "--warmup-limit", "128",
            "--threads", str(configuration["threads"]),
            "--n-ctx", str(configuration["n_ctx"]),
        )
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            write_json(
                progress_path,
                progress_document(
                    preregistration_path, args.project_sha, captures,
                    f"STOPPED_AT_FAILED_CAPTURE_{plan['ordinal']:03d}",
                ),
            )
            raise RuntimeError(
                f"capture {plan['ordinal']:03d} failed and may not be replaced: {directory}"
            )
        summary = validate_capture(plan, directory, args.project_sha)
        captures.append(summary)
        write_json(
            progress_path,
            progress_document(
                preregistration_path, args.project_sha, captures,
                "CAMPAIGN_COMPLETE" if len(captures) == EXPECTED_CAPTURE_COUNT else "READY_FOR_NEXT_CAPTURE",
            ),
        )
        print(json.dumps({
            "status": "accepted",
            "ordinal": plan["ordinal"],
            "case_id": plan["case_id"],
            "accepted_capture_count": len(captures),
            "result_sha256": summary["result"]["sha256"],
        }, sort_keys=True), flush=True)

    print(json.dumps({
        "status": "pass",
        "accepted_capture_count": len(captures),
        "progress": file_identity(progress_path),
    }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

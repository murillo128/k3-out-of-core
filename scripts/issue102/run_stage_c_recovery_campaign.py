#!/usr/bin/env python3
"""Run the reviewed issue-102 Stage-C recovery and frozen continuation serially."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import subprocess
import sys
from typing import Any


CAMPAIGN = "issue102-stage-c-recovery-v1"
CONTROL_SCHEMA = "phase13-6pg-stage-c-recovery-control-v1"
PROGRESS_SCHEMA = "phase13-6pg-stage-c-recovery-progress-v1"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=pathlib.Path, required=True)
    parser.add_argument("--expected-control-sha256", required=True)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    return {
        "path": str(resolved),
        "bytes": metadata.st_size,
        "sha256": sha256(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
    }


def verify_identity(row: dict[str, Any]) -> pathlib.Path:
    path = pathlib.Path(row["path"]).resolve(strict=True)
    observed = identity(path)
    if observed["bytes"] != row["bytes"] or observed["sha256"] != row["sha256"]:
        raise ValueError(f"frozen input identity changed: {path}")
    return path


def verify_preserved_metadata(row: dict[str, Any]) -> None:
    declared = pathlib.Path(row["path"])
    resolved = declared.resolve(strict=True)
    metadata = os.lstat(resolved)
    if (
        resolved != declared
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size != row["bytes"]
        or ("device" in row and metadata.st_dev != row["device"])
        or ("inode" in row and metadata.st_ino != row["inode"])
    ):
        raise ValueError(f"preserved output metadata changed: {declared}")


def load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open() as stream:
        return json.load(stream)


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def output_release_may_have_occurred(progress_path: pathlib.Path) -> bool:
    """Treat the durable pre-advice progress marker conservatively on every restart."""
    return progress_path.exists()


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def ancestor_pids() -> set[int]:
    result = {os.getpid()}
    current = os.getpid()
    while current > 1:
        try:
            current = int(pathlib.Path(f"/proc/{current}/stat").read_text().split()[3])
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
            break
        result.add(current)
    return result


def active_k3_processes() -> list[dict[str, Any]]:
    excluded = ancestor_pids()
    names = {
        "issue102-cross-prompt-probe", "issue102-exact-route-observer",
        "run_qualification_cell.py", "run_stage_c_campaign.py",
        "run_stage_c_recovery_campaign.py",
    }
    active = []
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(path.parent.name)
            values = [item.decode(errors="replace") for item in path.read_bytes().split(b"\0") if item]
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
        if pid in excluded:
            continue
        basenames = {pathlib.Path(value).name for value in values if " " not in value}
        if basenames & names:
            active.append({"pid": pid, "arguments": values})
    return active


def load_module(path: pathlib.Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load frozen module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_control(
    path: pathlib.Path, expected_sha256: str, released_outputs_possible: bool,
) -> tuple[dict[str, Any], dict[str, pathlib.Path]]:
    observed = identity(path)
    if observed["sha256"] != expected_sha256:
        raise ValueError("Stage-C recovery control identity changed")
    control = load_json(path)
    if (
        control.get("schema_version") != CONTROL_SCHEMA
        or control.get("status") != "frozen"
        or control.get("disposition") != "READY_AFTER_PUBLICATION_AND_INDEPENDENT_REVIEW_FOR_VERIFY_ONLY_THEN_RECOVERY_01"
        or control.get("authority", {}).get("classification") != "STAGE_C_EVIDENCE_PAGECACHE_ADMISSION_DRIFT"
        or len(control.get("plan", [])) != 48
        or control["plan"][0].get("output_name") != "recovery-01-run-001-01-math-b6-exact"
        or control["recovery_policy"]["recovery_attempt_budget"] != 1
        or control["recovery_policy"]["later_cell_retry_budget"] != 0
        or control["output_cache_hygiene"]["initial_expected_file_count"] != 199
        or control["configuration"]["cache_bytes"] != 137728475136
        or control["configuration"]["cache_slots"] != 7849
        or control["configuration"]["n_ctx"] != 768
        or control["configuration"]["threads"] != 32
        or control["configuration"]["decode_forwards"] != 64
    ):
        raise ValueError("Stage-C recovery control is not executable")
    if [row["run_ordinal"] for row in control["plan"]] != list(range(1, 49)):
        raise ValueError("frozen Stage-C run order changed")
    prompt_rows: dict[int, list[dict[str, Any]]] = {}
    for row in control["plan"]:
        prompt_rows.setdefault(row["prompt_ordinal"], []).append(row)
    for ordinal, rows in prompt_rows.items():
        expected = ["EXACT", "KNEE"] if ordinal % 2 == 1 else ["KNEE", "EXACT"]
        if [row["point"] for row in rows] != expected or len({row["case_id"] for row in rows}) != 1:
            raise ValueError(f"frozen Stage-C pair changed: {ordinal}")

    runtime_names = (
        "controller", "allowlist_builder", "hygiene_executor", "cache_operations",
        "helper_binary", "qualification_runner", "resume_guard_test",
    )
    paths = {name: verify_identity(control["runtime"][name]) for name in runtime_names}
    if paths["controller"] != pathlib.Path(__file__).resolve(strict=True):
        raise ValueError("invoked recovery controller is not the frozen controller")
    input_names = (
        "stage_c_preregistration", "original_failed_progress", "original_technical_return",
        "observer_output_allowlist", "stage_b_capacity_handoff", "hygiene_reference",
        "corpus", "model_identity", "build_fingerprint",
    )
    paths.update({name: verify_identity(control["inputs"][name]) for name in input_names})
    for name in ("route_analysis_index", "observer_replay_index", "posthoc_analysis_index"):
        if not released_outputs_possible:
            paths[name] = verify_identity(control["inputs"][name])
        else:
            paths[name] = pathlib.Path(control["inputs"][name]["path"]).resolve(strict=True)
    paths["model"] = pathlib.Path(control["runtime"]["model_first_shard"]).resolve(strict=True)

    prereg = load_json(paths["stage_c_preregistration"])
    original = load_json(paths["original_failed_progress"])
    technical = load_json(paths["original_technical_return"])
    if (
        prereg.get("status") != "frozen"
        or prereg.get("nested_llama_cpp_sha") != control["nested_llama_cpp_sha"]
        or prereg.get("plan") != [
            {key: value for key, value in row.items() if key != "attempt_kind"}
            | ({"output_name": "run-001-01-math-b6-exact"} if row["run_ordinal"] == 1 else {})
            for row in control["plan"]
        ]
        or original.get("status") != "failed"
        or original.get("accepted_cell_count") != 0
        or original.get("failed_cell_count") != 1
        or technical.get("status") != "blocked"
        or technical.get("classification") != "PRE_CONTEXT_CAPACITY_ADMISSION_FAILURE"
    ):
        raise ValueError("original Stage-C contract/failure preservation changed")
    if git_output("rev-parse", "HEAD") != control["execution_project_sha"]:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", control["execution_project_sha"], "HEAD"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if git_output("-C", "llama.cpp", "rev-parse", "HEAD") != control["nested_llama_cpp_sha"]:
        raise ValueError("nested llama.cpp target changed")
    return control, paths


def validate_cell(
    plan: dict[str, Any], root: pathlib.Path, control: dict[str, Any],
    runner_module: Any,
) -> dict[str, Any]:
    paths = {name: root / name for name in ("result.json", "envelope.json", "stdout.log", "stderr.log")}
    if any(not path.is_file() for path in paths.values()):
        raise ValueError(f"Stage-C recovery cell output is incomplete: {root}")
    result = load_json(paths["result.json"])
    envelope = load_json(paths["envelope.json"])
    runner_module.verify(
        result, envelope, control["configuration"]["cache_bytes"], plan["point"],
        plan["case_id"], "full-prompt", "nvme0n1", "nvme2n1",
    )
    if (
        envelope.get("campaign") != CAMPAIGN
        or envelope.get("run_ordinal") != plan["run_ordinal"]
        or envelope.get("triplet") != plan["prompt_ordinal"]
        or envelope.get("order") != plan["pair_position"]
        or envelope.get("identities", {}).get("project") != control["execution_project_sha"]
        or envelope.get("identities", {}).get("nested") != control["nested_llama_cpp_sha"]
        or result.get("case", {}).get("templated_prompt_tokens") != plan["prompt_tokens"]
        or result.get("measured", {}).get("decode_forwards") != 64
        or result.get("output", {}).get("generated_token_count") != 64
    ):
        raise ValueError(f"Stage-C recovery cell identity/shape changed: {root}")
    measured = result["measured"]
    cold = measured["cold_delta"]
    routing = result["routing"]["stats"]
    return {
        "run_ordinal": plan["run_ordinal"],
        "prompt_ordinal": plan["prompt_ordinal"],
        "pair_position": plan["pair_position"],
        "case_id": plan["case_id"],
        "semantic_family": plan["semantic_family"],
        "length_level": plan["length_level"],
        "selection_role": plan["selection_role"],
        "point": plan["point"],
        "prompt_tokens": plan["prompt_tokens"],
        "attempt_kind": plan["attempt_kind"],
        "decode_tok_s": measured["decode_tok_s"],
        "hits": cold["hits"],
        "misses": cold["misses"],
        "hit_ratio": cold["hits"] / cold["requests"],
        "loads_per_token": cold["misses"] / measured["decode_forwards"],
        "backing_bytes": measured["storage_delta"]["backing_bytes"],
        "bytes_per_token": measured["storage_delta"]["backing_bytes"] / measured["decode_forwards"],
        "generated_token_count": result["output"]["generated_token_count"],
        "generated_token_hash": result["output"]["generated_token_hash"],
        "routing": {
            "changed_decisions": routing["changed_decisions"],
            "realized_swaps": routing["swaps"],
            "cumulative_score_regret": routing["cumulative_score_regret"],
            "maximum_realized_regret": routing["maximum_realized_regret"],
            "maximum_realized_regret_status": routing["maximum_realized_regret_status"],
        },
        "artifacts": {name: identity(path) for name, path in paths.items()},
        "host_safety": {
            "peak_process_swap_kib": envelope["samples"]["peak_process_swap_kib"],
            "unused_nvme_read_bytes": envelope["delta"]["nvme"].get("nvme2n1", {}).get("read_bytes", 0),
            "memory_pressure_total_delta_usec": envelope["memory_pressure_total_delta_usec"],
            "cgroup_memory_events": envelope["delta"]["cgroup_memory_events"],
        },
    }


def initial_progress(control_path: pathlib.Path, control: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PROGRESS_SCHEMA,
        "status": "in_progress",
        "provenance": "MEASURED_STAGE_C_PERFORMANCE_WITH_AMENDED_OUTPUT_ONLY_HYGIENE",
        "inputs": {
            "recovery_control": identity(control_path),
            "original_failed_progress": control["inputs"]["original_failed_progress"],
            "original_technical_return": control["inputs"]["original_technical_return"],
        },
        "execution_project_sha": control["execution_project_sha"],
        "nested_llama_cpp_sha": control["nested_llama_cpp_sha"],
        "original_failure_preserved": True,
        "output_release_may_have_occurred": True,
        "release_guard": "PROGRESS_EXISTENCE_IS_DURABLE_BEFORE_ANY_OUTPUT_ADVICE",
        "expected_cell_count": 48,
        "accepted_cell_count": 0,
        "failed_cell_count": 0,
        "next_run_ordinal": 1,
        "recovery_attempt_budget_remaining": 1,
        "later_cell_retry_budget_remaining": 0,
        "hygiene_events": [],
        "processes_started": [],
        "captures": [],
        "failures": [],
        "technical_failures": [],
        "disposition": "READY_FOR_OUTPUT_HYGIENE_THEN_RECOVERY_01_CELL_001",
    }


def verify_progress(
    progress: dict[str, Any], control_path: pathlib.Path, control: dict[str, Any], output_root: pathlib.Path,
) -> None:
    captures = progress.get("captures", [])
    starts = progress.get("processes_started", [])
    hygiene_events = progress.get("hygiene_events", [])
    if (
        progress.get("schema_version") != PROGRESS_SCHEMA
        or progress.get("status") not in {"in_progress", "pass"}
        or progress.get("inputs", {}).get("recovery_control", {}).get("sha256") != sha256(control_path)
        or progress.get("execution_project_sha") != control["execution_project_sha"]
        or progress.get("nested_llama_cpp_sha") != control["nested_llama_cpp_sha"]
        or progress.get("output_release_may_have_occurred") is not True
        or progress.get("failed_cell_count") != 0
        or progress.get("failures")
        or progress.get("technical_failures")
        or [row["run_ordinal"] for row in captures] != list(range(1, len(captures) + 1))
        or [row["run_ordinal"] for row in starts] != list(range(1, len(starts) + 1))
        or len(starts) != len(captures)
        or len(hygiene_events) not in {len(starts), len(starts) + 1}
        or progress.get("recovery_attempt_budget_remaining") != (1 if not starts else 0)
        or progress.get("later_cell_retry_budget_remaining") != 0
    ):
        raise ValueError("Stage-C recovery progress is not safely resumable")
    if progress["status"] == "pass" and len(captures) != 48:
        raise ValueError("Stage-C recovery PASS is incomplete")
    for capture, plan in zip(captures, control["plan"]):
        if capture["run_ordinal"] != plan["run_ordinal"] or capture["case_id"] != plan["case_id"]:
            raise ValueError("Stage-C recovery capture prefix changed")
        for artifact in capture["artifacts"].values():
            verify_preserved_metadata(artifact)
    for event in hygiene_events:
        allowlist_path = verify_identity(event["allowlist"])
        hygiene_path = verify_identity(event["hygiene"])
        hygiene = load_json(hygiene_path)
        if hygiene.get("status") != "pass" or not all(hygiene.get("gate", {}).values()):
            raise ValueError("preserved output-only hygiene event is not PASS")
        if load_json(allowlist_path).get("status") != "frozen":
            raise ValueError("preserved output-only allowlist changed")
    expected_dirs = {row["output_name"] for row in control["plan"][:len(captures)]}
    actual_dirs = {path.name for path in output_root.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        raise ValueError("Stage-C recovery output root contains an incomplete or unowned attempt")


def command_base(control: dict[str, Any], paths: dict[str, pathlib.Path], plan: dict[str, Any]) -> list[str]:
    return [
        sys.executable, str(paths["qualification_runner"]),
        "--binary", str(paths["helper_binary"]),
        "--model", str(paths["model"]),
        "--prompt-corpus", str(paths["corpus"]),
        "--case-id", plan["case_id"],
        "--protocol", "full-prompt",
        "--output-root", control["output"]["root"],
        "--name", plan["output_name"],
        "--campaign", CAMPAIGN,
        "--run-ordinal", str(plan["run_ordinal"]),
        "--triplet", str(plan["prompt_ordinal"]),
        "--order", str(plan["pair_position"]),
        "--point", plan["point"],
        "--cold-cache-bytes", str(control["configuration"]["cache_bytes"]),
        "--project-sha", control["execution_project_sha"],
        "--nested-sha", control["nested_llama_cpp_sha"],
        "--model-identity", control["inputs"]["model_identity"]["sha256"],
        "--build-fingerprint", str(paths["build_fingerprint"]),
        "--decode-forwards", "64", "--threads", "32", "--n-ctx", "768",
    ]


def build_and_apply_hygiene(
    control: dict[str, Any], paths: dict[str, pathlib.Path], progress_path: pathlib.Path,
    progress: dict[str, Any], plan: dict[str, Any], event_ordinal: int,
) -> dict[str, Any]:
    control_root = pathlib.Path(control["output"]["control_root"])
    stem = f"event-{event_ordinal:03d}-before-run-{plan['run_ordinal']:03d}"
    allowlist_path = control_root / f"output-cache-allowlist-{stem}.json"
    hygiene_path = control_root / f"output-cache-hygiene-{stem}.json"
    if allowlist_path.exists() or hygiene_path.exists():
        raise ValueError("output-only hygiene evidence path already exists")
    common_expected = [
        "--expected-observer-allowlist-sha256", control["inputs"]["observer_output_allowlist"]["sha256"],
        "--expected-handoff-sha256", control["inputs"]["stage_b_capacity_handoff"]["sha256"],
        "--expected-route-index-sha256", control["inputs"]["route_analysis_index"]["sha256"],
        "--expected-posthoc-index-sha256", control["inputs"]["posthoc_analysis_index"]["sha256"],
        "--expected-original-progress-sha256", control["inputs"]["original_failed_progress"]["sha256"],
    ]
    command = [sys.executable, str(paths["allowlist_builder"]), *common_expected]
    if progress["hygiene_events"]:
        base = progress["hygiene_events"][-1]["allowlist"]
        command.extend(["--base-allowlist", base["path"], "--expected-base-allowlist-sha256", base["sha256"]])
    else:
        command.extend([
            "--observer-allowlist", str(paths["observer_output_allowlist"]),
            "--handoff", str(paths["stage_b_capacity_handoff"]),
            "--route-index", str(paths["route_analysis_index"]),
            "--replay-index", str(paths["observer_replay_index"]),
            "--posthoc-index", str(paths["posthoc_analysis_index"]),
            "--original-progress", str(paths["original_failed_progress"]),
        ])
    command.extend([
        "--amended-progress", str(progress_path),
        "--expected-amended-progress-sha256", sha256(progress_path),
        "--output", str(allowlist_path),
    ])
    built = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if built.returncode != 0 or not allowlist_path.is_file():
        raise RuntimeError(f"output allowlist build failed: {built.stderr.strip()}")
    allowlist_id = identity(allowlist_path)
    allowlist = load_json(allowlist_path)
    expected_c = 3 + 4 * len(progress["captures"])
    if (
        allowlist.get("status") != "frozen"
        or allowlist.get("file_count") != 196 + expected_c
        or allowlist.get("class_file_counts") != {
            "A_OBSERVER_OUTPUT": 182,
            "B_POSTPROCESSING_OUTPUT": 14,
            "C_STAGE_C_OUTPUT": expected_c,
        }
    ):
        raise ValueError("output-only allowlist coverage changed")
    applied = subprocess.run([
        sys.executable, str(paths["hygiene_executor"]),
        "--allowlist", str(allowlist_path),
        "--expected-allowlist-sha256", allowlist_id["sha256"],
        "--reference-preflight", str(paths["hygiene_reference"]),
        "--output", str(hygiene_path),
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if not hygiene_path.is_file():
        raise RuntimeError(f"output-only hygiene did not preserve evidence: {applied.stderr.strip()}")
    hygiene_id = identity(hygiene_path)
    hygiene = load_json(hygiene_path)
    event = {
        "event_ordinal": event_ordinal,
        "before_run_ordinal": plan["run_ordinal"],
        "case_id": plan["case_id"],
        "point": plan["point"],
        "allowlist": allowlist_id,
        "hygiene": hygiene_id,
        "file_count": allowlist["file_count"],
        "class_file_counts": allowlist["class_file_counts"],
        "resident_bytes_before": hygiene.get("files", {}).get("resident_bytes_before"),
        "resident_bytes_after": hygiene.get("files", {}).get("resident_bytes_after"),
        "projected_admission_margin_after_bytes": hygiene.get("host", {}).get("guard_projection_after", {}).get("projected_admission_margin_bytes"),
        "status": hygiene.get("status"),
        "executor_exit_status": applied.returncode,
    }
    if applied.returncode != 0 or hygiene.get("status") != "pass" or not all(hygiene.get("gate", {}).values()):
        raise RuntimeError(f"output-only hygiene gate failed: {json.dumps(event, sort_keys=True)}")
    return event


def preserve_failure_artifacts(root: pathlib.Path) -> dict[str, Any]:
    if not root.is_dir():
        return {}
    return {path.name: identity(path) for path in sorted(root.iterdir()) if path.is_file()}


def main() -> int:
    args = arguments()
    control_path = args.control.resolve(strict=True)
    if identity(control_path)["sha256"] != args.expected_control_sha256:
        raise ValueError("Stage-C recovery control identity changed")
    preliminary = load_json(control_path)
    progress_path = pathlib.Path(preliminary["output"]["progress"])
    released_outputs_possible = output_release_may_have_occurred(progress_path)
    control, paths = validate_control(control_path, args.expected_control_sha256, released_outputs_possible)
    active = active_k3_processes()
    if active:
        raise RuntimeError(f"K3/helper process already active: {active}")
    output_root = pathlib.Path(control["output"]["root"])
    control_root = pathlib.Path(control["output"]["control_root"])
    progress_path = pathlib.Path(control["output"]["progress"])
    if args.verify_only:
        if output_root.exists() or progress_path.exists():
            raise ValueError("amended Stage-C outcome exists during pristine verify-only")
        print(json.dumps({
            "status": "pass", "mode": "verify-only", "control": identity(control_path),
            "plan_cells": 48, "unique_prompts": 24, "active_k3_processes": 0,
            "recovery_outcomes_inspected": 0,
            "disposition": "READY_AFTER_INDEPENDENT_REVIEW_FOR_RECOVERY_01",
        }, sort_keys=True))
        return 0

    runner_module = load_module(paths["qualification_runner"], "issue102_frozen_qualification_runner")
    if progress_path.exists():
        progress = load_json(progress_path)
        if progress.get("status") == "failed":
            raise ValueError("failed Stage-C recovery progress is terminal and cannot be retried")
        verify_progress(progress, control_path, control, output_root)
    else:
        if output_root.exists():
            raise ValueError("amended Stage-C output root exists without owned progress")
        output_root.mkdir(parents=True)
        control_root.mkdir(parents=True, exist_ok=True)
        progress = initial_progress(control_path, control)
        write_json(progress_path, progress)

    for plan in control["plan"][len(progress["captures"]):]:
        if active_k3_processes():
            raise RuntimeError("K3/helper process became active before the next frozen cell")
        event_ordinal = len(progress["hygiene_events"]) + 1
        try:
            event = build_and_apply_hygiene(
                control, paths, progress_path, progress, plan, event_ordinal,
            )
            progress["hygiene_events"].append(event)
            write_json(progress_path, progress)
        except Exception as error:
            progress["status"] = "failed"
            progress["technical_failures"].append({
                "before_run_ordinal": plan["run_ordinal"],
                "case_id": plan["case_id"], "point": plan["point"],
                "phase": "OUTPUT_ONLY_HYGIENE_BEFORE_PHYSICAL_PROCESS",
                "error": f"{type(error).__name__}: {error}",
            })
            progress["disposition"] = "STOP_PRESERVE_HYGIENE_FAILURE_RETURN_TO_DESIGN"
            write_json(progress_path, progress)
            raise

        start = {
            "run_ordinal": plan["run_ordinal"], "case_id": plan["case_id"],
            "point": plan["point"], "output_name": plan["output_name"],
            "attempt_kind": plan["attempt_kind"], "hygiene_event_ordinal": event_ordinal,
        }
        progress["processes_started"].append(start)
        if plan["run_ordinal"] == 1:
            progress["recovery_attempt_budget_remaining"] = 0
        progress["disposition"] = f"PHYSICAL_PROCESS_{plan['run_ordinal']:03d}_STARTED_NO_RETRY_ON_AMBIGUITY"
        write_json(progress_path, progress)

        attempt = subprocess.run(command_base(control, paths, plan), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        root = output_root / plan["output_name"]
        failure_reason = None
        capture = None
        if attempt.returncode != 0:
            failure_reason = f"runner exit status {attempt.returncode}"
        else:
            try:
                capture = validate_cell(plan, root, control, runner_module)
            except Exception as error:
                failure_reason = f"{type(error).__name__}: {error}"
        if failure_reason is not None:
            progress["status"] = "failed"
            progress["failed_cell_count"] = 1
            progress["failures"].append({
                **start,
                "root": str(root),
                "runner_exit_status": attempt.returncode,
                "runner_stdout": attempt.stdout,
                "runner_stderr": attempt.stderr,
                "failure_reason": failure_reason,
                "artifacts": preserve_failure_artifacts(root),
            })
            progress["next_run_ordinal"] = plan["run_ordinal"]
            progress["disposition"] = (
                "STOP_PRESERVE_RECOVERY_01_FAILURE_RETURN_TO_DESIGN"
                if plan["run_ordinal"] == 1
                else "STOP_PRESERVE_LATER_STAGE_C_FAILURE_NO_RETRY"
            )
            write_json(progress_path, progress)
            raise RuntimeError(f"Stage-C physical process failed: {failure_reason}")

        progress["captures"].append(capture)
        progress["accepted_cell_count"] = len(progress["captures"])
        progress["next_run_ordinal"] = plan["run_ordinal"] + 1 if plan["run_ordinal"] < 48 else None
        if len(progress["captures"]) == 48:
            progress["status"] = "pass"
            progress["disposition"] = "STAGE_C_RECOVERY_AND_FROZEN_CONTINUATION_COMPLETE_READY_FOR_SYNTHESIS"
        else:
            progress["disposition"] = f"READY_FOR_OUTPUT_HYGIENE_THEN_STAGE_C_CELL_{plan['run_ordinal'] + 1:03d}"
        write_json(progress_path, progress)
        print(json.dumps({
            "status": "pass", "accepted": len(progress["captures"]), "expected": 48,
            "run_ordinal": plan["run_ordinal"], "case_id": plan["case_id"],
            "point": plan["point"], "result_sha256": capture["artifacts"]["result.json"]["sha256"],
        }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

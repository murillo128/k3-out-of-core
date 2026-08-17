#!/usr/bin/env python3
"""Run or resume the frozen issue-102 Stage-C EXACT/KNEE campaign serially."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


CAMPAIGN = "issue102-stage-c"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--expected-preregistration-sha256", required=True)
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
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def verify_identity(row: dict[str, Any]) -> pathlib.Path:
    path = pathlib.Path(row["path"]).resolve(strict=True)
    observed = identity(path)
    if observed["bytes"] != row["bytes"] or observed["sha256"] != row["sha256"]:
        raise ValueError(f"frozen input identity changed: {path}")
    return path


def load_json(path: pathlib.Path) -> dict[str, Any]:
    with path.open() as stream:
        return json.load(stream)


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)


def ancestor_pids() -> set[int]:
    result = {os.getpid()}
    current = os.getpid()
    while current > 1:
        try:
            fields = pathlib.Path(f"/proc/{current}/stat").read_text().split()
            current = int(fields[3])
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
            break
        result.add(current)
    return result


def active_k3_processes() -> list[dict[str, Any]]:
    excluded = ancestor_pids()
    active = []
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(path.parent.name)
            args = [item.decode(errors="replace") for item in path.read_bytes().split(b"\0") if item]
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
        if pid in excluded or not args:
            continue
        basenames = {pathlib.Path(item).name for item in args if "/" in item or " " not in item}
        if basenames & {
            "issue102-cross-prompt-probe",
            "issue102-exact-route-observer",
            "run_qualification_cell.py",
            "run_stage_c_campaign.py",
        }:
            active.append({"pid": pid, "arguments": args})
    return active


def load_runner(path: pathlib.Path) -> Any:
    spec = importlib.util.spec_from_file_location("issue102_frozen_qualification_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load qualification runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def validate_preregistration(
    path: pathlib.Path, expected_sha256: str,
) -> tuple[dict[str, Any], dict[str, pathlib.Path]]:
    observed = identity(path)
    if observed["sha256"] != expected_sha256:
        raise ValueError("Stage-C preregistration identity changed")
    prereg = load_json(path)
    if (
        prereg.get("schema_version") != "phase13-6pg-stage-c-preregistration-v1"
        or prereg.get("status") != "frozen"
        or prereg.get("disposition") != "READY_AFTER_PUBLICATION_FOR_VERIFY_ONLY_THEN_SERIAL_STAGE_C"
        or len(prereg.get("plan", [])) != 48
        or prereg["outcome_inspection"]["stage_c_outcomes_inspected"] != 0
        or prereg["configuration"]["retry_budget"] != 0
    ):
        raise ValueError("Stage-C preregistration is not executable")
    if [row["run_ordinal"] for row in prereg["plan"]] != list(range(1, 49)):
        raise ValueError("Stage-C run ordinals changed")
    prompt_rows: dict[int, list[dict[str, Any]]] = {}
    for row in prereg["plan"]:
        prompt_rows.setdefault(row["prompt_ordinal"], []).append(row)
    if sorted(prompt_rows) != list(range(1, 25)):
        raise ValueError("Stage-C prompt ordinals changed")
    for prompt_ordinal, rows in prompt_rows.items():
        expected = ["EXACT", "KNEE"] if prompt_ordinal % 2 == 1 else ["KNEE", "EXACT"]
        if [row["point"] for row in rows] != expected or len({row["case_id"] for row in rows}) != 1:
            raise ValueError(f"Stage-C pair order changed: {prompt_ordinal}")

    paths = {
        name: verify_identity(prereg["inputs"][name])
        for name in (
            "selection", "stage_a_final_checkpoint", "stage_b_capacity_handoff",
            "final_observer_cache_hygiene", "corpus", "model_identity", "build_fingerprint",
        )
    }
    paths["binary"] = verify_identity(prereg["runtime"]["helper_binary"])
    paths["runner"] = verify_identity(prereg["runtime"]["runner"])
    paths["controller"] = verify_identity(prereg["runtime"]["controller"])
    if paths["controller"] != pathlib.Path(__file__).resolve(strict=True):
        raise ValueError("invoked controller is not the frozen controller")
    paths["model"] = pathlib.Path(prereg["runtime"]["model_first_shard"]).resolve(strict=True)
    if git_output("rev-parse", "HEAD") == prereg["execution_project_sha"]:
        pass
    else:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", prereg["execution_project_sha"], "HEAD"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if git_output("-C", "llama.cpp", "rev-parse", "HEAD") != prereg["nested_llama_cpp_sha"]:
        raise ValueError("nested llama.cpp target changed")
    hygiene = load_json(paths["final_observer_cache_hygiene"])
    if (
        hygiene.get("status") != "pass"
        or not all(hygiene.get("gate", {}).values())
        or hygiene["files"]["resident_bytes_after"] != 0
        or hygiene["operation"]["model_or_runtime_file_touched"] is not False
    ):
        raise ValueError("final observer-output cache hygiene is not a clean PASS")
    return prereg, paths


def validate_cell(
    plan: dict[str, Any], root: pathlib.Path, prereg: dict[str, Any],
    paths: dict[str, pathlib.Path], runner_module: Any,
) -> dict[str, Any]:
    result_path = root / "result.json"
    envelope_path = root / "envelope.json"
    stdout_path = root / "stdout.log"
    stderr_path = root / "stderr.log"
    for path in (result_path, envelope_path, stdout_path, stderr_path):
        if not path.is_file():
            raise ValueError(f"Stage-C cell is missing {path.name}: {root}")
    result = load_json(result_path)
    envelope = load_json(envelope_path)
    runner_module.verify(
        result, envelope, prereg["configuration"]["cache_bytes"], plan["point"],
        plan["case_id"], "full-prompt", "nvme0n1", "nvme2n1",
    )
    if (
        envelope.get("campaign") != CAMPAIGN
        or envelope.get("run_ordinal") != plan["run_ordinal"]
        or envelope.get("triplet") != plan["prompt_ordinal"]
        or envelope.get("order") != plan["pair_position"]
        or envelope.get("identities", {}).get("project") != prereg["execution_project_sha"]
        or envelope.get("identities", {}).get("nested") != prereg["nested_llama_cpp_sha"]
        or result.get("case", {}).get("templated_prompt_tokens") != plan["prompt_tokens"]
        or result.get("measured", {}).get("decode_forwards") != 64
        or result.get("output", {}).get("generated_token_count") != 64
    ):
        raise ValueError(f"Stage-C cell identity/shape changed: {root}")
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
        "artifacts": {
            "result": identity(result_path),
            "envelope": identity(envelope_path),
            "stdout": identity(stdout_path),
            "stderr": identity(stderr_path),
        },
        "host_safety": {
            "peak_process_swap_kib": envelope["samples"]["peak_process_swap_kib"],
            "unused_nvme_read_bytes": envelope["delta"]["nvme"].get("nvme2n1", {}).get("read_bytes", 0),
            "memory_pressure_total_delta_usec": envelope["memory_pressure_total_delta_usec"],
            "cgroup_memory_events": envelope["delta"]["cgroup_memory_events"],
        },
    }


def initial_progress(prereg_path: pathlib.Path, prereg: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "phase13-6pg-stage-c-progress-v1",
        "status": "in_progress",
        "provenance": "MEASURED_STAGE_C_PERFORMANCE",
        "inputs": {"preregistration": identity(prereg_path)},
        "execution_project_sha": prereg["execution_project_sha"],
        "nested_llama_cpp_sha": prereg["nested_llama_cpp_sha"],
        "expected_cell_count": 48,
        "accepted_cell_count": 0,
        "failed_cell_count": 0,
        "next_run_ordinal": 1,
        "captures": [],
        "failures": [],
        "retry_budget_remaining": 0,
        "disposition": "READY_FOR_SERIAL_STAGE_C_CELL_001",
    }


def verify_resume(
    progress: dict[str, Any], prereg_path: pathlib.Path, prereg: dict[str, Any],
    paths: dict[str, pathlib.Path], runner_module: Any, output_root: pathlib.Path,
) -> None:
    if (
        progress.get("schema_version") != "phase13-6pg-stage-c-progress-v1"
        or progress.get("inputs", {}).get("preregistration", {}).get("sha256") != sha256(prereg_path)
        or progress.get("execution_project_sha") != prereg["execution_project_sha"]
        or progress.get("nested_llama_cpp_sha") != prereg["nested_llama_cpp_sha"]
        or progress.get("failed_cell_count") != 0
        or progress.get("retry_budget_remaining") != 0
    ):
        raise ValueError("Stage-C progress is not safely resumable")
    captures = progress.get("captures", [])
    if [row["run_ordinal"] for row in captures] != list(range(1, len(captures) + 1)):
        raise ValueError("Stage-C accepted progress is not a prefix")
    for summary, plan in zip(captures, prereg["plan"]):
        observed = validate_cell(plan, output_root / plan["output_name"], prereg, paths, runner_module)
        if observed != summary:
            raise ValueError(f"Stage-C accepted capture changed: {plan['run_ordinal']}")
    expected_dirs = {row["output_name"] for row in prereg["plan"][:len(captures)]}
    actual_dirs = {path.name for path in output_root.iterdir() if path.is_dir()}
    if actual_dirs != expected_dirs:
        raise ValueError("Stage-C output root contains an unowned or incomplete attempt")


def main() -> int:
    args = arguments()
    prereg_path = args.preregistration.resolve(strict=True)
    prereg, paths = validate_preregistration(prereg_path, args.expected_preregistration_sha256)
    active = active_k3_processes()
    if active:
        raise RuntimeError(f"K3/helper process already active: {active}")
    output_root = pathlib.Path(prereg["output"]["root"])
    progress_path = pathlib.Path(prereg["output"]["progress"])
    if args.verify_only:
        if output_root.exists() or progress_path.exists():
            raise ValueError("Stage-C output exists during pristine verify-only preflight")
        print(json.dumps({
            "status": "pass",
            "mode": "verify-only",
            "preregistration": identity(prereg_path),
            "plan_cells": len(prereg["plan"]),
            "unique_prompts": len({row["case_id"] for row in prereg["plan"]}),
            "active_k3_processes": 0,
            "stage_c_outcomes_inspected": 0,
            "disposition": "READY_FOR_SERIAL_STAGE_C",
        }, sort_keys=True))
        return 0

    runner_module = load_runner(paths["runner"])
    if progress_path.exists():
        progress = load_json(progress_path)
        verify_resume(progress, prereg_path, prereg, paths, runner_module, output_root)
    else:
        if output_root.exists():
            raise ValueError("Stage-C output root exists without owned progress")
        output_root.mkdir(parents=True)
        progress = initial_progress(prereg_path, prereg)
        write_json(progress_path, progress)

    start_index = len(progress["captures"])
    for plan in prereg["plan"][start_index:]:
        active = active_k3_processes()
        if active:
            raise RuntimeError(f"K3/helper process active before cell: {active}")
        command = [
            sys.executable, str(paths["runner"]),
            "--binary", str(paths["binary"]),
            "--model", str(paths["model"]),
            "--prompt-corpus", str(paths["corpus"]),
            "--case-id", plan["case_id"],
            "--protocol", "full-prompt",
            "--output-root", str(output_root),
            "--name", plan["output_name"],
            "--campaign", CAMPAIGN,
            "--run-ordinal", str(plan["run_ordinal"]),
            "--triplet", str(plan["prompt_ordinal"]),
            "--order", str(plan["pair_position"]),
            "--point", plan["point"],
            "--cold-cache-bytes", str(prereg["configuration"]["cache_bytes"]),
            "--project-sha", prereg["execution_project_sha"],
            "--nested-sha", prereg["nested_llama_cpp_sha"],
            "--model-identity", prereg["inputs"]["model_identity"]["sha256"],
            "--build-fingerprint", str(paths["build_fingerprint"]),
            "--decode-forwards", "64",
            "--threads", "32",
            "--n-ctx", "768",
        ]
        attempt = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        root = output_root / plan["output_name"]
        if attempt.returncode != 0:
            failure = {
                "run_ordinal": plan["run_ordinal"],
                "case_id": plan["case_id"],
                "point": plan["point"],
                "runner_exit_status": attempt.returncode,
                "runner_stdout": attempt.stdout,
                "runner_stderr": attempt.stderr,
                "root": str(root),
                "artifacts": {
                    path.name: identity(path) for path in root.iterdir() if path.is_file()
                } if root.is_dir() else {},
            }
            progress["status"] = "failed"
            progress["failed_cell_count"] += 1
            progress["failures"].append(failure)
            progress["next_run_ordinal"] = plan["run_ordinal"]
            progress["disposition"] = "STOP_PRESERVE_FAILURE_RETURN_TO_DESIGN"
            write_json(progress_path, progress)
            raise RuntimeError(f"Stage-C cell failed: {json.dumps(failure, sort_keys=True)}")
        capture = validate_cell(plan, root, prereg, paths, runner_module)
        progress["captures"].append(capture)
        progress["accepted_cell_count"] = len(progress["captures"])
        progress["next_run_ordinal"] = (
            plan["run_ordinal"] + 1 if plan["run_ordinal"] < 48 else None
        )
        if len(progress["captures"]) == 48:
            progress["status"] = "pass"
            progress["disposition"] = "STAGE_C_COMPLETE_READY_FOR_SYNTHESIS"
        else:
            progress["disposition"] = (
                f"READY_FOR_SERIAL_STAGE_C_CELL_{plan['run_ordinal'] + 1:03d}"
            )
        write_json(progress_path, progress)
        print(json.dumps({
            "status": "pass",
            "accepted": len(progress["captures"]),
            "expected": 48,
            "run_ordinal": plan["run_ordinal"],
            "case_id": plan["case_id"],
            "point": plan["point"],
            "result_sha256": capture["artifacts"]["result"]["sha256"],
        }, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

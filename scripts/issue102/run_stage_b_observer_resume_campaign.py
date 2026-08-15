#!/usr/bin/env python3
"""Resume the frozen observer campaign serially with per-attempt cache hygiene."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from typing import Any


EXPECTED_CONTROL_SCHEMA = "phase13-6pg-stage-b-observer-continuation-control-v4"
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
EXPECTED_V2_SHA256 = "1c96c86920e6f7312ce887783c7436eb2601aadf4ea622b47b3cd1b8d53ab701"
EXPECTED_HELPER_SHA256 = "a8cd60963c7da3ece8937ba83834435217ac2ec7922de15c50cd5a59743fb392"
EXPECTED_RUNNER_SHA256 = "0e09960035666f15bfc82cef2a8dd81358f744a848f3f1f633d27d420afeca92"
EXPECTED_CAPTURE_COUNT = 44


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=pathlib.Path, required=True)
    parser.add_argument("--expected-control-sha256", required=True)
    parser.add_argument("--preflight", type=pathlib.Path, required=True)
    parser.add_argument("--expected-preflight-sha256", required=True)
    parser.add_argument("--model", type=pathlib.Path, required=True)
    parser.add_argument("--project-sha", required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--control-root", type=pathlib.Path, required=True)
    parser.add_argument("--repo", type=pathlib.Path, default=pathlib.Path.cwd())
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
    value: dict[str, Any] = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if resolved.suffix == ".json":
        document = json.loads(resolved.read_text())
        if "schema_version" in document:
            value["schema_version"] = document["schema_version"]
    return value


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def git_revision(repo: pathlib.Path, *extra: str) -> str:
    return subprocess.check_output(("git", "-C", str(repo), *extra), text=True).strip()


def active_helper_pids(binary: pathlib.Path) -> list[int]:
    target = str(binary.resolve()).encode()
    active = []
    for cmdline in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            values = cmdline.read_bytes().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if target in values:
            active.append(int(cmdline.parent.name))
    return sorted(active)


def verify_identity(value: dict[str, Any], label: str) -> None:
    path = pathlib.Path(value["path"])
    if not path.is_file() or sha256(path) != value["sha256"]:
        raise ValueError(f"frozen {label} identity changed: {path}")


def verify_preflight(
    preflight_path: pathlib.Path,
    expected_preflight_sha256: str,
    control_path: pathlib.Path,
) -> None:
    if sha256(preflight_path) != expected_preflight_sha256:
        raise ValueError("continuation preflight identity changed")
    document = json.loads(preflight_path.read_text())
    if (
        document.get("schema_version")
        != "phase13-6pg-stage-b-observer-continuation-preflight-v1"
        or document.get("status") != "pass"
        or document.get("disposition") != "READY_FOR_SERIAL_CAPTURE_005"
        or document.get("inputs", {}).get("control", {}).get("sha256") != sha256(control_path)
        or document.get("recovery_gate", {}).get("resident_bytes_after") != 0
        or not document.get("recovery_gate", {}).get("no_payload_reread_or_rehash_after_recovery")
        or document.get("post_retry_k3_attempts") != 0
    ):
        raise ValueError("continuation preflight is not executable")
    for name, value in document["inputs"].items():
        verify_identity(value, f"preflight {name}")


def verify_environment(
    control_path: pathlib.Path,
    expected_control_sha256: str,
    control: dict[str, Any],
    repo: pathlib.Path,
    project_sha: str,
    model: pathlib.Path,
) -> tuple[dict[str, Any], pathlib.Path]:
    if sha256(control_path) != expected_control_sha256:
        raise ValueError("continuation control identity changed")
    if (
        control.get("schema_version") != EXPECTED_CONTROL_SCHEMA
        or control.get("status") != "frozen"
        or control.get("disposition") != "READY_FOR_SERIAL_CAPTURE_005_THROUGH_044"
    ):
        raise ValueError("continuation control is not executable")
    if git_revision(repo, "rev-parse", "HEAD") != project_sha:
        raise ValueError("project HEAD differs from the published execution identity")
    if git_revision(repo / "llama.cpp", "rev-parse", "HEAD") != EXPECTED_NESTED_SHA:
        raise ValueError("nested llama.cpp revision changed")

    inputs = control["inputs"]
    for name in (
        "resume_preregistration", "retry_checkpoint", "v2_preregistration",
        "initial_hygiene", "host_normalization", "hygiene_reference", "generator",
    ):
        verify_identity(inputs[name], name)
    for group in ("tools", "runtime_files"):
        for name, value in inputs[group].items():
            verify_identity(value, f"{group}.{name}")
    if inputs["v2_preregistration"]["sha256"] != EXPECTED_V2_SHA256:
        raise ValueError("V2 preregistration is not the frozen identity")
    v2_path = pathlib.Path(inputs["v2_preregistration"]["path"])
    v2 = json.loads(v2_path.read_text())
    for name, value in v2["inputs"].items():
        verify_identity(value, f"V2 input {name}")
    if (
        v2["runtime"]["nested_llama_cpp"] != EXPECTED_NESTED_SHA
        or v2["runtime"]["helper_binary"]["sha256"] != EXPECTED_HELPER_SHA256
        or v2["runtime"]["runner"]["sha256"] != EXPECTED_RUNNER_SHA256
    ):
        raise ValueError("frozen observer runtime identity changed")

    model_identity_path = pathlib.Path(v2["inputs"]["model_identity"]["path"])
    resolved_model = model.resolve(strict=True)
    model_entry = control["model_entry_shard"]
    if (
        resolved_model != pathlib.Path(model_entry["path"]).resolve()
        or resolved_model.stat().st_size != model_entry["bytes"]
        or model_entry["identity_manifest_sha256"] != sha256(model_identity_path)
        or not model_entry["content_hash_must_not_be_recomputed"]
    ):
        raise ValueError("model entry shard differs from the normalized frozen host identity")

    plans = control["remaining_plan"]
    if len(plans) != 40 or [row["ordinal"] for row in plans] != list(range(5, 45)):
        raise ValueError("remaining plan differs from the frozen ordinal 5..44 suffix")
    evidence_root = pathlib.Path(plans[0]["output_directory"]).resolve().parent
    expected_directories = {
        pathlib.Path(row["output_directory"]).resolve() for row in plans
    }
    for row in control["accepted_prefix"]["captures_001_003"]:
        expected_directories.add(pathlib.Path(row["artifacts"]["result"]["path"]).resolve().parent)
    expected_directories.add(
        pathlib.Path(control["accepted_prefix"]["capture_004_retry"]["output_directory"]).resolve()
    )
    expected_directories.add(
        pathlib.Path(control["accepted_prefix"]["original_capture_004_failure"]["output_directory"]).resolve()
    )
    expected_directories.add(
        pathlib.Path(v2["supersession"]["failed_attempt"]["output_directory"]).resolve()
    )
    observed = {path.resolve() for path in evidence_root.glob("run-*") if path.is_dir()}
    unexpected = sorted(observed - expected_directories)
    if unexpected:
        raise ValueError(f"unregistered observer attempt directories: {unexpected}")
    if len(observed) > 46:
        raise ValueError("maximum observer process-attempt budget exceeded")
    return v2, evidence_root


def retry_capture_summary(control: dict[str, Any]) -> dict[str, Any]:
    retry = control["accepted_prefix"]["capture_004_retry"]
    validation = retry["validation"]
    evidence = retry["evidence"]
    return {
        "ordinal": 4,
        "case_id": retry["case_id"],
        "selection_role": retry["selection_role"],
        "execution_project_sha": retry["execution_project_sha"],
        "prompt_tokens": validation["prompt_tokens"],
        "generated_tokens": validation["generated_tokens"],
        "observer_records": validation["observer_records"],
        "selected_occurrences": validation["selected_occurrences"],
        "candidate_occurrences": validation["candidate_occurrences"],
        "result": evidence["result.json"],
        "envelope": evidence["envelope.json"],
        "stdout": evidence["stdout.log"],
        "stderr": evidence["stderr.log"],
        "validation": validation,
        "hygiene": retry["hygiene"],
    }


def initial_progress(
    control_path: pathlib.Path,
    control: dict[str, Any],
    project_sha: str,
) -> dict[str, Any]:
    captures = list(control["accepted_prefix"]["captures_001_003"])
    captures.append(retry_capture_summary(control))
    return {
        "schema_version": "phase13-6pg-stage-b-observer-resume-progress-v4",
        "status": "in_progress",
        "disposition": "READY_FOR_CAPTURE_005",
        "provenance": "MEASURED_OBSERVER_NON_PERFORMANCE",
        "control": identity(control_path),
        "execution_project_sha": project_sha,
        "runtime_source_target": control["runtime"]["project_source_target"],
        "nested_llama_cpp": EXPECTED_NESTED_SHA,
        "helper_binary_sha256": EXPECTED_HELPER_SHA256,
        "runner_sha256": EXPECTED_RUNNER_SHA256,
        "accepted_capture_count": 4,
        "expected_capture_count": EXPECTED_CAPTURE_COUNT,
        "captures": captures,
        "pending_attempt": None,
        "process_attempts_after_amendment": 1,
        "retry_budget_remaining": 0,
        "performance_interpretation": "FORBIDDEN",
    }


def load_progress(
    progress_path: pathlib.Path,
    control_path: pathlib.Path,
    control: dict[str, Any],
    project_sha: str,
) -> dict[str, Any]:
    if not progress_path.exists():
        progress = initial_progress(control_path, control, project_sha)
        write_json(progress_path, progress)
        return progress
    progress = json.loads(progress_path.read_text())
    if (
        progress.get("schema_version") != "phase13-6pg-stage-b-observer-resume-progress-v4"
        or progress.get("execution_project_sha") != project_sha
        or progress.get("control", {}).get("sha256") != sha256(control_path)
        or progress.get("expected_capture_count") != EXPECTED_CAPTURE_COUNT
    ):
        raise ValueError("existing progress does not belong to this execution target")
    count = progress.get("accepted_capture_count")
    ordinals = [row["ordinal"] for row in progress.get("captures", [])]
    if count != len(ordinals) or ordinals != list(range(1, count + 1)):
        raise ValueError("existing progress is not a contiguous accepted prefix")
    if progress.get("status") == "failed":
        raise RuntimeError("campaign already stopped at an immutable failed attempt")
    return progress


def runner_command(
    plan: dict[str, Any],
    directory: pathlib.Path,
    control: dict[str, Any],
    v2: dict[str, Any],
    model: pathlib.Path,
    project_sha: str,
) -> tuple[str, ...]:
    configuration = control["configuration"]
    return (
        control["runtime"]["runner"]["path"],
        "--binary", control["runtime"]["helper_binary"]["path"],
        "--model", str(model),
        "--prompt-corpus", v2["inputs"]["corpus"]["path"],
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
        "--project-sha", project_sha,
        "--nested-sha", EXPECTED_NESTED_SHA,
        "--model-identity", v2["inputs"]["model_identity"]["path"],
        "--build-fingerprint", v2["inputs"]["build_fingerprint"]["path"],
        "--decode-forwards", str(configuration["decode_forwards"]),
        "--warmup-limit", "128",
        "--threads", str(configuration["threads"]),
        "--n-ctx", str(configuration["n_ctx"]),
    )


def validate_hygiene(path: pathlib.Path, allowlist_sha256: str) -> tuple[dict[str, Any], bool]:
    if not path.is_file():
        raise ValueError("hygiene tool did not preserve its output record")
    document = json.loads(path.read_text())
    passed = (
        document.get("status") == "pass"
        and document.get("inputs", {}).get("allowlist", {}).get("sha256") == allowlist_sha256
        and all(document.get("gate", {}).values())
        and document.get("files", {}).get("resident_bytes_after") == 0
        and document.get("files", {}).get("content_read_after_release") is False
        and document.get("operation", {}).get("model_or_runtime_file_touched") is False
    )
    return document, passed


def finish_attempt(
    progress: dict[str, Any],
    progress_path: pathlib.Path,
    plan: dict[str, Any],
    pending: dict[str, Any],
    hygiene_path: pathlib.Path,
    hygiene: dict[str, Any],
    hygiene_passed: bool,
) -> bool:
    success = (
        pending["process_exit_status"] == 0
        and pending.get("validation_status") == "pass"
        and hygiene_passed
    )
    pending["hygiene"] = identity(hygiene_path)
    pending["hygiene_status"] = hygiene.get("status", "missing")
    pending["released_resident_bytes"] = hygiene.get("files", {}).get("released_resident_bytes")
    pending["resident_bytes_after"] = hygiene.get("files", {}).get("resident_bytes_after")
    if not success:
        progress["status"] = "failed"
        progress["disposition"] = f"RETURN_TO_DESIGN_AFTER_CAPTURE_{plan['ordinal']:03d}"
        progress["pending_attempt"] = pending
        write_json(progress_path, progress)
        return False

    capture = dict(pending["validated_capture"])
    capture["validation_record"] = pending["validation_record"]
    capture["hygiene"] = {
        "allowlist": pending["allowlist"],
        "gate": pending["hygiene"],
        "released_resident_bytes": pending["released_resident_bytes"],
        "resident_bytes_after": pending["resident_bytes_after"],
        "content_read_after_release": False,
    }
    progress["captures"].append(capture)
    progress["accepted_capture_count"] = len(progress["captures"])
    progress["pending_attempt"] = None
    if progress["accepted_capture_count"] == EXPECTED_CAPTURE_COUNT:
        progress["status"] = "pass"
        progress["disposition"] = "OBSERVER_CAMPAIGN_COMPLETE_READY_FOR_SYNTHESIS"
    else:
        progress["disposition"] = f"READY_FOR_CAPTURE_{plan['ordinal'] + 1:03d}"
    write_json(progress_path, progress)
    return True


def main() -> int:
    args = arguments()
    repo = args.repo.resolve()
    control_path = args.control.resolve(strict=True)
    preflight_path = args.preflight.resolve(strict=True)
    model = args.model.resolve(strict=True)
    progress_path = args.progress.resolve()
    control_root = args.control_root.resolve()
    control_root.mkdir(parents=True, exist_ok=True)
    control = json.loads(control_path.read_text())
    verify_preflight(
        preflight_path, args.expected_preflight_sha256, control_path,
    )
    v2, evidence_root = verify_environment(
        control_path, args.expected_control_sha256, control, repo, args.project_sha, model,
    )
    if args.verify_only:
        print(json.dumps({
            "status": "pass",
            "disposition": "VERIFIED_WITHOUT_K3_EXECUTION",
            "control": identity(control_path),
            "project_sha": args.project_sha,
            "next_ordinal": 5,
            "remaining_capture_count": len(control["remaining_plan"]),
        }, sort_keys=True), flush=True)
        return 0
    progress = load_progress(progress_path, control_path, control, args.project_sha)
    if progress["status"] == "pass":
        print(json.dumps({
            "status": "pass",
            "accepted_capture_count": progress["accepted_capture_count"],
            "progress": identity(progress_path),
        }, sort_keys=True), flush=True)
        return 0

    binary = pathlib.Path(control["runtime"]["helper_binary"]["path"]).resolve()
    validator = pathlib.Path(control["inputs"]["tools"]["capture_validator"]["path"])
    allowlist_builder = pathlib.Path(control["inputs"]["tools"]["allowlist_builder"]["path"])
    hygiene_tool = pathlib.Path(control["inputs"]["tools"]["hygiene_tool"]["path"])
    hygiene_reference = pathlib.Path(control["inputs"]["hygiene_reference"]["path"])
    resume_path = pathlib.Path(control["inputs"]["resume_preregistration"]["path"])

    for plan in control["remaining_plan"]:
        ordinal = plan["ordinal"]
        if ordinal <= progress["accepted_capture_count"]:
            print(json.dumps({
                "status": "accepted_existing_without_payload_reread",
                "ordinal": ordinal,
                "case_id": plan["case_id"],
            }, sort_keys=True), flush=True)
            continue
        if ordinal != progress["accepted_capture_count"] + 1:
            raise ValueError("next plan ordinal is not contiguous with accepted progress")
        verify_environment(
            control_path, args.expected_control_sha256, control, repo, args.project_sha, model,
        )
        active = active_helper_pids(binary)
        if active:
            raise RuntimeError(f"observer helper already active: {active}")

        directory = pathlib.Path(plan["output_directory"]).resolve()
        validation_path = control_root / f"validation-run-{ordinal:03d}.json"
        allowlist_path = control_root / f"allowlist-run-{ordinal:03d}.json"
        hygiene_path = control_root / f"hygiene-run-{ordinal:03d}.json"
        pending = progress.get("pending_attempt")
        if pending is not None and pending.get("ordinal") != ordinal:
            raise ValueError("pending attempt is not the next frozen ordinal")

        if pending is None:
            if directory.exists():
                envelope_path = directory / "envelope.json"
                if not envelope_path.is_file():
                    raise RuntimeError(f"existing attempt is incomplete and immutable: {directory}")
                process_exit_status = json.loads(envelope_path.read_text())["exit_status"]
                print(json.dumps({
                    "status": "recovering_completed_attempt_before_hygiene",
                    "ordinal": ordinal,
                    "case_id": plan["case_id"],
                    "process_exit_status": process_exit_status,
                }, sort_keys=True), flush=True)
            else:
                print(json.dumps({
                    "status": "starting",
                    "ordinal": ordinal,
                    "case_id": plan["case_id"],
                }, sort_keys=True), flush=True)
                completed = subprocess.run(
                    runner_command(plan, directory, control, v2, model, args.project_sha),
                    check=False,
                )
                process_exit_status = completed.returncode
                progress["process_attempts_after_amendment"] += 1

            validation_status = "not_available"
            validated_capture = None
            validation_record = None
            validation_error = None
            if process_exit_status == 0:
                validated = subprocess.run((
                    sys.executable, str(validator),
                    "--resume-preregistration", str(resume_path),
                    "--ordinal", str(ordinal),
                    "--directory", str(directory),
                    "--project-sha", args.project_sha,
                    "--output", str(validation_path),
                ), check=False, text=True, capture_output=True)
                if validated.returncode == 0 and validation_path.is_file():
                    validation_document = json.loads(validation_path.read_text())
                    validation_status = validation_document["status"]
                    validated_capture = validation_document["capture"]
                    validation_record = identity(validation_path)
                else:
                    validation_status = "fail"
                    validation_error = (validated.stderr or validated.stdout).strip()[-4000:]
            pending = {
                "ordinal": ordinal,
                "case_id": plan["case_id"],
                "directory": str(directory),
                "process_exit_status": process_exit_status,
                "validation_status": validation_status,
                "validated_capture": validated_capture,
                "validation_record": validation_record,
                "validation_error": validation_error,
                "phase": "VALIDATED_PENDING_ALLOWLIST_AND_HYGIENE",
            }
            progress["pending_attempt"] = pending
            progress["disposition"] = f"CAPTURE_{ordinal:03d}_PENDING_EXACT_FILE_HYGIENE"
            write_json(progress_path, progress)

        if hygiene_path.is_file():
            if not pending.get("allowlist"):
                raise ValueError("hygiene record exists without a frozen allowlist identity")
            hygiene, hygiene_passed = validate_hygiene(
                hygiene_path, pending["allowlist"]["sha256"],
            )
            if not finish_attempt(
                progress, progress_path, plan, pending, hygiene_path, hygiene, hygiene_passed,
            ):
                return 1
            print(json.dumps({
                "status": "accepted_recovered_after_hygiene",
                "ordinal": ordinal,
                "case_id": plan["case_id"],
                "accepted_capture_count": progress["accepted_capture_count"],
            }, sort_keys=True), flush=True)
            continue

        if not pending.get("allowlist"):
            allowlist_command = [
                sys.executable, str(allowlist_builder),
                "--capture-root", str(directory),
                "--evidence-root", str(evidence_root),
                "--ordinal", str(ordinal),
                "--case-id", plan["case_id"],
                "--process-exit-status", str(pending["process_exit_status"]),
                "--output", str(allowlist_path),
            ]
            if pending.get("validation_record") is not None:
                allowlist_command.extend((
                    "--validation-record", pending["validation_record"]["path"],
                ))
            frozen = subprocess.run(allowlist_command, check=False)
            if frozen.returncode != 0 or not allowlist_path.is_file():
                progress["status"] = "failed"
                progress["disposition"] = f"RETURN_TO_DESIGN_ALLOWLIST_FAILURE_{ordinal:03d}"
                write_json(progress_path, progress)
                return 1
            pending["allowlist"] = identity(allowlist_path)
            pending["phase"] = "ALLOWLIST_FROZEN_PENDING_HYGIENE"
            progress["pending_attempt"] = pending
            write_json(progress_path, progress)

        released = subprocess.run((
            sys.executable, str(hygiene_tool),
            "--allowlist", str(allowlist_path),
            "--expected-allowlist-sha256", pending["allowlist"]["sha256"],
            "--reference-preflight", str(hygiene_reference),
            "--output", str(hygiene_path),
        ), check=False)
        hygiene, hygiene_passed = validate_hygiene(
            hygiene_path, pending["allowlist"]["sha256"],
        )
        hygiene_passed = hygiene_passed and released.returncode == 0
        if not finish_attempt(
            progress, progress_path, plan, pending, hygiene_path, hygiene, hygiene_passed,
        ):
            print(json.dumps({
                "status": "failed_preserved_after_hygiene",
                "ordinal": ordinal,
                "case_id": plan["case_id"],
                "process_exit_status": pending["process_exit_status"],
                "validation_status": pending["validation_status"],
                "hygiene_status": hygiene.get("status"),
            }, sort_keys=True), flush=True)
            return 1
        print(json.dumps({
            "status": "accepted",
            "ordinal": ordinal,
            "case_id": plan["case_id"],
            "accepted_capture_count": progress["accepted_capture_count"],
            "result_sha256": pending["validated_capture"]["result"]["sha256"],
            "hygiene_sha256": pending["hygiene"]["sha256"],
        }, sort_keys=True), flush=True)

    print(json.dumps({
        "status": progress["status"],
        "accepted_capture_count": progress["accepted_capture_count"],
        "progress": identity(progress_path),
    }, sort_keys=True), flush=True)
    return 0 if progress["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

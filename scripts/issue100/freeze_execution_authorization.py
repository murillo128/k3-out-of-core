#!/usr/bin/env python3
"""Freeze the published AUTO amendment and exact-target conformance for execution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from protocol import (
    AUTO_ADMISSION_SHA256, AUTO_CACHE_REQUEST_BYTES, CAMPAIGN_SHA256,
    CAPACITY_FLOOR_BYTES, CAPACITY_FLOOR_SLOTS, MODEL_MANIFEST_SHA256,
    EXPERT_BUNDLE_BYTES, MEMLOCK_LIMIT_BYTES, NESTED_BASELINE, PREREGISTRATION_SHA256,
    PREVIOUS_BINARY_SHA256, PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
    PREVIOUS_NESTED_COMMIT, PREVIOUS_PROJECT_COMMIT, PUBLIC_AUTO_ADMISSION,
    RECOVERY_ATTEMPT_FIRST, RECOVERY_ATTEMPT_LAST, RECOVERY_EPOCH,
    RECOVERY_RUN_ORDINAL,
    atomic_json, bind_checksum, load_json, repository_identity, sha256_bytes,
    sha256_file, process_entry_failures, transport_diagnostic_failures,
    transport_teardown_failures, validate_checksum,
)


class AuthorizationError(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--preregistration", type=Path, required=True)
    parser.add_argument("--protected-plan", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--conformance", type=Path, required=True)
    parser.add_argument("--previous-execution-authorization", type=Path, required=True)
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--recovery-amendment-url", required=True)
    parser.add_argument("--recovery-amendment-sha256", required=True)
    parser.add_argument("--independent-review-url", required=True)
    parser.add_argument("--independent-review-sha256", required=True)
    parser.add_argument("--reboot-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve(strict=True)
    repository = repository_identity(repo_root)
    if repository["nested_commit"] != NESTED_BASELINE or repository["worktree_porcelain"]:
        raise AuthorizationError("authorization requires frozen nested commit and clean worktree")
    if sha256_file(args.preregistration.resolve(strict=True)) != PREREGISTRATION_SHA256:
        raise AuthorizationError("preregistration identity mismatch")
    if sha256_file((repo_root / PUBLIC_AUTO_ADMISSION).resolve(strict=True)) != AUTO_ADMISSION_SHA256:
        raise AuthorizationError("AUTO admission amendment identity mismatch")
    protected_plan = load_json(args.protected_plan.resolve(strict=True))
    if protected_plan.get("campaign_sha256") != CAMPAIGN_SHA256:
        raise AuthorizationError("protected campaign identity mismatch")
    conformance_path = args.conformance.resolve(strict=True)
    conformance = load_json(conformance_path)
    capacity = conformance.get("capacity", {})
    if conformance.get("schema_version") != "issue100-non-scored-conformance-v4" or \
            conformance.get("status") != "pass" or conformance.get("outcome_inspected") or \
            conformance.get("gpqa_item_used") or \
            conformance.get("successful_path_equivalence") != "PASS" or \
            capacity.get("request_mode") != "AUTO" or \
            capacity.get("request_bytes") != AUTO_CACHE_REQUEST_BYTES or \
            capacity.get("floor_slots") != CAPACITY_FLOOR_SLOTS or \
            capacity.get("floor_bytes") != CAPACITY_FLOOR_BYTES or \
            conformance.get("auto_admission", {}).get("sha256") != AUTO_ADMISSION_SHA256 or \
            conformance.get("recovery", {}).get("memlock_limit_bytes") != MEMLOCK_LIMIT_BYTES or \
            conformance.get("repository", {}).get("project_commit") != repository["project_commit"] or \
            conformance.get("repository", {}).get("nested_commit") != NESTED_BASELINE:
        raise AuthorizationError("non-scored conformance evidence does not bind current target")
    for arm in ("exact", "s2"):
        slots = capacity.get(f"{arm}_auto_slots")
        if not isinstance(slots, int) or slots < CAPACITY_FLOOR_SLOTS or \
                capacity.get(f"{arm}_auto_bytes") != slots*EXPERT_BUNDLE_BYTES:
            raise AuthorizationError(f"non-scored {arm} AUTO capacity is invalid")
    if capacity.get("auto_slot_delta") != \
            capacity["s2_auto_slots"] - capacity["exact_auto_slots"]:
        raise AuthorizationError("non-scored AUTO capacity delta is invalid")
    for arm in ("exact", "s2"):
        entry = conformance.get("process_entry", {}).get(arm, {})
        transport = conformance.get("transport", {}).get(arm, {})
        failures = process_entry_failures(entry)
        failures.extend(transport_diagnostic_failures(transport))
        failures.extend(transport_teardown_failures(
            transport,
            {"delta": {"vmstat": {
                "nr_foll_pin_acquired": transport.get("nr_foll_pin_acquired"),
                "nr_foll_pin_released": transport.get("nr_foll_pin_released"),
            }}},
            entry,
        ))
        if transport.get("storage_io_errors") != 0:
            failures.append("storage I/O errors")
        if entry.get("boot_id") != conformance.get("recovery", {}).get("boot_id"):
            failures.append("boot identity")
        if failures:
            raise AuthorizationError(
                f"non-scored {arm} recovery envelope is invalid: " + "; ".join(failures)
            )
    previous_authorization_path = args.previous_execution_authorization.resolve(strict=True)
    if sha256_file(previous_authorization_path) != PREVIOUS_EXECUTION_AUTHORIZATION_SHA256:
        raise AuthorizationError("previous execution authorization identity mismatch")
    previous_authorization = load_json(previous_authorization_path)
    validate_checksum(previous_authorization)
    if previous_authorization.get("schema_version") != "issue100-execution-authorization-v2" or \
            previous_authorization.get("project_commit") != PREVIOUS_PROJECT_COMMIT or \
            previous_authorization.get("nested_commit") != PREVIOUS_NESTED_COMMIT or \
            previous_authorization.get("binary_sha256") != PREVIOUS_BINARY_SHA256:
        raise AuthorizationError("previous execution authorization target mismatch")

    campaign_root = args.campaign_root.resolve(strict=True)
    runs_path = campaign_root / "runs.jsonl"
    lines = runs_path.read_bytes().splitlines(keepends=True)
    if len(lines) != 1:
        raise AuthorizationError("recovery authorization requires exactly one accepted run")
    accepted_run = json.loads(lines[0])
    validate_checksum(accepted_run)
    previous_identity = {
        "campaign_sha256": CAMPAIGN_SHA256,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "project_commit": PREVIOUS_PROJECT_COMMIT,
        "nested_commit": PREVIOUS_NESTED_COMMIT,
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "adapter_binary_sha256": PREVIOUS_BINARY_SHA256,
        "protected_plan_sha256": sha256_file(args.protected_plan),
        "execution_authorization_sha256": PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
        "auto_admission_sha256": AUTO_ADMISSION_SHA256,
        "capacity_request_mode": "AUTO",
        "capacity_request_bytes": AUTO_CACHE_REQUEST_BYTES,
        "capacity_floor_slots": CAPACITY_FLOOR_SLOTS,
        "capacity_floor_bytes": CAPACITY_FLOOR_BYTES,
        "scoring_identity": "issue100-response-boundary-first-regex-v1",
    }
    if accepted_run.get("run_ordinal") != 1 or any(
            accepted_run.get(key) != expected for key, expected in previous_identity.items()):
        raise AuthorizationError("accepted run-1 recovery identity mismatch")
    accepted_manifest = Path(accepted_run["attempt_manifest_path"])
    if not accepted_manifest.is_file() or \
            sha256_file(accepted_manifest) != accepted_run.get("attempt_manifest_sha256"):
        raise AuthorizationError("accepted run-1 attempt manifest drift")

    control_path = campaign_root / "campaign-control.json"
    control = load_json(control_path)
    if control.get("status") != "halted" or control.get("failed_run") != RECOVERY_RUN_ORDINAL or \
            control.get("failed_attempt") != RECOVERY_ATTEMPT_FIRST - 1 or any(
                control.get(key) != expected for key, expected in previous_identity.items()):
        raise AuthorizationError("pre-recovery campaign-control state drift")
    previous_campaign_control_sha256 = sha256_file(control_path)

    run_roots = list((campaign_root / "attempts").glob(f"run-{RECOVERY_RUN_ORDINAL:03d}-*"))
    if len(run_roots) != 1:
        raise AuthorizationError("run-2 recovery lineage root is ambiguous")
    attempt_hashes = []
    for attempt_ordinal in range(1, RECOVERY_ATTEMPT_FIRST):
        manifest_path = run_roots[0] / f"attempt-{attempt_ordinal:02d}" / "attempt-manifest.json"
        manifest = load_json(manifest_path)
        if manifest.get("run_ordinal") != RECOVERY_RUN_ORDINAL or \
                manifest.get("attempt_ordinal") != attempt_ordinal or manifest.get("accepted"):
            raise AuthorizationError("run-2 prior-attempt identity drift")
        attempt_hashes.append(sha256_file(manifest_path))
    attempt_lineage_sha256 = sha256_bytes(("\n".join(attempt_hashes) + "\n").encode("ascii"))

    for name, digest in (
        ("recovery amendment", args.recovery_amendment_sha256),
        ("independent review", args.independent_review_sha256),
    ):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise AuthorizationError(f"{name} SHA-256 is invalid")
    clean_reboot_used = args.reboot_evidence is not None
    reboot_evidence_sha256 = None
    if clean_reboot_used:
        reboot_path = args.reboot_evidence.resolve(strict=True)
        reboot = load_json(reboot_path)
        if reboot.get("schema_version") != "issue100-recovery-reboot-v1" or \
                reboot.get("status") != "pass" or \
                reboot.get("before", {}).get("boot_id") == reboot.get("after", {}).get("boot_id") or \
                conformance.get("recovery", {}).get("boot_id") != reboot.get("after", {}).get("boot_id"):
            raise AuthorizationError("reboot evidence does not bind conformance boot")
        reboot_evidence_sha256 = sha256_file(reboot_path)
    value = bind_checksum({
        "schema_version": "issue100-execution-authorization-v4",
        "verdict": "PASS",
        "safe_to_start_scored_inference": True,
        "serves_as_final_review": False,
        "project_commit": repository["project_commit"],
        "nested_commit": NESTED_BASELINE,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "campaign_sha256": CAMPAIGN_SHA256,
        "protected_plan_sha256": sha256_file(args.protected_plan),
        "binary_sha256": sha256_file(args.binary.resolve(strict=True)),
        "model_manifest_sha256": MODEL_MANIFEST_SHA256,
        "auto_admission_sha256": AUTO_ADMISSION_SHA256,
        "capacity_request_mode": "AUTO",
        "capacity_request_bytes": AUTO_CACHE_REQUEST_BYTES,
        "capacity_floor_slots": CAPACITY_FLOOR_SLOTS,
        "capacity_floor_bytes": CAPACITY_FLOOR_BYTES,
        "memlock_limit_bytes": MEMLOCK_LIMIT_BYTES,
        "non_scored_conformance": "PASS",
        "non_scored_conformance_sha256": sha256_file(conformance_path),
        "successful_path_equivalence": "PASS",
        "previous_execution_authorization_sha256": PREVIOUS_EXECUTION_AUTHORIZATION_SHA256,
        "previous_campaign_control_sha256": previous_campaign_control_sha256,
        "previous_identity": previous_identity,
        "accepted_prefix_runs": 1,
        "accepted_prefix_sha256": sha256_bytes(lines[0]),
        "prior_attempt_manifest_sha256s": attempt_hashes,
        "attempt_lineage_sha256": attempt_lineage_sha256,
        "recovery_epoch": RECOVERY_EPOCH,
        "recovery_run_ordinal": RECOVERY_RUN_ORDINAL,
        "recovery_attempt_first": RECOVERY_ATTEMPT_FIRST,
        "recovery_attempt_last": RECOVERY_ATTEMPT_LAST,
        "clean_reboot_used": clean_reboot_used,
        "reboot_evidence_sha256": reboot_evidence_sha256,
        "recovery_amendment_url": args.recovery_amendment_url,
        "recovery_amendment_sha256": args.recovery_amendment_sha256,
        "independent_review_url": args.independent_review_url,
        "independent_review_sha256": args.independent_review_sha256,
    })
    atomic_json(args.output, value)
    print(
        f"ISSUE100_EXECUTION_AUTHORIZATION status=PASS project={repository['project_commit']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 authorization: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

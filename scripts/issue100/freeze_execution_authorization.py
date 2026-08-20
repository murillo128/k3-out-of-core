#!/usr/bin/env python3
"""Freeze the published AUTO amendment and exact-target conformance for execution."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from protocol import (
    AUTO_ADMISSION_SHA256, AUTO_CACHE_REQUEST_BYTES, CAMPAIGN_SHA256,
    CAPACITY_FLOOR_BYTES, CAPACITY_FLOOR_SLOTS, MODEL_MANIFEST_SHA256,
    EXPERT_BUNDLE_BYTES, NESTED_BASELINE, PREREGISTRATION_SHA256,
    PUBLIC_AUTO_ADMISSION,
    atomic_json, bind_checksum, load_json, repository_identity, sha256_file,
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
    parser.add_argument("--execution-amendment-url", required=True)
    parser.add_argument("--execution-amendment-sha256", required=True)
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
    if conformance.get("schema_version") != "issue100-non-scored-conformance-v2" or \
            conformance.get("status") != "pass" or conformance.get("outcome_inspected") or \
            conformance.get("gpqa_item_used") or \
            capacity.get("request_mode") != "AUTO" or \
            capacity.get("request_bytes") != AUTO_CACHE_REQUEST_BYTES or \
            capacity.get("floor_slots") != CAPACITY_FLOOR_SLOTS or \
            capacity.get("floor_bytes") != CAPACITY_FLOOR_BYTES or \
            conformance.get("auto_admission", {}).get("sha256") != AUTO_ADMISSION_SHA256 or \
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
    if not re.fullmatch(r"[0-9a-fA-F]{64}", args.execution_amendment_sha256):
        raise AuthorizationError("execution amendment SHA-256 is invalid")
    value = bind_checksum({
        "schema_version": "issue100-execution-authorization-v2",
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
        "non_scored_conformance": "PASS",
        "non_scored_conformance_sha256": sha256_file(conformance_path),
        "execution_amendment_url": args.execution_amendment_url,
        "execution_amendment_sha256": args.execution_amendment_sha256,
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

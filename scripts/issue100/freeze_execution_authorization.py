#!/usr/bin/env python3
"""Freeze the published independent pre-execution PASS consumed by the runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from protocol import (
    CACHE_BYTES, CACHE_SLOTS, CAMPAIGN_SHA256, MODEL_MANIFEST_SHA256,
    NESTED_BASELINE, PREREGISTRATION_SHA256, atomic_json, bind_checksum,
    load_json, repository_identity, sha256_file,
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
    parser.add_argument("--review-comment-url", required=True)
    parser.add_argument("--review-verdict-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve(strict=True)
    repository = repository_identity(repo_root)
    if repository["nested_commit"] != NESTED_BASELINE or repository["worktree_porcelain"]:
        raise AuthorizationError("authorization requires frozen nested commit and clean worktree")
    if sha256_file(args.preregistration.resolve(strict=True)) != PREREGISTRATION_SHA256:
        raise AuthorizationError("preregistration identity mismatch")
    protected_plan = load_json(args.protected_plan.resolve(strict=True))
    if protected_plan.get("campaign_sha256") != CAMPAIGN_SHA256:
        raise AuthorizationError("protected campaign identity mismatch")
    conformance_path = args.conformance.resolve(strict=True)
    conformance = load_json(conformance_path)
    if conformance.get("schema_version") != "issue100-non-scored-conformance-v1" or \
            conformance.get("status") != "pass" or conformance.get("outcome_inspected") or \
            conformance.get("gpqa_item_used") or \
            conformance.get("repository", {}).get("project_commit") != repository["project_commit"] or \
            conformance.get("repository", {}).get("nested_commit") != NESTED_BASELINE:
        raise AuthorizationError("non-scored conformance evidence does not bind current target")
    if len(args.review_verdict_sha256) != 64:
        raise AuthorizationError("independent review verdict SHA-256 is invalid")
    value = bind_checksum({
        "schema_version": "issue100-execution-authorization-v1",
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
        "cache_slots": CACHE_SLOTS,
        "cache_bytes": CACHE_BYTES,
        "non_scored_conformance": "PASS",
        "non_scored_conformance_sha256": sha256_file(conformance_path),
        "review_comment_url": args.review_comment_url,
        "review_verdict_sha256": args.review_verdict_sha256,
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

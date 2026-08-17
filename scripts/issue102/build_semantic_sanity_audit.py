#!/usr/bin/env python3
"""Build the issue-102 post-Stage-A semantic-sanity audit artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
from typing import Any


LABELS = (
    "COHERENT_RELEVANT",
    "COHERENT_BUT_WRONG_OR_INCOMPLETE",
    "PARTIALLY_COHERENT",
    "REPETITIVE_OR_DEGENERATE",
    "UNRELATED",
    "GIBBERISH_OR_TOKEN_SOUP",
    "SPECIAL_TOKEN_OR_FORMAT_FAILURE",
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detokenized", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--generated-utc", required=True)
    parser.add_argument("--uniform-reviewed-label", choices=LABELS, required=True)
    parser.add_argument("--review-project-sha", required=True)
    return parser.parse_args()


def load_json(path: pathlib.Path) -> Any:
    with path.open() as source:
        return json.load(source)


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    args = arguments()
    source_path = pathlib.Path(args.detokenized).resolve()
    output_path = pathlib.Path(args.output).resolve()
    source = load_json(source_path)
    cases = source.get("cases", [])
    if (
        source.get("schema_version") != "issue102-semantic-sanity-detokenized-v1"
        or source.get("status") != "pass"
        or source.get("case_count") != 128
        or len(cases) != 128
    ):
        raise ValueError("detokenized source identity/count mismatch")
    ids = [row.get("case_id") for row in cases]
    if len(set(ids)) != 128 or any(not value for value in ids):
        raise ValueError("case IDs are missing or duplicated")

    required = {
        "case_id", "semantic_family", "length_level", "corpus_reference",
        "source_result_path", "templated_prompt_tokens", "generated_token_count",
        "generated_token_hash", "generated_ids", "detokenized_generated_text",
        "special_control_token_observations",
    }
    reviewed_cases: list[dict[str, Any]] = []
    for row in cases:
        if not required.issubset(row):
            raise ValueError(f"missing required fields for {row.get('case_id')}")
        if (
            row["generated_token_count"] != 64
            or len(row["generated_ids"]) != 64
            or not row["detokenized_generated_text"]
        ):
            raise ValueError(f"invalid frozen output for {row['case_id']}")
        reviewed = dict(row)
        reviewed["sanity_label"] = args.uniform_reviewed_label
        reviewed["sanity_reason"] = None
        reviewed_cases.append(reviewed)

    counts = {label: 0 for label in LABELS}
    by_family: dict[str, dict[str, int]] = {}
    for row in reviewed_cases:
        label = row["sanity_label"]
        counts[label] += 1
        family = row["semantic_family"]
        by_family.setdefault(family, {item: 0 for item in LABELS})[label] += 1
    fractions = {label: counts[label] / len(reviewed_cases) for label in LABELS}
    screening = [row["case_id"] for row in reviewed_cases if row["length_level"] == 1]
    screening += [row["case_id"] for row in reviewed_cases if row["length_level"] == 6]
    if len(screening) != 32:
        raise ValueError("preregistered screening set is not exactly 32 cases")

    adverse_labels = set(LABELS[2:])
    adverse = [row["case_id"] for row in reviewed_cases if row["sanity_label"] in adverse_labels]
    token_soup = [
        row["case_id"] for row in reviewed_cases
        if row["sanity_label"] == "GIBBERISH_OR_TOKEN_SOUP"
    ]
    unrelated = [
        row["case_id"] for row in reviewed_cases
        if row["sanity_label"] == "UNRELATED"
    ]
    format_failures = [
        row["case_id"] for row in reviewed_cases
        if row["sanity_label"] == "SPECIAL_TOKEN_OR_FORMAT_FAILURE"
    ]
    conclusion = (
        "NO_CATASTROPHIC_GENERATION_FAILURE_OBSERVED" if not adverse else
        "CATASTROPHIC_GENERATION_FAILURE_REQUIRES_INVESTIGATION"
    )

    artifact = {
        "schema_version": "issue102-semantic-sanity-audit-v1",
        "status": "pass" if not adverse else "finding",
        "generated_utc": args.generated_utc,
        "provenance": {
            **source["provenance"],
            "review_project_sha": args.review_project_sha,
            "detokenized_artifact_path": str(source_path),
            "detokenized_artifact_sha256": sha256(source_path),
            "review_mode": "human inspection in preregistered screening order, then full frozen order",
            "classification_labels": list(LABELS),
        },
        "inspection": {
            "screening_order": screening,
            "screening_case_count": len(screening),
            "full_order": ids,
            "full_case_count": len(ids),
            "uniform_reviewed_label": args.uniform_reviewed_label,
        },
        "summary": {
            "counts_by_label": counts,
            "fractions_by_label": fractions,
            "counts_by_semantic_family": by_family,
            "systematic_language_or_format_degeneration_observed": bool(adverse),
            "adverse_cases": adverse,
            "token_soup_cases": token_soup,
            "unrelated_generation_cases": unrelated,
            "special_token_or_format_failure_cases": format_failures,
            "conclusion": conclusion,
            "limits": (
                "This 64-token think-channel audit is diagnostic only; it does not establish "
                "semantic equivalence, benchmark quality, task correctness, or quality neutrality."
            ),
        },
        "cases": reviewed_cases,
    }
    write_json(output_path, artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

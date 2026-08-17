#!/usr/bin/env python3
"""Reconstruct the exact issue-102 owner-preregistered candidate from comments."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


VERSION = "owner-preregistered-candidate-v2"
CANONICALIZATION = "UTF-8 JSON, ensure_ascii=false, separators=(',',':'), keys in insertion order"
EXPECTED_SHA256 = "3535638264d920b025e8c99caedf2197a73f3a7d4a274d865bd7f4defbdf3ef6"
CASE_HEADING = re.compile(r"^### `(?P<id>\d{2}-[a-z]+-b[1-8])`")


def payloads(body: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    current_id: str | None = None
    lines = body.splitlines()
    index = 0
    while index < len(lines):
        match = CASE_HEADING.match(lines[index])
        if match:
            current_id = match.group("id")
        elif lines[index] == "```text" and current_id is not None:
            end = index + 1
            while end < len(lines) and lines[end] != "```":
                end += 1
            if end == len(lines):
                raise RuntimeError(f"unterminated payload block for {current_id}")
            result.append((current_id, "\n".join(lines[index + 1:end])))
            index = end
        index += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comments", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--provenance-output", required=True, type=Path)
    args = parser.parse_args()

    comments = json.loads(args.comments.read_text(encoding="utf-8"))
    fragments: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, list[int]] = defaultdict(list)
    for comment in sorted(comments, key=lambda value: (value["created_at"], value["id"])):
        for case_id, prompt_fragment in payloads(comment["body"]):
            if case_id == "13-creative-b8" and comment["id"] not in {
                5280532398, 5280534239, 5280555804, 5280557891,
            }:
                continue
            fragments[case_id].append(prompt_fragment)
            sources[case_id].append(comment["id"])

    cases = []
    for family_index in range(1, 17):
        matching = sorted(
            (case_id for case_id in fragments if int(case_id[:2]) == family_index),
            key=lambda case_id: int(case_id.rsplit("b", 1)[1]),
        )
        if len(matching) != 8:
            raise RuntimeError(f"family {family_index:02d} has {len(matching)} cases: {matching}")
        for band, case_id in enumerate(matching, start=1):
            observed_band = int(case_id.rsplit("b", 1)[1])
            if observed_band != band:
                raise RuntimeError(f"unexpected band order for {case_id}")
            family = case_id.split("-", 2)[1]
            cases.append({
                "id": case_id,
                "family": family,
                "band": band,
                "prompt": " ".join(fragments[case_id]),
            })

    candidate = {
        "version": VERSION,
        "canonicalization": CANONICALIZATION,
        "cases": cases,
    }
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    observed_sha256 = hashlib.sha256(encoded).hexdigest()
    if observed_sha256 != EXPECTED_SHA256:
        raise RuntimeError(
            f"owner candidate SHA-256 mismatch: {observed_sha256} != {EXPECTED_SHA256}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(encoded + b"\n")
    provenance = {
        "schema_version": "issue102-owner-corpus-comment-provenance-v1",
        "candidate_sha256": observed_sha256,
        "comments_source_sha256": hashlib.sha256(args.comments.read_bytes()).hexdigest(),
        "case_comment_ids": sources,
    }
    args.provenance_output.parent.mkdir(parents=True, exist_ok=True)
    args.provenance_output.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"cases": len(cases), "sha256": observed_sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

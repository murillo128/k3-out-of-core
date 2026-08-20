#!/usr/bin/env python3
"""Materialize the protected issue #100 GPQA inputs from pinned sources."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import random
import sys
import zipfile
from pathlib import Path

from protocol import (
    CAMPAIGN_SHA256, DATASET_PASSWORD, DATASET_ZIP_SHA256, DIAMOND_CSV_SHA256,
    EXACT30_SELECTION_SHA256, ITEM_UNIVERSE_SHA256, K3_WRAPPER_SHA256,
    LICENSE_SHA256, QUERY_TEMPLATE_SHA256, atomic_json, canonical_json_bytes,
    file_identity, generation_seed, load_json, sha256_bytes, sha256_file,
)


QUERY_TEMPLATE = """Answer the following multiple choice question. The last line of your response should be of the following format: 'Answer: $LETTER' (without quotes) where LETTER is one of ABCD. Think step by step before answering.

{Question}

A) {A}
B) {B}
C) {C}
D) {D}"""

K3_PREFIX = (
    '<|open|>message role="system" type="thinking-effort"<|sep|>'
    '`thinking_effort` guides on how much to think in your thinking channel '
    '(not including the response channel), supported values include `low`, '
    '`medium`, `high`, and `max`.\nNow the system is invoked with '
    '`thinking_effort=max`.<|close|>message<|sep|><|end_of_msg|>'
    '<|open|>message role="user"<|sep|>'
)
K3_SUFFIX = (
    '<|close|>message<|sep|><|end_of_msg|><|open|>message '
    'role="assistant"<|sep|><|open|>think<|sep|>'
)

RECORD_FIELDS = (
    "Record ID", "High-level domain", "Subdomain", "Question", "Correct Answer",
    "Incorrect Answer 1", "Incorrect Answer 2", "Incorrect Answer 3",
)


class PreparationError(RuntimeError):
    pass


def require_hash(path: Path, expected: str, role: str) -> None:
    observed = sha256_file(path)
    if observed != expected:
        raise PreparationError(f"{role} SHA-256 mismatch: {observed}")


def record_hash(row: dict[str, str]) -> str:
    return sha256_bytes(canonical_json_bytes({field: row[field] for field in RECORD_FIELDS}))


def permutation_hash(permutation: list[int]) -> str:
    return sha256_bytes(canonical_json_bytes(permutation))


def prompt_for(row: dict[str, str], permutation: list[int]) -> tuple[str, str]:
    source_choices = [
        row["Correct Answer"], row["Incorrect Answer 1"],
        row["Incorrect Answer 2"], row["Incorrect Answer 3"],
    ]
    choices = [source_choices[index] for index in permutation]
    query = QUERY_TEMPLATE.format(Question=row["Question"], **dict(zip("ABCD", choices)))
    return K3_PREFIX + query + K3_SUFFIX, "ABCD"[permutation.index(0)]


def read_dataset(archive: Path) -> tuple[bytes, bytes, list[dict[str, str]]]:
    with zipfile.ZipFile(archive) as source:
        csv_bytes = source.read("dataset/gpqa_diamond.csv", pwd=DATASET_PASSWORD)
        license_bytes = source.read("dataset/license.txt", pwd=DATASET_PASSWORD)
    if sha256_bytes(csv_bytes) != DIAMOND_CSV_SHA256:
        raise PreparationError("decrypted GPQA Diamond CSV identity mismatch")
    if sha256_bytes(license_bytes) != LICENSE_SHA256:
        raise PreparationError("decrypted GPQA license identity mismatch")
    text = csv_bytes.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    if len(rows) != 198 or len({row["Record ID"] for row in rows}) != 198:
        raise PreparationError("GPQA Diamond must contain 198 unique Record IDs")
    return csv_bytes, license_bytes, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-zip", type=Path, required=True)
    parser.add_argument("--item-universe", type=Path, required=True)
    parser.add_argument("--exact30-selection", type=Path, required=True)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    require_hash(args.dataset_zip, DATASET_ZIP_SHA256, "dataset.zip")
    require_hash(args.item_universe, ITEM_UNIVERSE_SHA256, "item universe")
    require_hash(args.exact30_selection, EXACT30_SELECTION_SHA256, "paired selection")
    require_hash(args.campaign, CAMPAIGN_SHA256, "campaign")
    if sha256_bytes(QUERY_TEMPLATE.encode("utf-8")) != QUERY_TEMPLATE_SHA256:
        raise PreparationError("embedded query template drift")

    universe = load_json(args.item_universe)
    selection = load_json(args.exact30_selection)
    campaign = load_json(args.campaign)
    if universe.get("schema_version") != "issue100-gpqa-item-universe-v3" or \
            selection.get("schema_version") != "issue100-gpqa-exact30-selection-v3" or \
            campaign.get("schema_version") != "issue100-gpqa-campaign-v3":
        raise PreparationError("protected manifest schema drift")
    if campaign.get("source_item_universe_sha256") != ITEM_UNIVERSE_SHA256 or \
            campaign.get("exact30_selection_sha256") != EXACT30_SELECTION_SHA256:
        raise PreparationError("campaign does not bind the frozen source manifests")

    _, _, rows = read_dataset(args.dataset_zip)
    manifest_items = {item["record_id"]: item for item in universe["items"]}
    if len(manifest_items) != 198:
        raise PreparationError("item universe cardinality mismatch")

    rng = random.Random(0)
    prepared: dict[str, dict] = {}
    for source_ordinal, row in enumerate(rows, 1):
        record_id = row["Record ID"]
        permutation = rng.sample(range(4), 4)
        expected = manifest_items.get(record_id)
        if expected is None:
            raise PreparationError(f"dataset item absent from universe: {record_id}")
        prompt, correct_letter = prompt_for(row, permutation)
        checks = {
            "source_ordinal": source_ordinal,
            "record_sha256": record_hash(row),
            "choice_source_indices": permutation,
            "choice_permutation_sha256": permutation_hash(permutation),
            "correct_answer_letter": correct_letter,
            "generation_seed": generation_seed(record_id),
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "domain": row["High-level domain"],
            "subdomain": row["Subdomain"],
        }
        for key, observed in checks.items():
            if expected.get(key) != observed:
                raise PreparationError(f"{record_id} manifest mismatch for {key}")
        prepared[record_id] = {
            "schema_version": "issue100-protected-item-v1",
            "record_id": record_id,
            "source_ordinal": source_ordinal,
            "domain": row["High-level domain"],
            "subdomain": row["Subdomain"],
            "record_sha256": checks["record_sha256"],
            "choice_source_indices": permutation,
            "choice_permutation_sha256": checks["choice_permutation_sha256"],
            "correct_answer_letter": correct_letter,
            "generation_seed": checks["generation_seed"],
            "rendered_prompt": prompt,
            "prompt_sha256": checks["prompt_sha256"],
            "prompt_tokens": expected["prompt_tokens"],
        }

    exact_order = campaign["exact_order"]
    s2_order = campaign["s2_order"]
    exact_ids = [item["record_id"] for item in exact_order]
    s2_ids = [item["record_id"] for item in s2_order]
    selected_ids = [item["record_id"] for item in selection["selected_items"]]
    if len(exact_ids) != 30 or len(s2_ids) != 198 or len(set(s2_ids)) != 198 or \
            exact_ids != s2_ids[:30] or exact_ids != selected_ids:
        raise PreparationError("EXACT/S2 order or paired prefix drift")
    if sorted(item["domain"] for item in exact_order).count("Biology") != 10 or \
            sorted(item["domain"] for item in exact_order).count("Chemistry") != 10 or \
            sorted(item["domain"] for item in exact_order).count("Physics") != 10:
        raise PreparationError("paired domain quotas drift")

    root = args.output_root.resolve()
    item_root = root / "protected-items"
    item_root.mkdir(parents=True, exist_ok=True)
    for record_id, item in prepared.items():
        atomic_json(item_root / f"{record_id}.json", item)

    plan = []
    for ordinal, record_id in enumerate(exact_ids, 1):
        plan.append({
            "stage": "A", "arm": "EXACT", "run_ordinal": len(plan) + 1,
            "pair_ordinal": ordinal, "s2_ordinal": None, "item_id": record_id,
        })
        plan.append({
            "stage": "A", "arm": "S2_P50", "run_ordinal": len(plan) + 1,
            "pair_ordinal": ordinal, "s2_ordinal": ordinal, "item_id": record_id,
        })
    for ordinal, record_id in enumerate(s2_ids[30:], 31):
        plan.append({
            "stage": "B", "arm": "S2_P50", "run_ordinal": len(plan) + 1,
            "pair_ordinal": None, "s2_ordinal": ordinal, "item_id": record_id,
        })
    if len(plan) != 228:
        raise PreparationError("execution plan cardinality drift")

    protected_plan = {
        "schema_version": "issue100-protected-execution-plan-v1",
        "campaign_sha256": CAMPAIGN_SHA256,
        "item_universe_sha256": ITEM_UNIVERSE_SHA256,
        "exact30_selection_sha256": EXACT30_SELECTION_SHA256,
        "query_template_sha256": QUERY_TEMPLATE_SHA256,
        "k3_wrapper_sha256": K3_WRAPPER_SHA256,
        "dataset_zip": file_identity(args.dataset_zip),
        "source_manifests": {
            "item_universe": file_identity(args.item_universe),
            "exact30_selection": file_identity(args.exact30_selection),
            "campaign": file_identity(args.campaign),
        },
        "items_root": str(item_root),
        "runs": plan,
    }
    atomic_json(root / "protected-plan.json", protected_plan)
    atomic_json(root / "preparation.json", {
        "schema_version": "issue100-preparation-v1",
        "status": "pass",
        "outcome_inspected": False,
        "items": len(prepared),
        "runs": len(plan),
        "paired_items": 30,
        "s2_items": 198,
        "protected_plan": file_identity(root / "protected-plan.json"),
    })
    print(
        "ISSUE100_PREPARED status=pass items=198 runs=228 "
        f"campaign_sha256={CAMPAIGN_SHA256}", flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 preparation: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1)

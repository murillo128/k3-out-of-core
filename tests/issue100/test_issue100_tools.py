from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "scripts/issue100"
sys.path.insert(0, str(TOOLS))

import analyze_campaign as analysis  # noqa: E402
import prepare_campaign as preparation  # noqa: E402
import protocol  # noqa: E402
import run_campaign as campaign  # noqa: E402


def fake_result(pieces: list[bytes], *, truncated: bool = False) -> dict:
    return {
        "generation": {
            "piece_hex": [piece.hex() for piece in pieces],
            "stopped_eog": not truncated,
            "truncated": truncated,
        }
    }


def fake_item(correct: str = "B") -> dict:
    return {"correct_answer_letter": correct}


def test_committed_preregistration_has_checkpoint_a_identity() -> None:
    path = ROOT / protocol.PUBLIC_PREREGISTRATION
    assert protocol.sha256_file(path) == protocol.PREREGISTRATION_SHA256
    value = protocol.load_json(path)
    assert value["schema_version"] == "issue100-checkpoint-a-preregistration-v2"
    assert value["status"] == "ACCEPTED"
    assert value["outcome_inspected"] is False
    assert value["dataset"]["archive_blob_sha1"] == "80788cb325c4d63bbccad56c2e393b33389348fe"


def test_committed_auto_admission_amendment_identity() -> None:
    path = ROOT / protocol.PUBLIC_AUTO_ADMISSION
    assert protocol.sha256_file(path) == protocol.AUTO_ADMISSION_SHA256
    value = protocol.load_json(path)
    assert value["schema_version"] == "issue100-auto-admission-v1"
    assert value["status"] == "ACCEPTED"
    assert value["outcome_inspected"] is False
    assert value["capacity"]["request_mode"] == "AUTO"
    assert value["capacity"]["request_bytes"] == 0
    assert value["capacity"]["floor_slots"] == protocol.CAPACITY_FLOOR_SLOTS


def test_frozen_prompt_and_seed_algorithms() -> None:
    assert protocol.sha256_bytes(preparation.QUERY_TEMPLATE.encode()) == protocol.QUERY_TEMPLATE_SHA256
    assert protocol.generation_seed("recZSGUkn56v9kEp1") == 3_055_973_621
    row = {
        "Question": "Question?",
        "Correct Answer": "correct",
        "Incorrect Answer 1": "wrong-1",
        "Incorrect Answer 2": "wrong-2",
        "Incorrect Answer 3": "wrong-3",
    }
    rendered, correct = preparation.prompt_for(row, [2, 0, 3, 1])
    assert correct == "B"
    assert "A) wrong-2\nB) correct\nC) wrong-3\nD) wrong-1" in rendered
    assert rendered.startswith('<|open|>message role="system" type="thinking-effort"')
    assert rendered.endswith('<|open|>think<|sep|>')


def test_record_and_permutation_hashes_are_canonical() -> None:
    row = {field: f"value-{index}" for index, field in enumerate(preparation.RECORD_FIELDS)}
    expected = hashlib.sha256(protocol.canonical_json_bytes(row)).hexdigest()
    assert preparation.record_hash(row) == expected
    assert preparation.permutation_hash([2, 0, 3, 1]) == \
        "2e80a054bca18cf1dbb9f03f01dae407bf77c46218991dc3ee17bbd1358d8d68"


def test_score_uses_only_first_response_channel_match() -> None:
    pieces = [
        b"private Answer: A reasoning ",
        protocol.RESPONSE_BOUNDARY,
        b"final Answer: $B$ and later Answer: C",
        b"<eog>",
    ]
    score = campaign.score_result(fake_result(pieces), fake_item("B"))
    assert score["extracted_answer"] == "B"
    assert score["correct"] is True
    assert score["outcome"] == "correct"
    assert score["reasoning_bytes"] == pieces[0]
    assert score["transition_bytes"] == protocol.RESPONSE_BOUNDARY
    assert score["response_bytes"] == pieces[2]
    assert score["reasoning_tokens"] == 1
    assert score["transition_tokens"] == 1
    assert score["response_tokens"] == 1
    assert score["raw_bytes"].endswith(b"<eog>")
    assert not score["content_bytes"].endswith(b"<eog>")


@pytest.mark.parametrize(
    ("pieces", "truncated", "outcome"),
    [
        ([b"Answer: B", b"<eog>"], False, "invalid"),
        ([protocol.RESPONSE_BOUNDARY, b"no letter", b"<eog>"], False, "invalid"),
        ([protocol.RESPONSE_BOUNDARY, b"Answer: B"], True, "truncated"),
    ],
)
def test_invalid_and_truncated_are_accepted_incorrect(
    pieces: list[bytes], truncated: bool, outcome: str,
) -> None:
    score = campaign.score_result(fake_result(pieces, truncated=truncated), fake_item("B"))
    assert score["correct"] is False
    assert score["outcome"] == outcome


def test_record_checksums_detect_mutation() -> None:
    bound = protocol.bind_checksum({"schema_version": "example-v1", "value": 3})
    protocol.validate_checksum(bound)
    bound["value"] = 4
    with pytest.raises(protocol.ProtocolError, match="checksum"):
        protocol.validate_checksum(bound)


def test_durable_json_helpers_round_trip(tmp_path: Path) -> None:
    control = tmp_path / "control" / "state.json"
    protocol.atomic_json(control, {"z": 1, "a": "é"})
    assert protocol.load_json(control) == {"a": "é", "z": 1}
    stream = tmp_path / "records.jsonl"
    protocol.append_canonical_jsonl(stream, {"ordinal": 1})
    protocol.append_canonical_jsonl(stream, {"ordinal": 2})
    assert campaign.load_jsonl(stream) == [{"ordinal": 1}, {"ordinal": 2}]


def test_bootstrap_exact_byte_stream_identity() -> None:
    digest = hashlib.sha256()
    first = None
    count = 0
    for indices in protocol.bootstrap_indices():
        if first is None:
            first = indices
        digest.update(bytes(indices))
        count += len(indices)
    assert first == protocol.BOOTSTRAP_REPLICATE_ZERO
    assert count == 3_000_000
    assert digest.hexdigest() == protocol.BOOTSTRAP_STREAM_SHA256


def test_exact_mcnemar_and_wilson_dispositions() -> None:
    assert analysis.exact_mcnemar(0, 0)["p_value"] == 1.0
    assert analysis.exact_mcnemar(0, 6)["p_value"] == pytest.approx(0.03125)
    runs = [
        {"correct": index < 185, "auto_resolved_slots": protocol.CAPACITY_FLOOR_SLOTS}
        for index in range(198)
    ]
    result = analysis.full_s2_statistics(runs, protocol_drift=False)
    assert result["accuracy"] == pytest.approx(185/198)
    assert result["protocol_fidelity"] == "OFFICIAL_PROTOCOL_NEAR_MATCH"
    drifted = analysis.full_s2_statistics(runs, protocol_drift=True)
    assert drifted["disposition"] == "INCONCLUSIVE_PROTOCOL_OR_SAMPLE"


def synthetic_run(ordinal: int, item_id: str, arm: str, correct: bool) -> dict:
    return protocol.bind_checksum({
        "schema_version": "issue100-accepted-run-v1",
        "run_ordinal": ordinal,
        "item_id": item_id,
        "arm": arm,
        "correct": correct,
        "first_generated_token_id": 42,
        "auto_resolved_slots": 6_000 if arm == "EXACT" else 6_002,
        "auto_resolved_bytes": (6_000 if arm == "EXACT" else 6_002)*protocol.EXPERT_BUNDLE_BYTES,
    })


def test_pair_reconciliation_appends_only_after_both_arms(tmp_path: Path) -> None:
    exact = synthetic_run(1, "item-1", "EXACT", True)
    assert campaign.reconcile_pairs(tmp_path, [exact]) == []
    s2 = synthetic_run(2, "item-1", "S2_P50", False)
    pairs = campaign.reconcile_pairs(tmp_path, [exact, s2])
    assert len(pairs) == 1
    assert pairs[0]["pair_class"] == "EXACT-only"
    assert pairs[0]["accuracy_delta"] == -1
    assert pairs[0]["exact_auto_slots"] == 6_000
    assert pairs[0]["s2_auto_slots"] == 6_002
    assert pairs[0]["auto_slot_delta"] == 2
    assert len(campaign.load_jsonl(tmp_path / "pairs.jsonl")) == 1
    assert len(campaign.reconcile_pairs(tmp_path, [exact, s2])) == 1


def test_pair_rejects_first_token_nondeterminism() -> None:
    exact = synthetic_run(1, "item-1", "EXACT", True)
    s2 = synthetic_run(2, "item-1", "S2_P50", True)
    s2["first_generated_token_id"] = 43
    s2["artifact_checksum"] = protocol.record_checksum(s2)
    with pytest.raises(campaign.CampaignError, match="first sampled token"):
        campaign.pair_record(1, exact, s2)


def test_interrupted_unaccepted_attempt_is_sealed_not_reused(tmp_path: Path) -> None:
    directory = tmp_path / "attempts/run-001-item-exact/attempt-01"
    directory.mkdir(parents=True)
    protocol.atomic_json(directory / "attempt-start.json", {
        "schema_version": "issue100-attempt-start-v1",
        "started_at_epoch_s": 10.0,
        "pid": None,
        "process_start_ticks": None,
    })
    protocol.atomic_json(directory / "input.json", {"partial": True})
    identity = {
        "run_ordinal": 1, "item_id": "item", "arm": "EXACT",
        "stage": "A", "pair_ordinal": 1, "s2_ordinal": None,
        "attempt_ordinal": 1, "campaign_sha256": protocol.CAMPAIGN_SHA256,
    }
    campaign.seal_interrupted_attempt(directory, identity)
    manifest = protocol.load_json(directory / "attempt-manifest.json")
    assert manifest["accepted"] is False
    assert manifest["interrupted_before_acceptance"] is True
    assert "input.json" in manifest["artifacts"]
    assert campaign.cumulative_attempt_seconds(tmp_path) >= 0.0


def test_probe_command_uses_direct_auto_without_capacity_argument(tmp_path: Path) -> None:
    command = campaign.build_probe_command(
        tmp_path / "probe", tmp_path / "model", tmp_path / "input.json",
        tmp_path / "result.json", tmp_path / "progress.jsonl", "EXACT", 7,
    )
    assert "--cold-cache-bytes" not in command
    assert command[command.index("--arm") + 1] == "EXACT"
    assert command[command.index("--seed") + 1] == "7"


def test_analysis_nearest_rank_is_strictly_preregistered() -> None:
    values = [0.0, 1.0, 2.0, 3.0]
    assert analysis.nearest_rank(values, 0.25) == 0.0
    assert analysis.nearest_rank(values, 0.50) == 1.0
    assert analysis.nearest_rank(values, 0.95) == 3.0


def test_public_sources_do_not_embed_protected_gpqa_payload() -> None:
    public = [
        ROOT / "corpus/phase13/issue100-preregistration-v2.json",
        ROOT / "corpus/phase13/issue100-auto-admission-v1.json",
        *TOOLS.glob("*.py"),
        TOOLS / "gpqa_probe.cpp",
    ]
    for path in public:
        text = path.read_text()
        assert "Pre-Revision Correct Answer" not in text
        assert "recZSGUkn56v9kEp1\",\"correct_answer_letter" not in text

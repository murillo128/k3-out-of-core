from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "issue105"))

from curate_evidence import (  # noqa: E402
    CurationError,
    TopLevelSchemaScanner,
    common_provenance,
    compare_nested_numbers,
    csv_schema_fields,
    csv_logical_hash,
    require_identity,
    source_evidence_class,
    source_status,
    phase_core_sets,
    stage_a_descriptive_decompositions,
    stage_c_comparison_aggregates,
    validate_physical_rows,
    validate_source_catalog,
)


def physical_row(stage: str, case_id: str, policy: str) -> dict[str, object]:
    return {
        "stage": stage,
        "case_id": case_id,
        "case_role": "sentinel" if stage == "STAGE_A_SENTINEL" else "primary",
        "policy": policy,
        "protocol": "issue102_full_prompt",
        "source_evidence_class": "MEASURED_PHYSICAL",
    }


class Issue105CurationTests(unittest.TestCase):
    def test_streamed_top_level_schema_preserves_chunk_boundary(self) -> None:
        scanner = TopLevelSchemaScanner()
        scanner.feed(b'{\n  "nested": {\n    "schema_version": "not-top-level"\n  },\n  "schema_')
        scanner.feed(b'version": "issue102-cross-prompt-cell-v1",\n  "value": 1\n}')
        self.assertEqual(scanner.finish(), "issue102-cross-prompt-cell-v1")

    def test_streamed_top_level_schema_rejects_ambiguous_declaration(self) -> None:
        scanner = TopLevelSchemaScanner()
        scanner.feed(b'{"schema_version":null}')
        with self.assertRaisesRegex(CurationError, "invalid top-level"):
            scanner.finish()

    def test_schema_less_json_is_failed_for_scientific_ingestion(self) -> None:
        status = source_status(
            "host/unknown/result.json", "RAW_OR_DERIVED_HOST_EVIDENCE", set(), ""
        )
        self.assertEqual(status[0], "failed")

    def test_source_sha_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "input.json"
            path.write_text('{"schema_version":"v1"}\n', encoding="utf-8")
            with self.assertRaises(CurationError):
                require_identity(path, "0" * 64)

    def test_source_schema_lock_is_exact(self) -> None:
        lock = json.loads((ROOT / "scripts/issue105/source-lock.json").read_text())
        self.assertEqual(lock["schema_version"], "issue105-source-lock-v1")
        self.assertEqual(lock["issue102"]["archive"]["member_count"], 1442)
        self.assertEqual(
            lock["issue102"]["archive"]["sha256"],
            "e198913eb541b2a2e7465a01e09215fc5fecf6fb91574ff1841b11bf2664250c",
        )

    def test_authority_supersession_conflict_rejected(self) -> None:
        row = {
            "source_issue_release": "#102/r",
            "archive_member_original_path": "a.json",
            "source_evidence_class": "CURATED_FROM_MEASURED",
            "status": "accepted",
            "superseded_by": "b.json",
        }
        with self.assertRaises(CurationError):
            validate_source_catalog([row])

    def test_duplicate_authority_path_rejected(self) -> None:
        row = {
            "source_issue_release": "#102/r",
            "archive_member_original_path": "a.json",
            "source_evidence_class": "CURATED_FROM_MEASURED",
            "status": "accepted",
            "superseded_by": "",
        }
        with self.assertRaises(CurationError):
            validate_source_catalog([row, dict(row)])

    def test_protocol_pooling_rejected(self) -> None:
        rows = [physical_row("STAGE_A", f"case-{index}", "S2_P50") for index in range(128)]
        rows += [physical_row("STAGE_A_SENTINEL", f"sentinel-{index}", "S2_P50") for index in range(8)]
        rows += [physical_row("STAGE_C", f"stage-c-{index}", "EXACT" if index % 2 == 0 else "KNEE") for index in range(48)]
        rows[0]["protocol"] = "legacy_first_full"
        with self.assertRaisesRegex(CurationError, "protocol pooling"):
            validate_physical_rows(rows)

    def test_observer_tps_rejected(self) -> None:
        rows = [physical_row("STAGE_A", f"case-{index}", "S2_P50") for index in range(128)]
        rows += [physical_row("STAGE_A_SENTINEL", f"sentinel-{index}", "S2_P50") for index in range(8)]
        rows += [physical_row("STAGE_C", f"stage-c-{index}", "EXACT" if index % 2 == 0 else "KNEE") for index in range(48)]
        rows[10]["source_evidence_class"] = "MEASURED_OBSERVER"
        with self.assertRaisesRegex(CurationError, "observer/replay timing"):
            validate_physical_rows(rows)

    def test_stage_a_stage_c_s2_double_count_rejected(self) -> None:
        rows = [physical_row("STAGE_A", f"case-{index}", "S2_P50") for index in range(128)]
        rows += [physical_row("STAGE_A_SENTINEL", f"sentinel-{index}", "S2_P50") for index in range(8)]
        rows += [physical_row("STAGE_C", f"stage-c-{index}", "EXACT" if index % 2 == 0 else "KNEE") for index in range(48)]
        rows[-1]["policy"] = "S2_P50"
        with self.assertRaisesRegex(CurationError, "double-count"):
            validate_physical_rows(rows)

    def test_evidence_class_propagation(self) -> None:
        exact = common_provenance(["b", "a", "a"], "d", "c", "EXACT_REPLAY", "p")
        physical = common_provenance(["a"], "d", "c", "MEASURED_PHYSICAL", "p")
        self.assertEqual(exact["derived_evidence_class"], "EXACT_REPLAY")
        self.assertEqual(physical["derived_evidence_class"], "CURATED_FROM_MEASURED")
        self.assertEqual(exact["source_sha256"], '["a","b"]')

    def test_classification_keeps_replay_and_observer_distinct(self) -> None:
        self.assertEqual(
            source_evidence_class("host/observer-replay-v1/exact-capacity-mrc.json"),
            "EXACT_REPLAY",
        )
        self.assertEqual(
            source_evidence_class("host/stage-b-observer/run-001/result.json"),
            "MEASURED_OBSERVER",
        )
        status = source_status(
            "host/stage-c-control-v1/progress.json", "RAW_OR_DERIVED_HOST_EVIDENCE", set()
        )
        self.assertEqual(status[0], "superseded")

    def test_core_sets_preserve_layer_boundaries(self) -> None:
        committee = {
            "phases": {
                "DECODE": {
                    "gamma_sensitivity": [{
                        "gamma": 1.0,
                        "layers": [
                            {"core_experts": [1, 2]},
                            {"core_experts": [3]},
                        ],
                    }]
                }
            }
        }
        cores = phase_core_sets(committee)
        self.assertEqual(cores["DECODE"][1.0], [{1, 2}, {3}])

    def test_logical_hash_depends_on_canonical_row_order(self) -> None:
        rows = [{"b": 2, "a": 1}, {"b": 4, "a": 3}]
        expected = hashlib.sha256(
            b'{"a":1,"b":2}\n{"a":3,"b":4}\n'
        ).hexdigest()
        self.assertEqual(csv_logical_hash(rows), expected)
        self.assertNotEqual(csv_logical_hash(rows), csv_logical_hash(list(reversed(rows))))

    def test_csv_schema_freezes_type_and_nullability(self) -> None:
        fields = csv_schema_fields(
            [{"name": "a", "value": 1.0}, {"name": "b", "value": None}],
            ["name", "value"],
        )
        self.assertEqual(fields[0], {"name": "name", "type": "string", "nullable": False})
        self.assertEqual(fields[1], {"name": "value", "type": "float64", "nullable": True})

    def test_stage_a_actual_token_decomposition_is_falsifiable(self) -> None:
        rows = [
            {"semantic_family": "a", "length_level": 1, "templated_prompt_tokens": 10,
             "hit_ratio": 0.5, "decode_tok_s": 1.0},
            {"semantic_family": "a", "length_level": 2, "templated_prompt_tokens": 20,
             "hit_ratio": 0.6, "decode_tok_s": 1.2},
            {"semantic_family": "b", "length_level": 1, "templated_prompt_tokens": 30,
             "hit_ratio": 0.7, "decode_tok_s": 1.4},
        ]
        observed = stage_a_descriptive_decompositions(rows)
        expected = json.loads(json.dumps(observed))
        expected["per_family"][0]["templated_prompt_tokens"]["median"] += 1
        with self.assertRaisesRegex(CurationError, "numeric mismatch"):
            compare_nested_numbers(observed, expected, "/stage_a")

    def test_stage_c_paired_aggregate_is_falsifiable(self) -> None:
        rows = []
        for index in range(24):
            case_id = f"case-{index:02d}"
            for stage, policy, tps, hit, loads, byte_count in (
                ("STAGE_A", "S2_P50", 1.2, 0.7, 30.0, 300.0),
                ("STAGE_C", "EXACT", 1.0, 0.5, 50.0, 500.0),
                ("STAGE_C", "KNEE", 1.1, 0.6, 40.0, 400.0),
            ):
                rows.append({
                    "stage": stage, "case_id": case_id, "policy": policy,
                    "decode_tok_s": tps, "hit_ratio": hit,
                    "loads_per_token": loads, "bytes_per_token": byte_count,
                })
        observed = stage_c_comparison_aggregates(rows)
        expected = json.loads(json.dumps(observed))
        expected["s2_vs_exact"]["decode_tok_s_ratio"]["median"] += 0.01
        with self.assertRaisesRegex(CurationError, "numeric mismatch"):
            compare_nested_numbers(observed, expected, "/stage_c")


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Finalize the non-final-capable issue #58 Checkpoint A manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    return {"path": str(path.relative_to(ROOT)), "size": path.stat().st_size, "sha256": sha256_file(path)}


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--implementation-revision", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    base = "63fdfdd49112e4324eb1206d0b6b31bd547669a6"
    nested = "71b4b0251fb314cb955fe5f43b6a1e382fc2b65c"
    if subprocess.check_output(["git", "merge-base", "--is-ancestor", args.implementation_revision, "HEAD"], cwd=ROOT).strip():
        raise AssertionError("unreachable")
    if subprocess.check_output(["git", "-C", "llama.cpp", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != nested:
        raise ValueError("nested revision changed")
    host_path = evidence / "host-preflight.json"
    gen1_path = evidence / "generation-1.json"
    gen2_path = evidence / "generation-2.json"
    comparison_path = evidence / "clean-generation-comparison.json"
    qualification_path = evidence / "harness-qualification.json"
    validation_path = evidence / "validation.json"
    raw_index_path = evidence / "raw-evidence-index.json"
    host = load(host_path)
    gen1 = load(gen1_path)
    gen2 = load(gen2_path)
    comparison = load(comparison_path)
    qualification = load(qualification_path)
    validation = load(validation_path)
    raw_index = load(raw_index_path)
    required_statuses = (host["status"], comparison["status"], qualification["status"], validation["status"], raw_index["status"])
    if required_statuses != ("PASS",) * 5:
        raise ValueError(f"Checkpoint A input did not pass: {required_statuses}")
    generation = gen2["generation"]
    if generation["aggregate_useful_sha256"] != gen1["generation"]["aggregate_useful_sha256"]:
        raise ValueError("clean generation payload identities differ")
    cases = qualification["cases"]
    negative_paths = [
        evidence / f"harness-qualification-attempt-{attempt}-rejected.json"
        for attempt in range(1, 5)
    ]
    document = {
        "schema_version": "phase12-nvme-checkpoint-a-v1",
        "status": "PASS",
        "checkpoint": "A",
        "final_capable": False,
        "issue": 58,
        "revisions": {
            "controlling_base": base,
            "implementation": args.implementation_revision,
            "nested_llama_cpp": nested,
        },
        "host": {
            "evidence": identity(host_path),
            "oci_instance": host["oci_instance"],
            "contract_deviation": host["contract_deviation"],
            "filesystems": [item["findmnt"]["parsed"]["filesystems"][0] for item in host["filesystems"]],
            "capability_probes": {name: probe["summary"] for name, probe in host["capability_probes"].items()},
        },
        "corpus": {
            "clean_generation_evidence": [identity(gen1_path), identity(gen2_path)],
            "comparison_evidence": identity(comparison_path),
            "record_count": generation["record_count"],
            "aggregate_useful_sha256": generation["aggregate_useful_sha256"],
            "route_sha256": generation["route_sha256"],
            "layout_a_definition_sha256": generation["layout_a_definition_sha256"],
            "layout_b_definition_sha256": generation["layout_b_definition_sha256"],
            "layout_a_entries_sha256": gen2["layout_identity"]["layout_a_entries_sha256"],
            "layout_b_index_sha256": gen2["layout_identity"]["layout_b_index_sha256"],
            "verified_useful_bytes_per_layout": gen2["verification"]["verified_useful_bytes_per_layout"],
            "layout_a_extent_proof": gen2["verification"]["layout_a_extent_proof"],
            "layout_b_extent_proof": gen2["verification"]["layout_b_extent_proof"],
            "retained_physical_generation": "/mnt/nvme0/k3-phase12-nvme-generation-2",
            "swap_used_bytes_during_generation": gen2["swap_used_bytes"],
        },
        "harness_qualification": {
            "evidence": identity(qualification_path),
            "raw_attempt": "qualification-attempt-6",
            "case_count": qualification["case_count"],
            "layouts": qualification["layouts"],
            "apis": qualification["apis"],
            "orders": qualification["orders"],
            "queue_depths": qualification["queue_depths"],
            "cache_states": qualification["cache_states"],
            "checksum_sink_sha256": qualification["checksum_sink_sha256"],
            "harness_identity": qualification["harness_identity"],
            "maximum_buffer_bytes": max(int(case["buffer_bytes"]) for case in cases),
            "all_short_reads_zero": all(int(case["short_reads"]) == 0 for case in cases),
            "all_requested_qd_supported": all(case["effective_qd_status"] == "SUPPORTED" for case in cases),
            "all_swap_zero": all(int(case["swap_used_bytes"]) == 0 for case in cases),
            "all_lifetime_deltas_zero": all(case["lifetime_resources"] == {"fd_delta": 0, "thread_delta": 0} for case in cases),
        },
        "negative_harness_attempts": [identity(path) for path in negative_paths],
        "focused_validation": identity(validation_path) | {
            "binary": validation["binary"],
            "commands_passed": len(validation["commands"]),
        },
        "raw_evidence": identity(raw_index_path) | {"archive": raw_index["archive"], "file_count": raw_index["file_count"]},
        "gates": {
            "exact_corpus_and_physical_backing": "PASS",
            "layout_byte_equivalence": "PASS",
            "fair_api_order_qd_cache_matrix": "PASS",
            "failure_lifetime_and_resource_behavior": "PASS",
            "runtime_or_default_change_selected": False,
        },
        "limitations": [
            "Observed VM.DenseIO.E5.Flex, Oracle Linux 9.8, and 192 GB RAM differ from the nominal E4/Ubuntu/256 GB two-drive profile.",
            "Clean generation 1 was deleted after complete verification and compact capture; it is deterministically reproducible from the recorded identities.",
            "The raw archive includes metadata and measured cells, not the 51.7 GB physical payload stores; clean generation 2 remains on local NVMe.",
            "Checkpoint A qualifies the harness only and selects no performance winner or runtime/default change.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

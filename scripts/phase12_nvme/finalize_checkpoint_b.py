#!/usr/bin/env python3
"""Freeze the final-capable Phase 12-NVMe technical handoff."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "63fdfdd49112e4324eb1206d0b6b31bd547669a6"
NESTED = "71b4b0251fb314cb955fe5f43b6a1e382fc2b65c"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(path: Path) -> dict[str, object]:
    path = path.resolve()
    return {
        "path": str(path.relative_to(ROOT)),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def compact_cell(cell: dict[str, object]) -> dict[str, object]:
    return {
        "case": cell["case"],
        "layout": cell["layout"],
        "order": cell["order"],
        "api": cell["api"],
        "requested_qd": cell["requested_qd"],
        "cache_state": cell["cache_state"],
        "useful_gbps": cell["useful_gbps"],
        "token_equivalent_latency_ms": cell["latency_ms"],
        "operation_elapsed_ms": cell["operation_elapsed_ms"],
        "buffer_bytes": cell["buffer_bytes"],
        "maximum_active_operations": cell["maximum_active_operations"],
        "checksum_sink_sha256": cell["checksum_sink_sha256"],
        "swap_used_bytes": cell["swap_used_bytes"],
        "lifetime_resources": cell["lifetime_resources"],
        "plan_sha256": cell["plan_sha256"],
    }


def compact_distribution(value: dict[str, object]) -> dict[str, object]:
    return {key: item for key, item in value.items() if key != "values"}


def compact_cache_capacity(row: dict[str, object]) -> dict[str, object]:
    result = {
        "capacity_gib": row["capacity_gib"],
        "support": row["support"],
    }
    if row["support"]["status"] != "SUPPORTED":
        return result
    replay = row["replay"]
    result["replay"] = {
        "windows": replay["windows"],
        "admissions": replay["admissions"],
        "evictions": replay["evictions"],
        "final_occupancy_slots": replay["final_occupancy_slots"],
        "final_occupancy_bytes": replay["final_occupancy_bytes"],
        "decode_misses_per_token": compact_distribution(replay["decode_misses_per_token"]),
        "decode_required_nvme_bytes_per_token": compact_distribution(
            replay["decode_required_nvme_bytes_per_token"]
        ),
        "final_lru_state_sha256": replay["final_lru_state_sha256"],
        "deterministic_replay_digest": replay["deterministic_replay_digest"],
    }
    projection = row["storage_only_projection"]
    result["storage_only_projection"] = {
        name: {
            "service_throughput_gbps": item["service_throughput_gbps"],
            "observed_throughput_gbps_range": item["observed_throughput_gbps_range"],
            "projected_storage_seconds_per_decode_token": compact_distribution(
                item["projected_storage_seconds_per_decode_token"]
            ),
            "mean_storage_seconds_range_from_observed_throughput": (
                item["mean_storage_seconds_range_from_observed_throughput"]
            ),
        }
        for name, item in projection.items() if name in ("single_nvme", "dual_nvme")
    } | {"claim_boundary": projection["claim_boundary"]}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-revision", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--raw-index-output", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    if subprocess.run(
        ["git", "merge-base", "--is-ancestor", args.evidence_revision, "HEAD"],
        cwd=ROOT,
    ).returncode:
        raise ValueError("evidence revision is not an ancestor of the target")
    if subprocess.check_output(["git", "-C", "llama.cpp", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() != NESTED:
        raise ValueError("nested revision changed")
    if subprocess.check_output(["git", "-C", "llama.cpp", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise ValueError("nested repository is dirty")

    paths = {
        "checkpoint_a": evidence / "checkpoint-a/checkpoint-a-manifest.json",
        "host": evidence / "checkpoint-a/host-preflight.json",
        "generation_2": evidence / "checkpoint-a/generation-2.json",
        "checkpoint_a_validation": evidence / "checkpoint-a/validation.json",
        "fio": evidence / "fio-characterization.json",
        "baseline_matrix": evidence / "baseline-matrix.json",
        "baseline_analysis": evidence / "baseline-analysis.json",
        "candidate_1": evidence / "candidate-01-screen.json",
        "candidate_2": evidence / "candidate-02-screen.json",
        "trace_attempt_1": evidence / "winner-trace-attempt-1-analysis.json",
        "trace_attempt_2": evidence / "winner-trace-attempt-2-analysis.json",
        "dual_corpus": evidence / "dual-corpus.json",
        "dual_comparison": evidence / "dual-comparison.json",
        "colibri_preflight": evidence / "colibri-preflight.json",
        "colibri_snapshot": evidence / "colibri-snapshot-verification.json",
        "colibri_attempt_1": evidence / "colibri-reference-attempt-1-rejected.json",
        "colibri_reference": evidence / "colibri-reference.json",
        "real_routing_capture": evidence / "real-routing-capture.json",
        "cache_locality": evidence / "cache-locality.json",
        "final_corpus": evidence / "final-corpus-verification.json",
        "final_validation": evidence / "checkpoint-b/final-validation.json",
    }
    documents = {name: load(path) for name, path in paths.items()}
    pass_names = (
        "checkpoint_a", "host", "checkpoint_a_validation", "fio", "baseline_matrix",
        "baseline_analysis", "candidate_1", "candidate_2", "trace_attempt_2",
        "dual_corpus", "dual_comparison", "colibri_preflight", "colibri_snapshot",
        "colibri_reference", "real_routing_capture", "cache_locality", "final_corpus",
        "final_validation",
    )
    if any(documents[name]["status"] != "PASS" for name in pass_names):
        raise ValueError("a required final input did not pass")
    if documents["trace_attempt_1"]["disposition"] != "rejected":
        raise ValueError("invalid first trace disposition")
    if documents["colibri_attempt_1"]["disposition"] != "rejected":
        raise ValueError("invalid first Colibrì disposition")
    if [documents["candidate_1"]["disposition"], documents["candidate_2"]["disposition"]] != ["rejected", "rejected"]:
        raise ValueError("optimization stop-rule inputs changed")

    checkpoint_a = documents["checkpoint_a"]
    final_corpus = documents["final_corpus"]
    if final_corpus["aggregate_useful_sha256"] != checkpoint_a["corpus"]["aggregate_useful_sha256"]:
        raise ValueError("final corpus aggregate changed")
    if final_corpus["route_sha256"] != checkpoint_a["corpus"]["route_sha256"]:
        raise ValueError("final route changed")
    if not final_corpus["layout_a_extent_proof"]["complete"] or not final_corpus["layout_b_extent_proof"]["complete"]:
        raise ValueError("final physical-backing proof failed")

    baseline_analysis = documents["baseline_analysis"]
    baseline = baseline_analysis["frozen_baseline"]
    resource = baseline_analysis["resource_efficient_comparator"]
    if baseline["name"] != "SINGLE_NVME_LAYOUT_A_LOGICAL_DIRECT_PREAD_QD32":
        raise ValueError("frozen baseline identity changed")
    if not baseline_analysis["matrix_conclusion"]["direct_single_nvme_is_hardware_bound"]:
        raise ValueError("baseline no longer reaches the device ceiling")
    if not baseline_analysis["matrix_conclusion"]["no_runtime_or_default_change_selected"]:
        raise ValueError("unexpected runtime/default selection")

    trace = documents["trace_attempt_2"]
    if trace["disposition"] != "accepted" or trace["loss_counters"]["count"] or trace["failures"]:
        raise ValueError("winner trace is not acceptable")
    if trace["correctness"]["useful_bytes"] != 25_829_572_608:
        raise ValueError("trace useful-byte mismatch")
    if trace["unexplained_residual_fraction"] >= 0.01:
        raise ValueError("winner trace has material residual")

    dual = documents["dual_comparison"]
    if dual["disposition"] != "accepted" or dual["failures"]:
        raise ValueError("dual-NVMe evidence is not acceptable")
    colibri = documents["colibri_reference"]
    if colibri["disposition"] != "accepted" or colibri["failures"] or colibri["environment"]["K3_TOPP"] != "0":
        raise ValueError("Colibrì reference is not acceptable")
    if colibri["metrics"]["configured_experts_per_token"] != 16:
        raise ValueError("Colibrì top-k mismatch")
    routing_capture = documents["real_routing_capture"]
    if routing_capture["disposition"] != "accepted" or routing_capture["failures"]:
        raise ValueError("real-routing capture is not acceptable")
    if routing_capture["colibri_commit"] != colibri["colibri_commit"]:
        raise ValueError("real-routing Colibrì revision changed")
    if routing_capture["model_revision"] != colibri["model_revision"]:
        raise ValueError("real-routing model revision changed")
    if routing_capture["environment"]["K3_TOPP"] != "0":
        raise ValueError("real-routing capture pruned exact top-16 routing")
    if routing_capture["routing_trace"]["complete_decode_forwards"] < 256:
        raise ValueError("real-routing capture is below the amended sample target")
    if routing_capture["routing_trace"]["top_k"] != 16:
        raise ValueError("real-routing capture top-k changed")
    if len(routing_capture["routing_trace"]["routed_layers"]) != 92:
        raise ValueError("real-routing capture routed-layer count changed")
    cache_locality = documents["cache_locality"]
    if cache_locality["disposition"] != "accepted_evidence_only":
        raise ValueError("cache-locality replay is not acceptable")
    if cache_locality["policy"] != {
        "admission": "ALWAYS",
        "capacity_unit": "binary GiB converted to floor(capacity_bytes / exact useful expert bundle bytes)",
        "cold_start_window": (
            "empty-cache prefill is reported separately; decode-cold is the first 32 complete decode-token "
            "forwards after that real prefill"
        ),
        "default_or_policy_change": False,
        "initial_state": "empty",
        "policy": "LRU",
        "request_boundary": "cache persists across captured requests in request-id order",
        "scope": "GLOBAL",
        "steady_state_window": "remaining 224 complete decode-token forwards",
    }:
        raise ValueError("cache-locality replay policy semantics changed")
    if [row["capacity_gib"] for row in cache_locality["capacity_curve"]] != [0, 8, 16, 32, 64, 96]:
        raise ValueError("cache-locality capacity sweep changed")
    if any(row["support"]["status"] != "SUPPORTED" for row in cache_locality["capacity_curve"]):
        raise ValueError("an amended cache capacity is unsupported")
    if cache_locality["routing_corpus"]["complete_decode_token_forwards"] < 256:
        raise ValueError("cache-locality replay sample count changed")
    if cache_locality["interpretation"]["claim_boundary"].find("project TPS claim") < 0:
        raise ValueError("cache-locality claim boundary changed")

    raw_index_paths = [
        evidence / "checkpoint-a/raw-evidence-index.json",
        evidence / "baseline-raw-index.json",
        evidence / "candidate-01-screen-raw-index.json",
        evidence / "candidate-02-screen-raw-index.json",
        evidence / "winner-trace-attempt-1-raw-index.json",
        evidence / "winner-trace-attempt-2-raw-index.json",
        evidence / "dual-comparison-raw-index.json",
        evidence / "colibri-reference-raw-index.json",
        evidence / "cache-locality-raw-index.json",
    ]
    raw_indexes: list[dict[str, object]] = []
    aggregate = hashlib.sha256()
    for path in raw_index_paths:
        item = load(path)
        archive_path = Path(str(item["archive"]["path"]))
        if item["status"] != "PASS" or not archive_path.is_file():
            raise ValueError(f"raw index/archive unavailable: {path}")
        if archive_path.stat().st_size != int(item["archive"]["size"]):
            raise ValueError(f"raw archive size mismatch: {archive_path}")
        if sha256_file(archive_path) != item["archive"]["sha256"]:
            raise ValueError(f"raw archive checksum mismatch: {archive_path}")
        entry = {
            "index": identity(path),
            "archive": item["archive"],
            "file_count": item["file_count"],
        }
        raw_indexes.append(entry)
        aggregate.update(
            (f"{entry['index']['path']}\0{entry['index']['sha256']}\0"
             f"{item['archive']['path']}\0{item['archive']['size']}\0{item['archive']['sha256']}\n").encode()
        )
    raw_document = {
        "schema_version": "phase12-nvme-final-raw-index-v2",
        "status": "PASS",
        "index_count": len(raw_indexes),
        "indexes": raw_indexes,
        "aggregate_index_and_archive_sha256": aggregate.hexdigest(),
        "retained_external_inputs": [
            {
                "description": "verified physical synthetic corpus generation 2",
                "path": "/mnt/nvme0/k3-phase12-nvme-generation-2",
                "aggregate_useful_sha256": final_corpus["aggregate_useful_sha256"],
                "route_sha256": final_corpus["route_sha256"],
            },
            {
                "description": "publisher-verified Kimi-K3 text snapshot",
                "path": documents["colibri_snapshot"]["snapshot"],
                "repository": documents["colibri_snapshot"]["repository"],
                "revision": documents["colibri_snapshot"]["revision"],
                "verified_bytes": documents["colibri_snapshot"]["verified_bytes"],
                "publisher_manifest_sha256": documents["colibri_snapshot"]["publisher_manifest_sha256"],
            },
        ],
    }
    raw_output = args.raw_index_output.resolve()
    raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.write_text(json.dumps(raw_document, indent=2, sort_keys=True) + "\n")

    host = documents["host"]
    candidates = [documents["candidate_1"], documents["candidate_2"]]
    request_wall = int(trace["critical_path_attribution"]["request_wall_ns"])
    critical = trace["critical_path_attribution"]
    document = {
        "schema_version": "phase12-nvme-checkpoint-b-v2",
        "status": "PASS",
        "checkpoint": "B",
        "final_capable": True,
        "technical_revisions": {
            "controlling_base": BASE,
            "evidence_revision": args.evidence_revision,
            "nested_llama_cpp": NESTED,
            "nested_changed": False,
        },
        "technical_scope": {
            "result": (
                "full-scale CPU/NVMe storage discovery, real-route cache-locality evidence, and frozen Phase 12 "
                "rerun handoff"
            ),
            "runtime_or_default_change": "NONE",
            "storage_format_disposition": "DEFERRED_TO_PHASE_12_NVME_PLUS_DISCRETE_CUDA_CONFIRMATION",
            "synthetic_claim_boundary": "storage-only token-equivalent service; not project model inference, quality, TTFT, or actual project tokens/second",
        },
        "host": {
            "evidence": identity(paths["host"]),
            "instance": host["oci_instance"],
            "architecture": host["architecture"],
            "cpu": host["cpu"],
            "memory": host["memory"],
            "swap": host["swap"],
            "kernel": host["kernel"],
            "os_release": host["os_release"],
            "numa": host["numa"],
            "nvme": host["nvme"],
            "block_devices": host["block_devices"],
            "filesystems": [item["findmnt"]["parsed"]["filesystems"][0] for item in host["filesystems"]],
            "contract_deviation": host["contract_deviation"],
            "discrete_cuda_available": False,
        },
        "corpus": {
            "checkpoint_a": identity(paths["checkpoint_a"]),
            "generation_2": identity(paths["generation_2"]),
            "final_verification": identity(paths["final_corpus"]),
            "record_count": final_corpus["record_count"],
            "verified_useful_bytes_per_layout": final_corpus["verified_useful_bytes_per_layout"],
            "aggregate_useful_sha256": final_corpus["aggregate_useful_sha256"],
            "route_sha256": final_corpus["route_sha256"],
            "layout_hashes": {
                "layout_a_definition_sha256": checkpoint_a["corpus"]["layout_a_definition_sha256"],
                "layout_b_definition_sha256": checkpoint_a["corpus"]["layout_b_definition_sha256"],
                "layout_a_entries_sha256": checkpoint_a["corpus"]["layout_a_entries_sha256"],
                "layout_b_index_sha256": checkpoint_a["corpus"]["layout_b_index_sha256"],
            },
            "final_extent_proofs": {
                "layout_a": final_corpus["layout_a_extent_proof"],
                "layout_b": final_corpus["layout_b_extent_proof"],
            },
            "retained_physical_generation": "/mnt/nvme0/k3-phase12-nvme-generation-2",
        },
        "baseline_storage_matrix": {
            "fio_evidence": identity(paths["fio"]),
            "matrix_evidence": identity(paths["baseline_matrix"]),
            "analysis_evidence": identity(paths["baseline_analysis"]),
            "frozen_baseline_name": baseline["name"],
            "frozen_baseline_reason": baseline["reason"],
            "fraction_of_fio_peak": baseline["fraction_of_fio_peak"],
            "cold": compact_cell(baseline["cold"]),
            "warm_direct_comparator": compact_cell(baseline["warm_direct_comparator"]),
            "matrix_conclusion": baseline_analysis["matrix_conclusion"],
        },
        "optimization_campaign": {
            "stop_rule": "two consecutive non-promising independent causal candidates",
            "stop_rule_reached": True,
            "candidate_count": len(candidates),
            "candidates": [
                {
                    "evidence": identity(paths[f"candidate_{index}"]),
                    "name": candidate["candidate"],
                    "causal_change": candidate["causal_change"],
                    "pair_count": candidate["pair_count"],
                    "paired_metrics": candidate["paired_metrics"],
                    "gate": candidate["gate"],
                    "disposition": candidate["disposition"],
                    "correctness_and_resources_clean": not bool(candidate["failures"]),
                }
                for index, candidate in enumerate(candidates, 1)
            ],
        },
        "frozen_phase12_rerun_shortlist": [
            {
                "role": "mandatory_unmodified_baseline",
                "name": baseline["name"],
                "configuration": compact_cell(baseline["cold"]),
                "survival_reason": "highest-throughput runtime-eligible Layout A direct cell and 99.255% of measured single-device fio ceiling",
                "required_confirmation": "rerun cold and warm on one host with real NVMe plus discrete CUDA, preserving demand, payload, top-k, arithmetic, and Phase 9/10 defaults",
            },
            {
                "role": "resource_efficient_api_comparator",
                "name": "SINGLE_NVME_LAYOUT_A_LOGICAL_DIRECT_IO_URING_QD4",
                "configuration": compact_cell(resource["cell"]),
                "throughput_delta_from_baseline": resource["throughput_delta_from_frozen_baseline"],
                "survival_reason": resource["reason"],
                "required_confirmation": "rerun beside the mandatory baseline with project transport/H2D/GPU overlap and explicit staging-resource accounting",
            },
        ],
        "winner_perfetto": {
            "rejected_attempt": identity(paths["trace_attempt_1"]) | {
                "disposition": documents["trace_attempt_1"]["disposition"],
                "failures": documents["trace_attempt_1"]["failures"],
            },
            "accepted_evidence": identity(paths["trace_attempt_2"]),
            "trace": trace["trace"],
            "trace_processor": trace["trace_processor"],
            "trace_throughput_perturbation": trace["trace_throughput_perturbation"],
            "correctness": trace["correctness"],
            "loss_counters": trace["loss_counters"],
            "request_wall_ns": request_wall,
            "critical_path": {
                "block_device_service_ns": critical["block_device_service_union_ns"],
                "block_device_service_fraction": critical["block_device_service_union_ns"] / request_wall,
                "syscall_outside_block_ns": critical["syscall_non_block_union_ns"],
                "syscall_outside_block_fraction": critical["syscall_non_block_union_ns"] / request_wall,
                "checksum_copy_ns": critical["checksum_copy_union_ns"],
                "checksum_copy_fraction": critical["checksum_copy_union_ns"] / request_wall,
                "scheduler_or_unattributed_ns": critical["scheduler_or_unattributed_union_ns"],
                "scheduler_or_unattributed_fraction": trace["unexplained_residual_fraction"],
            },
            "reopened_hypothesis": False,
        },
        "dual_nvme": {
            "corpus_evidence": identity(paths["dual_corpus"]),
            "comparison_evidence": identity(paths["dual_comparison"]),
            "configuration": dual["fixed_configuration"],
            "mapping": dual["mapping"],
            "pair_count": dual["pair_count"],
            "paired_metrics": dual["paired_metrics"],
            "per_drive": dual["dual_per_drive_summary"],
            "disposition": dual["disposition"],
        },
        "external_full_model_reference": {
            "preflight_evidence": identity(paths["colibri_preflight"]),
            "snapshot_evidence": identity(paths["colibri_snapshot"]),
            "rejected_attempt": identity(paths["colibri_attempt_1"]) | {
                "disposition": documents["colibri_attempt_1"]["disposition"],
                "failures": documents["colibri_attempt_1"]["failures"],
            },
            "accepted_evidence": identity(paths["colibri_reference"]),
            "engine_commit": colibri["colibri_commit"],
            "model_revision": colibri["model_revision"],
            "model_format": colibri["model_format"],
            "environment": colibri["environment"],
            "metrics": colibri["metrics"],
            "process_resources": colibri["process_resources"],
            "block_device": colibri["block_device"],
            "comparison_boundary": colibri["comparison_boundary"],
            "disposition": colibri["disposition"],
        },
        "real_route_cache_locality": {
            "capture_evidence": identity(paths["real_routing_capture"]),
            "replay_evidence": identity(paths["cache_locality"]),
            "capture": {
                "request_count": cache_locality["routing_corpus"]["request_count"],
                "complete_decode_token_forwards": (
                    cache_locality["routing_corpus"]["complete_decode_token_forwards"]
                ),
                "expert_requests": cache_locality["routing_corpus"]["expert_requests"],
                "useful_expert_bytes": cache_locality["routing_corpus"]["useful_expert_bytes"],
                "ordering": cache_locality["routing_corpus"]["ordering"],
                "route_captures": cache_locality["routing_corpus"]["captures"],
            },
            "policy": cache_locality["policy"],
            "ram_support_method": cache_locality["ram_support_method"],
            "capacity_curve": [compact_cache_capacity(row) for row in cache_locality["capacity_curve"]],
            "reuse": cache_locality["reuse"],
            "nvme_avoidance_thresholds": cache_locality["nvme_avoidance_thresholds"],
            "service_envelopes": cache_locality["service_envelopes"],
            "deterministic_replay_digest": cache_locality["deterministic_replay_digest"],
            "bounded_resources": cache_locality["bounded_resources"],
            "interpretation": cache_locality["interpretation"],
            "disposition": cache_locality["disposition"],
        },
        "secondary_external_comparator": {
            "status": "NOT_EXECUTED",
            "validation_status": "published full generation remains unverified for the identified native-MXFP4 comparator",
            "claim": "no secondary TPS or shortlist evidence",
        },
        "selected_generic_runtime_deltas": [],
        "evidence_only_deltas": [
            "deterministic exact corpus and route generators/verifiers",
            "fair positional-read/io_uring/mmap matrix harness and telemetry",
            "default-off accepted-Perfetto-SDK trace target and analysis",
            "dual-namespace preparation/comparison tooling",
            "Colibrì preflight/snapshot/reference capture tooling",
            "real-route capture and bounded global-LRU/ALWAYS cache-locality replay tooling",
        ],
        "phase12_import_contract": {
            "rerun_mandatory_baseline": True,
            "rerun_frozen_shortlist": True,
            "import_real_route_cache_locality_curve": True,
            "required_host": "one Linux host containing real NVMe and a discrete CUDA GPU",
            "must_add": [
                "project H2D and GPU execution correctness",
                "project storage/H2D/compute overlap",
                "project actual full-model inference and numerical/quality gates when capable",
                "final storage-format quantitative disposition",
                "explicit RAM/VRAM capacity selection from the real-route curve and actual end-to-end validation",
            ],
            "must_preserve": [
                "exact K3 source/topology/payload/seed/route identities",
                "top-16 selection, routing weights, arithmetic, and deterministic reduction order",
                "Phase 9 global LRU/ALWAYS defaults with explicit capacities",
                "Phase 10 null config/profile, no selected profiles, and speculative prefetch off",
                "accepted default-off Perfetto instrumentation surface",
            ],
        },
        "validation": {
            "checkpoint_a": identity(paths["checkpoint_a_validation"]),
            "final": identity(paths["final_validation"]),
            "final_binary": documents["final_validation"]["binary"],
            "final_commands_passed": len(documents["final_validation"]["commands"]),
        },
        "raw_evidence": identity(raw_output) | {
            "index_count": raw_document["index_count"],
            "aggregate_index_and_archive_sha256": raw_document["aggregate_index_and_archive_sha256"],
        },
        "statistical_methods": {
            "baseline_matrix": "complete deterministic screening matrix; single retained measurement per cell, not a final storage-format gate",
            "optimization_candidates": "three interleaved fresh-process cold pairs; predeclared 5% promising gate with correctness/resource falsifiers",
            "dual_nvme": "five interleaved same-host pairs; Student-t paired 95% interval",
            "colibri": "eight complete externally timestamped decode-forward intervals; bounded same-machine observation, not a format gate",
            "real_route_cache_locality": (
                "one fixed 30-token prefill plus 256 complete decode forwards; exact stack-distance and bounded "
                "global-LRU/ALWAYS replay at 0/8/16/32/64/96 GiB; storage-only miss-byte projection through "
                "accepted single/dual service envelopes"
            ),
            "percentiles": "nearest-rank for harness/tail metrics unless an evidence document explicitly states a paired interval calculation",
        },
        "gates": {
            "optimization_stop_rule": "PASS",
            "baseline_and_negative_candidates_inspectable": "PASS",
            "winner_trace_complete_and_attributed": "PASS",
            "material_unexplained_trace_residual": False,
            "runtime_reuse_delta_selected": False,
            "dual_nvme_controlled": "PASS",
            "same_machine_colibri_full_model": "PASS",
            "real_route_cache_locality": "PASS",
            "cache_policy_or_default_change": False,
            "final_corpus_physical_and_checksum_verification": "PASS",
            "phase12_importable_handoff": "PASS",
            "phase9_phase10_default_change": False,
            "project_cuda_or_h2d_claim": False,
            "final_storage_format_decision_made": False,
        },
        "limitations": [
            "The measured host is OCI VM.DenseIO.E5.Flex with 16 OCPU, 192 GB RAM, Oracle Linux 9.8, XFS, and two local NVMe namespaces; it differs from the nominal E4/Ubuntu/256 GB profile.",
            "This host has no discrete CUDA GPU. No project H2D, GPU execution, transfer-ring overlap, CUDA correctness, or project full-model inference claim is available here.",
            "The frozen single-NVMe baseline is one complete matrix observation plus adjacent traced/untraced verification; Phase 12 must rerun it and the shortlist under its confirmation methodology on NVMe plus discrete CUDA.",
            "Both causal optimization candidates were non-promising in three-pair screens; no new layout, runtime path, policy, default, or model format is selected.",
            "Dual-NVMe scaling is same-host CPU/storage-only evidence and does not establish multi-drive project GPU overlap or a production placement default.",
            "Colibrì is a separate CPU full-model runtime with int4/int8 trunk quantization and native MXFP4 experts; its actual TPS is not directly comparable to project synthetic token-equivalent throughput.",
            "The accepted Colibrì observation contains eight decode-forward samples and cannot drive a storage-format gate requiring at least 100 samples/five processes.",
            "The real-route cache-locality curve contains one fixed prompt with 256 complete decode forwards; it is sufficient for this bounded study but remains workload-specific and requires end-to-end confirmation for Phase 12 capacity selection.",
            "Cache-locality projections cover only the NVMe miss-byte service component; they do not assume linear end-to-end scaling and include no H2D, CUDA, GPU-compute, overlap, or project-TPS claim.",
            "The secondary native-MXFP4 comparator was not executed because its published full-generation path remained unverified for an authoritative TPS claim.",
            "Large raw evidence archives and the verified 1.56 TB checkpoint remain external and checksum-addressed; the retained physical synthetic corpus is reproducible but not committed to Git.",
            "No final GGUF/repack/custom-format disposition is made; that decision remains gated on the required NVMe plus discrete-CUDA Phase 12 confirmation.",
        ],
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": document["status"], "checkpoint": document["checkpoint"],
        "final_capable": document["final_capable"], "output": str(output),
        "raw_index": str(raw_output),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

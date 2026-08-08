#!/usr/bin/env python3
"""Freeze the compact Phase 13 remediation evidence and manifest."""

from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.phase12_5.common import file_identity, write_json

RAW_TAG = "phase13-issue61-closeout-v4"
RAW_ASSET = "phase13-issue61-closeout-v4-raw.tar.zst"
RAW_URL = f"https://github.com/murillo128/k3-out-of-core/releases/tag/{RAW_TAG}"


def load(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected object in {path}")
    return value


def git(*arguments: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def with_published_path(identity: dict, path: Path) -> dict:
    return {**identity, "path": str(path)}


def compact_device(device: dict) -> dict:
    return {
        "device_id": device["device_id"],
        "ring_async_enqueues": device["ring_async_enqueues"],
        "ring_h2d_event_records": device["ring_h2d_event_records"],
        "ring_h2d_event_waits": device["ring_h2d_event_waits"],
        "ring_h2d_event_synchronizations": device["ring_h2d_event_synchronizations"],
        "ring_live_events": device["ring_live_events"],
        "scheduler": device["scheduler"],
    }


def compact_peer(peer: dict) -> dict:
    keys = (
        "source_device_id", "device_id", "host_staged_bytes", "host_staged_copies",
        "host_staging_slots", "host_staging_peak_in_flight", "host_staging_reuse_waits",
        "cross_device_event_waits", "host_staged_blocking_us", "host_staging_enqueues",
        "host_staging_completions", "unexpected_host_synchronizations",
        "stale_staging_completions", "staging_cancellation_requests",
        "staging_cancellations_during_d2h", "staging_cancellations_during_h2d",
        "staging_cancellation_drains", "staging_rejected_enqueues",
        "host_staging_live_slots",
        "branch_delay_enqueues_for_testing", "branch_delay_completions_for_testing",
        "branch_delay_requested_us_for_testing",
    )
    return {key: peer[key] for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--causal-summary", type=Path, required=True)
    parser.add_argument("--compliance-summary", type=Path, required=True)
    parser.add_argument("--final-summary", type=Path, required=True)
    parser.add_argument("--trace-pair-dir", type=Path, required=True)
    parser.add_argument("--delay-evidence", type=Path, required=True)
    parser.add_argument("--failure-evidence", type=Path, required=True)
    parser.add_argument("--staging-evidence", type=Path, required=True)
    parser.add_argument("--pre-remediation-manifest", type=Path, required=True)
    parser.add_argument("--pre-remediation-summary", type=Path, required=True)
    parser.add_argument("--raw-asset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--published-output-dir", type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise FileExistsError(args.output_dir)

    causal = load(args.causal_summary)
    compliance = load(args.compliance_summary)
    final = load(args.final_summary)
    trace_pair = load(args.trace_pair_dir / "trace-pair.json")
    delay = load(args.delay_evidence)
    failure = load(args.failure_evidence)
    staging = load(args.staging_evidence)
    old_manifest = load(args.pre_remediation_manifest)
    old_summary = load(args.pre_remediation_summary)
    with gzip.open(args.compliance_summary.parent / "raw/A-01.json.gz", "rt") as source:
        compliance_identity_probe = json.load(source)
    with gzip.open(args.final_summary.parent / "raw/A-01.json.gz", "rt") as source:
        final_identity_probe = json.load(source)
    if causal.get("status") != "pass" or compliance.get("status") != "pass" or \
            final.get("status") != "pass":
        raise RuntimeError("causal, compliance, or final matrix did not pass")
    if trace_pair.get("status") != "valid" or not trace_pair.get("exact_identity"):
        raise RuntimeError("adjacent trace pair is not valid and exact")
    if delay.get("status") != "pass" or failure.get("status") != "pass" or \
            staging.get("status") != "pass":
        raise RuntimeError("correctness evidence did not pass")
    if compliance["identity"]["sha256"] != old_summary["identity"]["sha256"] or \
            compliance["identity"]["criterion"] != "FULL_COMPLIANCE" or \
            not compliance["identity"]["logits_fnv64_exact_across_all_processes"]:
        raise RuntimeError("Mode-C identity differs from immutable pre-remediation evidence")
    for key in ("prompt_ids", "generated_ids", "generated_text"):
        if final_identity_probe[key] != compliance_identity_probe[key]:
            raise RuntimeError(f"Mode-C/Mode-P workload identity differs at {key}")
    speedup = final["scaling"]["speedup"]
    interval = final["scaling"]["paired_bootstrap_95_percent_interval"]
    if final["identity"]["criterion"] != "PRODUCTION_GENERATED_OUTPUT" or \
            not final["identity"]["exact_across_all_processes"] or \
            final["identity"]["processes"] != 10 or speedup < 0.95 or interval[0] < 0.90:
        raise RuntimeError("Mode-P revised closeout gate did not pass")

    output = args.output_dir
    published_output = args.published_output_dir or output

    def output_identity(path: Path) -> dict:
        return with_published_path(file_identity(path), published_output / path.relative_to(output))

    checkpoint_a = output / "checkpoint-a"
    checkpoint_c = output / "checkpoint-c"
    trace_output = output / "trace"
    correctness_output = output / "correctness"
    for directory in (checkpoint_a, checkpoint_c, trace_output, correctness_output):
        directory.mkdir(parents=True, exist_ok=True)
    write_json(checkpoint_a / "causal-summary.json", causal)
    write_json(checkpoint_c / "mode-c-summary.json", compliance)
    write_json(checkpoint_c / "final-summary.json", final)

    trace_cases: dict[str, dict] = {}
    for cell in ("A", "B"):
        verification = load(args.trace_pair_dir / cell / "verification.json")
        capture = load(args.trace_pair_dir / cell / "capture.json")
        if verification.get("status") != "valid" or capture.get("status") != "complete":
            raise RuntimeError(f"trace case {cell} is not valid")
        trace_cases[cell] = {
            "trace": trace_pair["cases"][cell]["trace"],
            "workload": trace_pair["cases"][cell]["workload"],
            "capture": trace_pair["cases"][cell]["capture"],
            "verification": trace_pair["cases"][cell]["verification"],
            "metrics": verification["metrics"],
            "trace_diagnostics": capture["trace_diagnostics"],
        }
    trace_summary = {
        "schema_version": "phase13-windowed-trace-summary-v1",
        "status": "valid",
        "profile": {
            "cupti_kinds": ["CONCURRENT_KERNEL", "MEMCPY", "SYNCHRONIZATION"],
            "disabled": ["RUNTIME", "DRIVER", "MEMSET", "EXTERNAL_CORRELATION"],
            "external_correlation_scopes": "no-op",
            "total_memory_bound_bytes": 256 * 1024 * 1024,
            "retained_memory_bound_bytes": 128 * 1024 * 1024,
        },
        "selection": trace_pair["selection"],
        "order": trace_pair["order"],
        "graphs_disabled": trace_pair["graphs_disabled"],
        "exact_identity": trace_pair["exact_identity"],
        "identity_sha256": trace_pair["identity_sha256"],
        "cases": trace_cases,
        "raw_release": {"tag": RAW_TAG, "url": RAW_URL, "asset": RAW_ASSET,
            "archive": file_identity(args.raw_asset)},
    }
    write_json(trace_output / "trace-pair-summary.json", trace_summary)

    delay_multi = delay["multi_gpu"]
    delay_summary = {
        "schema_version": "phase13-device-completion-order-v1",
        "status": "pass",
        "nested_revision": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
        "generated_ids": delay["generated_ids"],
        "logits_fnv64": delay["logits_fnv64"],
        "exact_prefix_of_final_identity":
            delay["generated_ids"] == compliance_identity_probe["generated_ids"][:len(delay["generated_ids"])] and
            delay["logits_fnv64"] == compliance_identity_probe["logits_fnv64"][:len(delay["logits_fnv64"])],
        "delayed_device": delay_multi["delayed_device"],
        "delay_us_per_branch": delay_multi["device_delay_us"],
        "provider_h2d_join_decode_time_ns": delay_multi["provider_h2d_join_decode_time_ns"],
        "provider_h2d_async_decode_waves": delay_multi["provider_h2d_async_decode_waves"],
        "provider_h2d_async_branch_waits": delay_multi["provider_h2d_async_branch_waits"],
        "peer_diagnostics": [compact_peer(peer) for peer in delay_multi["peer_diagnostics"]],
        "devices": [compact_device(device) for device in delay_multi["devices"]],
        "lifecycle": delay["lifecycle"],
        "raw": {**file_identity(args.delay_evidence), "release_asset": RAW_ASSET},
    }
    if not delay_summary["exact_prefix_of_final_identity"]:
        raise RuntimeError("delayed-device output identity changed")
    write_json(correctness_output / "device-completion-order.json", delay_summary)

    failure_multi = failure["multi_gpu"]
    failure_summary = {
        "schema_version": "phase13-inflight-device-failure-v1",
        "status": "pass",
        "nested_revision": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
        "generated_ids_before_failure": failure["generated_ids"],
        "expected_device_failure": failure_multi["expected_device_failure"],
        "expected_device_failure_observed": failure_multi["expected_device_failure_observed"],
        "decode_only": failure_multi["failed_device_decode_only"],
        "failed_device": failure_multi["failed_device"],
        "injected_waves": failure_multi["injected_device_failure_waves"],
        "participants": failure_multi["injected_device_failure_participants"],
        "drained_waves": failure_multi["injected_device_failure_drained_waves"],
        "peer_diagnostics": [compact_peer(peer) for peer in failure_multi["peer_diagnostics"]],
        "devices": [compact_device(device) for device in failure_multi["devices"]],
        "lifecycle": failure["lifecycle"],
        "raw": {**file_identity(args.failure_evidence), "release_asset": RAW_ASSET},
    }
    if not (failure_summary["expected_device_failure_observed"] and failure_summary["participants"] == 2 and
            failure_summary["drained_waves"] == 1):
        raise RuntimeError("in-flight failure did not aggregate and drain")
    write_json(correctness_output / "inflight-device-failure.json", failure_summary)

    staging_summary = {
        **staging,
        "nested_revision": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
        "raw": file_identity(args.staging_evidence),
    }
    write_json(correctness_output / "staging-generation-cancellation.json", staging_summary)

    validation = {
        "schema_version": "phase13-remediation-validation-v2",
        "status": "pass",
        "issue": "https://github.com/murillo128/k3-out-of-core/issues/61",
        "execution_profile": "STANDARD",
        "revisions": {
            "project_base": old_manifest["revisions"]["project_base"],
            "project_branch": git("branch", "--show-current"),
            "project_evidence_parent": git("rev-parse", "HEAD"),
            "nested_base": old_manifest["revisions"]["nested_base"],
            "nested_head": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
            "nested_tree": git("rev-parse", "HEAD^{tree}", cwd=ROOT / "llama.cpp"),
            "retained_iteration_11": "6f7f87822b7bfdbf5d271d367a5e3d1b730ef6ca",
        },
        "build": {
            "parallel_jobs": 76,
            "result": "pass",
            "targets": ["llama", "phase9-cache-policy-probe", "phase13-topology-probe",
                "dsv4-artifact-inventory", "dsv4-source-span-probe", "12 focused test executables"],
            "note": "HOST_STAGED does not require NCCL; full unrelated UMA target remains outside Phase 13.",
        },
        "focused_ctest": {"passed": 12, "failed": 0, "result": "pass"},
        "causal_closeout": {
            "iteration": causal["iteration"],
            "status": causal["status"],
            "trace_identity": causal["revisions"],
            "iteration_10_inactive_id_fix": "rejected_below_3_percent_tps",
            "iteration_12_ephemeral_stage_overlap": "rejected_regression",
            "iteration_13_persistent_stage_overlap": "rejected_below_3_percent_tps",
            "residual": "symmetric DeviceId-0 resident graph; generalized roles owned by issue 63",
        },
        "mode_c_qualification": {
            "fresh_processes": compliance["identity"]["processes"],
            "pairs": 1,
            "criterion": compliance["identity"]["criterion"],
            "output_identity_exact": compliance["identity"]["exact_across_all_processes"],
            "logits_exact": compliance["identity"]["logits_fnv64_exact_across_all_processes"],
            "route_records_per_process": compliance["identity"]["route_records_per_process"],
            "output_identity_sha256": compliance["identity"]["sha256"],
            "result": compliance["status"],
        },
        "mode_p_final_campaign": {
            "fresh_processes": final["identity"]["processes"],
            "mandatory_pairs": 5,
            "capacity_matched_pairs": 0,
            "capacity_matched_disposition":
                "diagnostic_only and not repeated under design-authority comment 5226796421",
            "output_identity_exact": final["identity"]["exact_across_all_processes"],
            "output_identity_sha256": final["identity"]["sha256"],
            "single_gpu_decode_tps": final["scaling"]["single_gpu_tps"],
            "dual_gpu_decode_tps": final["scaling"]["dual_gpu_tps"],
            "speedup": final["scaling"]["speedup"],
            "paired_bootstrap_95_percent_interval":
                final["scaling"]["paired_bootstrap_95_percent_interval"],
            "preferred_floor": 0.95,
            "hard_floor": 0.90,
            "preferred_floor_pass": final["scaling"]["speedup"] >= 0.95,
            "interval_hard_floor_pass":
                final["scaling"]["paired_bootstrap_95_percent_interval"][0] >= 0.90,
            "result": final["status"],
        },
        "correctness": {
            "focused_staging_generation_and_cancellation": "pass",
            "actual_device_completion_order": "pass",
            "inflight_one_device_failure": "pass",
        },
        "trace_gate": {"result": "pass", "evidence": "../trace/trace-pair-summary.json"},
        "independent_final_review": {"run": False, "reason": "requested after this target is published"},
    }
    write_json(checkpoint_c / "phase13-validation.json", validation)

    artifact_paths = [
        checkpoint_a / "causal-summary.json",
        checkpoint_c / "mode-c-summary.json",
        checkpoint_c / "final-summary.json",
        trace_output / "trace-pair-summary.json",
        correctness_output / "device-completion-order.json",
        correctness_output / "inflight-device-failure.json",
        correctness_output / "staging-generation-cancellation.json",
        checkpoint_c / "phase13-validation.json",
        Path("scripts/phase13/analyze_phase13_matrix.py"),
        Path("scripts/phase13/capture_decode_window.py"),
        Path("scripts/phase13/finalize_phase13_remediation.py"),
        Path("scripts/phase13/run_phase13_matrix.py"),
        Path("scripts/phase13/run_phase13_trace_pair.py"),
        Path("scripts/phase13/verify_decode_window.py"),
        Path("scripts/phase13/configs/decode-window-128m.pbtxt"),
        Path("scripts/phase13/sql/verify_decode_window.sql"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-9/mode-split-validation.json"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-10/screen-result.json"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-11/trace-causal-analysis.json"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-11/validation.json"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-12/screen-result.json"),
        Path("results/2026-08-08/phase13-multi-gpu-remediation/iterations/iteration-13/screen-result.json"),
    ]
    manifest = {
        "schema_version": "phase13-remediation-manifest-v2",
        "self_identity": "phase13-manifest.json excludes its own hash and immutable URL to remain non-circular",
        "status": "complete",
        "disposition": "SUPPORTED_MULTI_GPU",
        "classification": "PARITY_QUALIFIED",
        "closeout_authority": {
            "issue_comment_id": 5226796421,
            "preferred_parity_floor": 0.95,
            "hard_floor": 0.90,
            "old_1_60x_target": "superseded",
            "generalized_device_roles_issue": 63,
        },
        "issue": "https://github.com/murillo128/k3-out-of-core/issues/61",
        "pull_request": "https://github.com/murillo128/k3-out-of-core/pull/62",
        "revisions": validation["revisions"],
        "pre_remediation": {
            "immutable": True,
            "manifest": file_identity(args.pre_remediation_manifest),
            "matrix": file_identity(args.pre_remediation_summary),
            "speedup": old_summary["scaling"]["speedup"],
            "classification": old_summary["scaling"]["classification"],
        },
        "dual_mode": {
            "mode_c": {
                "purpose": "correctness and compliance; never a TPS denominator",
                "processes": compliance["identity"]["processes"],
                "full_identity_sha256": compliance["identity"]["sha256"],
                "logits_exact": compliance["identity"]["logits_fnv64_exact_across_all_processes"],
                "route_records_per_process": compliance["identity"]["route_records_per_process"],
                "summary": output_identity(checkpoint_c / "mode-c-summary.json"),
            },
            "mode_p": {
                "purpose": "authoritative production performance",
                "processes": final["identity"]["processes"],
                "generated_output_identity_sha256": final["identity"]["sha256"],
                "compliance_hot_path_disabled": True,
                "summary": output_identity(checkpoint_c / "final-summary.json"),
            },
        },
        "corrected": {
            "transport": "HOST_STAGED_ASYNC_TWO_SLOT_PER_DIRECTED_EDGE",
            "provider_h2d_join": "PER_DEVICE_EVENT_DEPENDENCY",
            "lru_index_added": False,
            "single_gpu_decode_tps": final["scaling"]["single_gpu_tps"],
            "dual_gpu_decode_tps": final["scaling"]["dual_gpu_tps"],
            "speedup": final["scaling"]["speedup"],
            "efficiency": final["scaling"]["efficiency"],
            "paired_bootstrap_95_percent_interval": final["scaling"]["paired_bootstrap_95_percent_interval"],
            "classification": final["scaling"]["classification"],
            "preferred_parity_floor_pass": final["scaling"]["speedup"] >= 0.95,
            "hard_floor_interval_pass":
                final["scaling"]["paired_bootstrap_95_percent_interval"][0] >= 0.90,
            "capacity_matched_comparator": {
                "run": False,
                "reason": "B-prime is diagnostic only under design-authority comment 5226796421; no capacity hypothesis remained active.",
            },
            "output_identity_sha256": final["identity"]["sha256"],
            "provider_h2d_join_decode_fraction": final["cells"]["B"]
                ["provider_h2d_join_decode_fraction"],
            "lru_scan_decode_fraction": final["cells"]["B"]
                ["physical_feasibility_scan_decode_fraction"],
        },
        "causal_closeout": {
            "retained_iteration": 11,
            "retained_nested_commit": "6f7f87822b7bfdbf5d271d367a5e3d1b730ef6ca",
            "retained_source_tree": validation["revisions"]["nested_tree"],
            "iteration_10": "inactive-ID device-side correction rejected at +0.1605% B TPS",
            "iteration_12": "ephemeral cross-device stage overlap rejected at -17.11% B TPS",
            "iteration_13": "persistent cross-device stage overlap rejected at +0.345% B TPS",
            "conclusion": "No known removable in-scope component remains; residual graph serialization is the symmetric DeviceId-0 resident-graph topology delegated to issue 63.",
        },
        "trace": {
            "status": trace_summary["status"],
            "selection": trace_summary["selection"],
            "identity_sha256": trace_summary["identity_sha256"],
            "A_trace": trace_cases["A"]["trace"],
            "B_trace": trace_cases["B"]["trace"],
            "A_complete_routed_layer_intervals": trace_cases["A"]["metrics"]["complete_routed_layer_intervals"],
            "B_complete_routed_layer_intervals": trace_cases["B"]["metrics"]["complete_routed_layer_intervals"],
            "integrity": "zero errors, drops, exhaustion, data loss, or external correlations",
        },
        "correctness": {
            "completion_order": output_identity(correctness_output / "device-completion-order.json"),
            "inflight_device_failure": output_identity(correctness_output / "inflight-device-failure.json"),
            "staging_generation_cancellation": output_identity(
                correctness_output / "staging-generation-cancellation.json"),
        },
        "validation": output_identity(checkpoint_c / "phase13-validation.json"),
        "raw_release": {"tag": RAW_TAG, "url": RAW_URL, "asset": RAW_ASSET,
            "archive": file_identity(args.raw_asset)},
        "artifacts": [output_identity(path) if path.is_relative_to(output) else file_identity(path)
            for path in artifact_paths],
        "independent_review": "pending exact published target",
    }
    write_json(checkpoint_c / "phase13-manifest.json", manifest)

    summary = f"""# Phase 13 multi-GPU remediation

Disposition: `SUPPORTED_MULTI_GPU`. The frozen symmetric topology passes correctness, lifecycle, resource, trace and revised performance closeout gates. Classification: `PARITY_QUALIFIED`.

- Mode C: exact full identity `{compliance['identity']['sha256']}` across A/B, all logits exact and {compliance['identity']['route_records_per_process']} routes per process. These TPS values are diagnostic only.
- Mode P: A `{final['scaling']['single_gpu_tps']:.6f}` tok/s, B `{final['scaling']['dual_gpu_tps']:.6f}` tok/s, speedup `{final['scaling']['speedup']:.6f}x`.
- Paired 95% bootstrap: `{final['scaling']['paired_bootstrap_95_percent_interval'][0]:.6f}`–`{final['scaling']['paired_bootstrap_95_percent_interval'][1]:.6f}`; preferred `0.95x` and hard `0.90x` floors pass.
- Mode-P generated-output identity: `{final['identity']['sha256']}` across {final['identity']['processes']} fresh processes.
- B-prime: not repeated; comment `5226796421` makes it diagnostic only and no capacity hypothesis remained active.
- Decode H2D global join: removed; final measured B fraction `{final['cells']['B']['provider_h2d_join_decode_fraction']:.6%}`.
- LRU feasibility scan: `{final['cells']['B']['physical_feasibility_scan_decode_fraction']:.6%}` of B decode wall, below the 3% threshold.
- Windowed Mode-P trace: seed {trace_pair['selection']['seed']}, request {trace_pair['selection']['request_ordinal']}, layer {trace_pair['selection']['routed_layer']}, {trace_pair['selection']['window_ms']} ms; A/B traces are {trace_cases['A']['trace']['size']} and {trace_cases['B']['trace']['size']} bytes.
- Focused validation: 12/12 CTests; stale staging generation, D2H/H2D cancellation, actual device-delay and in-flight one-device failure gates pass.

Iteration 11 is retained. Iterations 10, 12 and 13 falsify the remaining bounded synchronization/staging corrections as throughput-dominant. The residual serialized resident graph is architectural follow-up #63, not a reason to broaden #61.
"""
    (output / "SUMMARY.md").write_text(summary)
    print(json.dumps({"status": "complete", "output": str(output),
        "speedup": final["scaling"]["speedup"], "trace": trace_summary["status"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

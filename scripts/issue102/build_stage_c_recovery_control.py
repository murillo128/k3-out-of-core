#!/usr/bin/env python3
"""Freeze the amended issue-102 Stage-C output-hygiene recovery contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
from typing import Any


EXPECTED_PREREGISTRATION_SHA256 = "c368ca9e8d0e35291e4be9e81747122275c7cd594e4603d570bafc0e7595d256"
EXPECTED_ORIGINAL_PROGRESS_SHA256 = "fa516a2eef9f71ac0065516d1dab4ad61b0e43204400ed929a3414737a85d1a0"
EXPECTED_TECHNICAL_RETURN_SHA256 = "d3b7deb89ae520f8224f30ea3e0d761440ba2c95022ed3234d38290351b41584"
EXPECTED_OBSERVER_ALLOWLIST_SHA256 = "2c226965c7b26220dbb0801d48c1fd5e548f84c090bdfc650a3845bd76099887"
EXPECTED_HANDOFF_SHA256 = "e560db9dfc1c05c8aa163d88b44073bebbfbafefb4c86dfbf78f075c3b403eca"
EXPECTED_ROUTE_INDEX_SHA256 = "c4eeb570f760dbe1fdeb38ccddb783aa6095a75af98ae5489bbf8d12a57cdb7d"
EXPECTED_REPLAY_INDEX_SHA256 = "d49db861f81ade803ab3d9c9e07c10d803247d2f3bcaf15b84d8afbf8a9b3dac"
EXPECTED_POSTHOC_INDEX_SHA256 = "1a7e42c0d549ccd9486a91b24e89b172d2848b1f80874e0cd40986d273d60111"
EXPECTED_HYGIENE_REFERENCE_SHA256 = "3e7df357e5c32331b099464e50adaa7b6431b7bd205105326addaa14d761f41f"
EXPECTED_NESTED_SHA = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
AUTHORITY_URL = "https://github.com/murillo128/k3-out-of-core/issues/102#issuecomment-5307903809"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-project-sha", required=True)
    parser.add_argument("--preregistration", type=pathlib.Path, required=True)
    parser.add_argument("--original-progress", type=pathlib.Path, required=True)
    parser.add_argument("--technical-return", type=pathlib.Path, required=True)
    parser.add_argument("--observer-allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--handoff", type=pathlib.Path, required=True)
    parser.add_argument("--route-index", type=pathlib.Path, required=True)
    parser.add_argument("--replay-index", type=pathlib.Path, required=True)
    parser.add_argument("--posthoc-index", type=pathlib.Path, required=True)
    parser.add_argument("--hygiene-reference", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--control-root", type=pathlib.Path, required=True)
    parser.add_argument("--progress", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path, expected: str | None = None) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    result = {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }
    if expected is not None and result["sha256"] != expected:
        raise ValueError(f"control input identity changed: {resolved}")
    return result


def load(path: pathlib.Path, expected: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source = identity(path, expected)
    with path.resolve(strict=True).open() as stream:
        return source, json.load(stream)


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def main() -> None:
    args = arguments()
    if git_output("cat-file", "-t", args.execution_project_sha) != "commit":
        raise ValueError("execution project target is not a local commit")
    if git_output("-C", "llama.cpp", "rev-parse", "HEAD") != EXPECTED_NESTED_SHA:
        raise ValueError("nested llama.cpp target changed")

    prereg_id, prereg = load(args.preregistration, EXPECTED_PREREGISTRATION_SHA256)
    original_id, original = load(args.original_progress, EXPECTED_ORIGINAL_PROGRESS_SHA256)
    technical_id, technical = load(args.technical_return, EXPECTED_TECHNICAL_RETURN_SHA256)
    observer_id, observer = load(args.observer_allowlist, EXPECTED_OBSERVER_ALLOWLIST_SHA256)
    handoff_id, handoff = load(args.handoff, EXPECTED_HANDOFF_SHA256)
    route_id, route = load(args.route_index, EXPECTED_ROUTE_INDEX_SHA256)
    replay_id, replay = load(args.replay_index, EXPECTED_REPLAY_INDEX_SHA256)
    posthoc_id, posthoc = load(args.posthoc_index, EXPECTED_POSTHOC_INDEX_SHA256)
    reference_id, reference = load(args.hygiene_reference, EXPECTED_HYGIENE_REFERENCE_SHA256)
    failure = original.get("failures", [{}])[0]
    if (
        prereg.get("schema_version") != "phase13-6pg-stage-c-preregistration-v1"
        or prereg.get("status") != "frozen"
        or len(prereg.get("plan", [])) != 48
        or prereg["configuration"]["cache_bytes"] != 137728475136
        or prereg["configuration"]["cache_slots"] != 7849
        or prereg["configuration"]["n_ctx"] != 768
        or prereg["configuration"]["threads"] != 32
        or prereg["configuration"]["decode_forwards"] != 64
        or prereg["nested_llama_cpp_sha"] != EXPECTED_NESTED_SHA
        or original.get("status") != "failed"
        or original.get("accepted_cell_count") != 0
        or original.get("failed_cell_count") != 1
        or failure.get("run_ordinal") != 1
        or failure.get("case_id") != "01-math-b6"
        or failure.get("point") != "EXACT"
        or set(failure.get("artifacts", {})) != {"envelope.json", "stderr.log", "stdout.log"}
        or technical.get("status") != "blocked"
        or technical.get("classification") != "PRE_CONTEXT_CAPACITY_ADMISSION_FAILURE"
        or observer.get("file_count") != 182
        or handoff.get("status") != "pass"
        or route.get("status") != "pass"
        or replay.get("status") != "pass"
        or posthoc.get("status") != "pass"
        or reference.get("status") != "frozen"
    ):
        raise ValueError("amended Stage-C prerequisites changed")

    source_dir = pathlib.Path(__file__).resolve(strict=True).parent
    runtime = {
        "controller": identity(source_dir / "run_stage_c_recovery_campaign.py"),
        "allowlist_builder": identity(source_dir / "build_stage_c_output_cache_allowlist.py"),
        "hygiene_executor": identity(source_dir / "apply_stage_c_output_cache_hygiene.py"),
        "cache_operations": identity(source_dir / "release_observer_evidence_page_cache.py"),
        "generator": identity(pathlib.Path(__file__)),
        "helper_binary": prereg["runtime"]["helper_binary"],
        "qualification_runner": prereg["runtime"]["runner"],
        "model_first_shard": prereg["runtime"]["model_first_shard"],
    }
    for name in ("helper_binary", "qualification_runner"):
        identity(pathlib.Path(runtime[name]["path"]), runtime[name]["sha256"])
    pathlib.Path(runtime["model_first_shard"]).resolve(strict=True)

    output_root = args.output_root.resolve(strict=False)
    control_root = args.control_root.resolve(strict=False)
    progress_path = args.progress.resolve(strict=False)
    if output_root.exists() or progress_path.exists():
        raise ValueError("amended Stage-C outcome/progress exists before control freeze")
    if progress_path.parent != control_root:
        raise ValueError("amended progress is outside its exact control root")
    plan = []
    for original_plan in prereg["plan"]:
        row = dict(original_plan)
        row["attempt_kind"] = "RECOVERY_01_SOLE_SCIENTIFIC_REALIZATION" if row["run_ordinal"] == 1 else "FROZEN_CONTINUATION"
        if row["run_ordinal"] == 1:
            row["output_name"] = "recovery-01-run-001-01-math-b6-exact"
        plan.append(row)

    output = {
        "schema_version": "phase13-6pg-stage-c-recovery-control-v1",
        "status": "frozen",
        "provenance": "PREREGISTERED_RECOVERY_CONTROL_BEFORE_ANY_RECOVERY_OUTCOME",
        "execution_project_sha": args.execution_project_sha,
        "nested_llama_cpp_sha": EXPECTED_NESTED_SHA,
        "authority": {
            "issue": 102,
            "url": AUTHORITY_URL,
            "classification": "STAGE_C_EVIDENCE_PAGECACHE_ADMISSION_DRIFT",
            "supersedes_original_technical_return_for_execution_authority_only": True,
            "original_failure_and_technical_return_preserved_unchanged": True,
            "scientific_contract_changed": False,
        },
        "inputs": {
            "stage_c_preregistration": prereg_id,
            "original_failed_progress": original_id,
            "original_technical_return": technical_id,
            "observer_output_allowlist": observer_id,
            "stage_b_capacity_handoff": handoff_id,
            "route_analysis_index": route_id,
            "observer_replay_index": replay_id,
            "posthoc_analysis_index": posthoc_id,
            "hygiene_reference": reference_id,
            "corpus": prereg["inputs"]["corpus"],
            "model_identity": prereg["inputs"]["model_identity"],
            "build_fingerprint": prereg["inputs"]["build_fingerprint"],
        },
        "runtime": runtime,
        "configuration": prereg["configuration"],
        "output": {
            "root": str(output_root),
            "control_root": str(control_root),
            "progress": str(progress_path),
            "root_existed_at_freeze": False,
            "progress_existed_at_freeze": False,
        },
        "output_cache_hygiene": {
            "before_every_physical_process": True,
            "classes": {
                "A": "STAGE_B_OBSERVER_OUTPUTS_EXACT_182",
                "B": "COMPLETED_SYNTHESIS_REPLAY_COUNTERFACTUAL_POSTHOC_OUTPUTS_EXACT_14",
                "C": "COMPLETED_STAGE_C_PROCESS_OUTPUTS_AFTER_DURABLE_VALIDATION_AND_HASH",
            },
            "initial_expected_file_count": 199,
            "initial_class_file_counts": {
                "A_OBSERVER_OUTPUT": 182,
                "B_POSTPROCESSING_OUTPUT": 14,
                "C_STAGE_C_OUTPUT": 3,
            },
            "operation": "POSIX_FADV_DONTNEED_AFTER_SYNCFS_NVME1",
            "payload_reread_or_rehash_after_release": False,
            "excluded": [
                "GGUF_OR_MODEL_OR_EXPERT_FILES", "CORPUS_OR_PROMPT_INPUTS",
                "STAGE_C_SELECTION_PREREGISTRATION_OR_CONTROL_INPUTS",
                "RUNTIME_LIBRARIES_EXECUTABLES_OR_HELPERS", "SOURCE_FILES",
                "OS_OR_GLOBAL_CACHE_STATE",
            ],
            "drop_caches_swap_cgroup_sysctl_or_admission_bypass_allowed": False,
        },
        "plan": plan,
        "recovery_policy": {
            "recovery_attempt_budget": 1,
            "recovery_attempt_name": "recovery-01",
            "recovery_only_for_original_frozen_cell_001": True,
            "later_cell_retry_budget": 0,
            "original_pre_result_failure_is_not_a_scientific_realization": True,
            "successful_recovery_is_sole_scientific_realization": True,
            "recovery_failure": "STOP_PRESERVE_AND_RETURN_TO_DESIGN",
            "later_failure": "STOP_PRESERVE_NO_RETRY",
        },
        "outcome_inspection": {
            "recovery_processes_started": 0,
            "recovery_outcomes_inspected": 0,
            "later_stage_c_processes_started": 0,
            "policy_selection_order_capacity_or_topology_changed": False,
        },
        "disposition": "READY_AFTER_PUBLICATION_AND_INDEPENDENT_REVIEW_FOR_VERIFY_ONLY_THEN_RECOVERY_01",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({"status": "pass", "output": identity(args.output)}, sort_keys=True))


if __name__ == "__main__":
    main()

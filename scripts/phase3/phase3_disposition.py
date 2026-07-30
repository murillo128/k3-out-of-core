#!/usr/bin/env python3
"""Build and validate the narrow issue #13 Phase 3 design disposition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common import sha256


RESULTS_RELATIVE = Path("results/2026-07-29/skynet/phase3-resident-provider")
CAPTURE_NAME = "provider-overhead-post-optimization.json"
CAPTURE_RELATIVE = str(RESULTS_RELATIVE / CAPTURE_NAME)
CAPTURE_SHA256 = "23eff115b87a9e8cee101bd1c0b02f299786175e786b4b30dd4a7e66617d4970"
STANDING_EVIDENCE_COMMIT = "93635d7ece8fdc617291d5a036bda1c8bc2b6c77"
DESIGN_AUTHORITY_COMMENT_IDS = [5128658370, 5128726338]
LLAMA_CPP_CANDIDATE = "a120de8e2d0b552c51eacd7d701ef1dd994bc3db"
EXPECTED_WAIVED_CELLS = [
    {
        "artifact": "mxfp4",
        "backend": "cuda",
        "comparison": "disabled-vs-resident",
        "metric": "prompt_tokens_per_second",
        "paired_mean_slowdown": 0.016570443496638908,
        "one_sided_95_percent_upper_bound": 0.03989153360974916,
        "original_budget": 0.02400604,
    },
    {
        "artifact": "mxfp4",
        "backend": "cuda",
        "comparison": "disabled-vs-resident",
        "metric": "ttft_seconds",
        "paired_mean_slowdown": 0.018462337140491426,
        "one_sided_95_percent_upper_bound": 0.0438654800269314,
        "original_budget": 0.02400604,
    },
]
EXPECTED_DECODE_REFERENCE = {
    "artifact": "mxfp4",
    "backend": "cuda",
    "comparison": "disabled-vs-resident",
    "metric": "decode_tokens_per_second",
    "one_sided_95_percent_upper_bound": 0.0016898439137899856,
    "original_budget": 0.00988906,
}
PREREQUISITE_NAMES = (
    "correctness",
    "lifecycle",
    "structural_zero_work",
    "graph",
    "scope",
    "evidence_integrity",
)
NON_PRECEDENT_GATES = [
    "later-cache-performance",
    "later-transport-performance",
    "later-miss-performance",
    "full-size-performance",
    "multi-request-performance",
    "tail-latency",
    "correctness",
    "default-path-performance",
    "steady-state-decode",
]


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _comparison(overhead: dict[str, Any], artifact: str, backend: str, name: str) -> dict[str, Any]:
    matches = [
        comparison
        for combination in overhead.get("combinations", [])
        if combination.get("artifact") == artifact and combination.get("backend") == backend
        for comparison in combination.get("comparisons", [])
        if comparison.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {artifact}/{backend}/{name} comparison, found {len(matches)}")
    return matches[0]


def derive_disposition(root: Path) -> dict[str, Any]:
    """Derive the disposition from immutable evidence and enforce the approved values."""
    result_root = root / RESULTS_RELATIVE
    capture_path = result_root / CAPTURE_NAME
    if sha256(capture_path) != CAPTURE_SHA256:
        raise ValueError("standing capture identity differs from the design-authority amendment")
    overhead = _load(capture_path)
    parity = _load(result_root / "provider-parity-post-optimization.json")
    lifecycle = _load(result_root / "lifecycle-and-failures-post-optimization.json")
    prerequisites = _load(result_root / "corrective-prerequisites.json")

    cells: list[dict[str, Any]] = []
    for combination in overhead.get("combinations", []):
        for comparison in combination.get("comparisons", []):
            for metric, analysis in comparison.get("analysis", {}).items():
                cells.append({
                    "artifact": combination.get("artifact"),
                    "backend": combination.get("backend"),
                    "comparison": comparison.get("name"),
                    "metric": metric,
                    "paired_mean_slowdown": analysis.get("paired_mean_relative_b_slowdown"),
                    "one_sided_95_percent_upper_bound": analysis.get("one_sided_95_percent_upper_bound"),
                    "original_budget": analysis.get("fixed_budget"),
                    "passed": analysis.get("passed"),
                })
    waived_cells = [
        {key: value for key, value in cell.items() if key != "passed"}
        for cell in cells if cell.get("passed") is not True
    ]
    if waived_cells != EXPECTED_WAIVED_CELLS:
        raise ValueError("standing capture failed cells or approved statistics differ")
    if overhead.get("status") != "fail" or len(cells) != 24 or sum(cell["passed"] is True for cell in cells) != 22:
        raise ValueError("standing capture is not the approved raw 22/24 failure")
    baseline_cells = [cell for cell in cells if cell["comparison"] == "baseline-vs-disabled"]
    decode_cells = [cell for cell in cells if cell["metric"] == "decode_tokens_per_second"]
    if len(baseline_cells) != 12 or not all(cell["passed"] is True for cell in baseline_cells):
        raise ValueError("not every baseline-to-disabled cell passes")
    if len(decode_cells) != 8 or not all(cell["passed"] is True for cell in decode_cells):
        raise ValueError("not every decode cell passes")
    decode = _comparison(overhead, "mxfp4", "cuda", "disabled-vs-resident")["analysis"]["decode_tokens_per_second"]
    decode_reference = {
        "artifact": "mxfp4",
        "backend": "cuda",
        "comparison": "disabled-vs-resident",
        "metric": "decode_tokens_per_second",
        "one_sided_95_percent_upper_bound": decode.get("one_sided_95_percent_upper_bound"),
        "original_budget": decode.get("fixed_budget"),
    }
    if decode_reference != EXPECTED_DECODE_REFERENCE:
        raise ValueError("standing MXFP4 CUDA decode reference differs")

    parity_cases = parity.get("cases", [])
    correctness = parity.get("status") == "pass" and len(parity_cases) == 4 and all(
        all(case.get("checks", {}).values()) for case in parity_cases
    )
    disabled_zero_names = (
        "objects", "bind_calls", "prepare_calls", "handles_acquired", "handles_released",
        "allocations", "callbacks", "tensor_copies", "synchronizations", "failures", "cancellations",
    )
    structural = correctness and all(
        case.get("checks", {}).get("disabled_structural_zero") is True
        and all(case.get("provider_stats", {}).get("disabled", {}).get(name) == 0 for name in disabled_zero_names)
        for case in parity_cases
    )
    graph = correctness and all(
        case.get("checks", {}).get("graph_topology_exact") is True
        and case.get("checks", {}).get("graph_reuse_exact") is True
        for case in parity_cases
    )
    lifecycle_coverage = lifecycle.get("coverage", {})
    lifecycle_booleans = [value for value in lifecycle_coverage.values() if isinstance(value, bool)]
    lifecycle_pass = (
        lifecycle.get("status") == "pass"
        and bool(lifecycle_booleans)
        and all(lifecycle_booleans)
        and lifecycle_coverage.get("cpu_model_load_decode_unload_cycles", 0) >= 20
        and lifecycle_coverage.get("cuda_model_load_decode_unload_cycles", 0) >= 10
    )
    prerequisite_checks = prerequisites.get("checks", {})
    scope = prerequisites.get("status") == "pass" and all(
        prerequisite_checks.get(name) is True
        for name in ("nested_scope_bounded", "project_scope_bounded", "scope_whitespace_clean")
    )
    integrity = prerequisites.get("status") == "pass" and all(prerequisite_checks.values())
    prerequisite_state = {
        "correctness": correctness,
        "lifecycle": lifecycle_pass,
        "structural_zero_work": structural,
        "graph": graph,
        "scope": scope,
        "evidence_integrity": integrity,
    }
    if not all(prerequisite_state.values()):
        raise ValueError(f"non-performance prerequisite differs: {prerequisite_state}")

    return {
        "schema_version": "phase3-disposition-v1",
        "repository": "murillo128/k3-out-of-core",
        "issue": 13,
        "design_authority_comment_ids": DESIGN_AUTHORITY_COMMENT_IDS,
        "disposition": "accepted-with-notes",
        "progression_scope": "phase3-only",
        "standing_evidence_commit": STANDING_EVIDENCE_COMMIT,
        "raw_performance": {
            "status": "fail",
            "capture": {"path": CAPTURE_RELATIVE, "sha256": CAPTURE_SHA256},
            "metric_cells": {"passed": 22, "total": 24},
            "waived_cells": waived_cells,
            "mxfp4_cuda_decode_reference": decode_reference,
            "all_baseline_to_disabled_cells_pass": True,
            "all_decode_cells_pass": True,
        },
        "prerequisites": prerequisite_state,
        "authorization": {
            "further_phase3_optimization_authorized": False,
            "further_phase3_capture_authorized": False,
        },
        "non_precedent_gates": NON_PRECEDENT_GATES,
    }


def validate_disposition(root: Path, disposition: dict[str, Any], errors: list[str]) -> None:
    try:
        expected = derive_disposition(root)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        errors.append(f"cannot derive approved Phase 3 disposition: {error}")
        return
    if disposition != expected:
        errors.append("Phase 3 disposition differs from the exact evidence-derived design amendment")


def write_disposition(root: Path, output: Path) -> dict[str, Any]:
    disposition = derive_disposition(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(disposition, indent=2, sort_keys=True) + "\n")
    return disposition

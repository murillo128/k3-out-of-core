#!/usr/bin/env python3
"""Frozen protocol constants and identity helpers for issue #99."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ISSUE = 99
PROFILE = "STANDARD"
PROJECT_BASELINE = "530b887689df6fbf1f8c9da073117c4db8c0e86f"
NESTED_BASELINE = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
MODEL_MANIFEST_SHA256 = "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2"
MODEL_SOURCE = "moonshotai/Kimi-K3@9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
MODEL_PATH = Path("/mnt/nvme0/issue77/model/kimi-k3-bf16-00001-of-00033.gguf")
CORPUS_PATH = Path("/mnt/nvme1/issue102/corpus/execution-manifest-repro.json")
FROZEN_BINARY = Path("/mnt/nvme1/issue99/build/bin/issue99-quality-probe")
EVIDENCE_ROOT = Path("/mnt/nvme1/issue99")

EXPERT_BUNDLE_BYTES = 17_547_264
TARGET_CACHE_SLOTS = 7_849
TARGET_CACHE_BYTES = 137_728_475_136
LOW_BRIDGE_CACHE_SLOTS = 5_874
LOW_BRIDGE_CACHE_BYTES = 103_072_628_736
N_CTX = 1_280
THREADS = 32
ROUTED_LAYERS = 92
SELECTED_EXPERTS = 16
CANDIDATE_COUNT = 32

QUALITY_TRACE_MAX_METADATA_BYTES = 1_024
QUALITY_TRACE_RECORD_HEADER_BYTES = 28
QUALITY_MOE_ELEMENTS = 3_584
QUALITY_HIDDEN_ELEMENTS = 7_168
QUALITY_LOGIT_ELEMENTS = 163_840
QUALITY_MAX_ROUTE_RECORD_BYTES = 4_096
QUALITY_MAX_HORIZON = 1_024
QUALITY_MAX_TRACE_BYTES = 12 + QUALITY_TRACE_MAX_METADATA_BYTES + QUALITY_MAX_HORIZON * (
    ROUTED_LAYERS * (QUALITY_TRACE_RECORD_HEADER_BYTES + QUALITY_MOE_ELEMENTS * 4) +
    ROUTED_LAYERS * (QUALITY_TRACE_RECORD_HEADER_BYTES + QUALITY_HIDDEN_ELEMENTS * 4) +
    QUALITY_TRACE_RECORD_HEADER_BYTES + QUALITY_LOGIT_ELEMENTS * 4
)
QUALITY_MAX_ROUTE_BYTES = 1024**2 + QUALITY_MAX_HORIZON * ROUTED_LAYERS * QUALITY_MAX_ROUTE_RECORD_BYTES
QUALITY_MAX_ACTIVE_OUTPUT_BYTES = QUALITY_MAX_TRACE_BYTES + QUALITY_MAX_ROUTE_BYTES
QUALITY_OUTPUT_RESIDENCY_RESERVE_BYTES = 6 * 1024**3
QUALITY_OUTPUT_RESIDENCY_RESERVE_SLOTS = (
    QUALITY_OUTPUT_RESIDENCY_RESERVE_BYTES + EXPERT_BUNDLE_BYTES - 1
) // EXPERT_BUNDLE_BYTES

BROAD_CASES = (
    "01-math-b1", "02-formal-b3", "03-science-b3", "04-factual-b2",
    "05-codegen-b5", "06-debug-b4", "07-algorithms-b2", "08-summary-b2",
    "09-extract-b8", "10-planning-b4", "11-instructions-b1", "12-compare-b1",
    "13-creative-b3", "14-qa-b2", "15-spanish-b2", "16-multi-b2",
)
BRIDGE_CASES = ("issue102-sentinel", "04-factual-b4", "10-planning-b2")
BROAD_CHECKPOINTS = (16, 32, 64, 128, 256, 512)
BRIDGE_CHECKPOINTS = (16, 32, 64, 128, 256, 512, 1024)

POLICIES = {
    "EXACT": {"candidate_count": 0, "max_swaps": 0, "max_score_regret": 0.0},
    "KNEE": {
        "candidate_count": 32,
        "max_swaps": 1,
        "max_score_regret": 0.0030885785818099976,
    },
    "S2_P50": {
        "candidate_count": 32,
        "max_swaps": 2,
        "max_score_regret": 0.007303759455680847,
    },
}

ISSUE105_ROOT = Path("results/2026-08-17/issue105")
ISSUE105_RELEASE = "issue105-curated-analysis-v3"
ISSUE105_RELEASE_SHA256 = "e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3"
ISSUE105_TARGET = "6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468"
ISSUE105_ANALYSIS_CODE = "76e0c3d578c4dba56e91d15ad643d8740037788a"
CORE_GAMMAS = (1.0, 0.8)

ISSUE102_RELEASE = "issue102-cross-prompt-v1"
ISSUE102_RELEASE_SHA256 = "e198913eb541b2a2e7465a01e09215fc5fecf6fb91574ff1841b11bf2664250c"
ISSUE102_EVIDENCE_TARGET = "0c4ed0ae92f4cc7efc79e544f04f745ff0b168cf"
ISSUE102_EXECUTION_CODE = "6ef64ba85a019d85a0fed06f49f8c45963f060ad"


def sha256_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def atomic_json(path: Path, value: Any) -> None:
    """Write JSON without exposing a partial control/evidence file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with temporary.open("w") as destination:
        destination.write(payload)
        destination.flush()
        os.fsync(destination.fileno())
    temporary.replace(path)


def file_identity(path: Path, *, hash_payload: bool = True) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    result: dict[str, Any] = {
        "canonical_path": str(resolved),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
    }
    if hash_payload:
        result["sha256"] = sha256_file(resolved)
    return result


def reference_identity(case_id: str, horizon: int, seed_token: int, target_ids: list[int]) -> str:
    value = {
        "case_id": case_id,
        "horizon_limit": horizon,
        "seed_token": seed_token,
        "target_ids": target_ids,
    }
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def expected_cell_count(low_bridge_enabled: bool) -> int:
    broad = len(BROAD_CASES) * 3
    bridge_high = len(BRIDGE_CASES) * 5
    low = len(BRIDGE_CASES) * 3 if low_bridge_enabled else 0
    return broad + bridge_high + low

#!/usr/bin/env python3
"""Frozen identities and durable I/O helpers for issue #100."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from typing import Any, Iterable


ISSUE = 100
PROFILE = "STANDARD"
PROJECT_BASELINE = "610cfb3eb1870c89016ba5ce25b875cd4e8ae14c"
NESTED_BASELINE = "a702c36b4ec50db5b5f653d5177eb4d732eeaaa9"
MODEL_MANIFEST_SHA256 = "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2"
MODEL_SOURCE = "moonshotai/Kimi-K3@9f62e4e9fffbd0a83ddd60e1c209d828994b3569"
MODEL_PATH = Path("/mnt/nvme0/issue77/model/kimi-k3-bf16-00001-of-00033.gguf")
FROZEN_BUILD = Path("/mnt/nvme1/issue77/build/cpu")
DEFAULT_BINARY = Path("/mnt/nvme1/issue100/build/bin/issue100-gpqa-probe")
DEFAULT_EVIDENCE_ROOT = Path("/mnt/nvme1/issue100/campaign")

PREREGISTRATION_SHA256 = "7d3fdd3ff2da19a41d51497115b9eb8b514978f6bb81ba060ec0b841ddb1be69"
AUTO_ADMISSION_SHA256 = "b067262db88c7e7959f3b0a48c76ed053aa773bf03d7950556d79d3e50a8b38b"
ITEM_UNIVERSE_SHA256 = "cadc978f75ff2262e8ad6112dd8f0f21ad3e489f9d68d070c625d260284e1119"
EXACT30_SELECTION_SHA256 = "dc34e5c86b3ff1fc1f58abda47d9de95810c72a58c42c21a2a321f77ebbb0f15"
CAMPAIGN_SHA256 = "399470402439b76c560a4c8bf665d7141302077733d77294ba9d53ac78bad622"
DATASET_ZIP_SHA256 = "461ae7329f15a3e35f8184d2dac24b990f34fdf12f366ca4062d8e6638cd08dc"
DIAMOND_CSV_SHA256 = "41d1213cd7a4998605a26c2798500652572007161b3a92817ba46b35befcd305"
LICENSE_SHA256 = "c4591c2eba3382b05a60e8437b7f67cc428539acbd7081d3ffe3fa55a98132db"
DATASET_PASSWORD = b"deserted-untie-orchid"

QUERY_TEMPLATE_SHA256 = "d4d85337bbd54659d12c8da7edc8b854d151b7327cf7574dc1062d753b03c8a6"
K3_WRAPPER_SHA256 = "56b417d73a77648560b11d6edad4035abc2e6119802afa9a9e556a7dc81ded8f"
RESPONSE_BOUNDARY = b"<|close|>think<|sep|><|open|>response<|sep|>"
ANSWER_PATTERN = rb"(?i)Answer[ \t]*:[ \t]*\$?([A-D])\$?"

EXPERT_BUNDLE_BYTES = 17_547_264
CAPACITY_FLOOR_SLOTS = 5_874
CAPACITY_FLOOR_BYTES = CAPACITY_FLOOR_SLOTS * EXPERT_BUNDLE_BYTES
AUTO_CACHE_REQUEST_BYTES = 0
N_CTX = 7_168
THREADS = 32
MAX_GENERATED = 4_096
ATTEMPT_TIMEOUT_S = 18_000
CUMULATIVE_ATTEMPT_BUDGET_S = 4_320_000
MAX_RESTARTS = 2
ROUTED_LAYERS = 92
SELECTED_EXPERTS = 16
CANDIDATE_COUNT = 32
MAX_SWAPS = 2
MAX_SCORE_REGRET = 0.007303759455680847

GENERATION_ROOT = "issue100-gpqa-diamond-generation-v1"
BOOTSTRAP_ROOT = "issue100-gpqa-paired-bootstrap-v1"
BOOTSTRAP_REPLICATES = 100_000
BOOTSTRAP_STREAM_SHA256 = "4ab25ca62bb69f42eecc3cd1b2214f10abab37f96f26e68ff60259d6a0ea16fc"
BOOTSTRAP_REPLICATE_ZERO = (
    27, 28, 17, 26, 14, 18, 10, 14, 26, 20, 19, 17, 19, 29, 15,
    29, 15, 6, 21, 7, 23, 22, 24, 7, 21, 14, 4, 6, 20, 23,
)

PUBLIC_PREREGISTRATION = Path("corpus/phase13/issue100-preregistration-v2.json")
PUBLIC_AUTO_ADMISSION = Path("corpus/phase13/issue100-auto-admission-v1.json")


class ProtocolError(RuntimeError):
    """A frozen identity, durability, or evidence invariant was violated."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    with path.open("rb") as source:
        return json.load(source)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value: Any) -> None:
    """Replace a JSON control file durably, including the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    payload = json.dumps(
        value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as destination:
            destination.write(payload)
            destination.flush()
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def append_canonical_jsonl(path: Path, value: Any) -> None:
    """Append one canonical record and make it durable before returning."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        payload = canonical_json_bytes(value)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise ProtocolError(f"short append to {path}")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if not existed:
        fsync_directory(path.parent)


def file_identity(path: Path, *, hash_payload: bool = True) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    stat = resolved.stat()
    identity: dict[str, Any] = {
        "canonical_path": str(resolved),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size_bytes": stat.st_size,
    }
    if hash_payload:
        identity["sha256"] = sha256_file(resolved)
    return identity


def generation_seed(record_id: str) -> int:
    payload = f"{GENERATION_ROOT}\0{record_id}\0repeat-0".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")


def bootstrap_indices() -> Iterable[tuple[int, ...]]:
    """Yield the exact preregistered paired-bootstrap index rows."""
    seed = BOOTSTRAP_ROOT.encode("utf-8")
    for replicate in range(BOOTSTRAP_REPLICATES):
        prefix = seed + b"\0" + f"{replicate:06d}".encode("ascii") + b"\0"
        yield tuple(
            int.from_bytes(hashlib.sha256(prefix + f"{draw:02d}".encode("ascii")).digest()[:8], "big") % 30
            for draw in range(30)
        )


def record_checksum(record: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in record.items() if key != "artifact_checksum"}
    return sha256_bytes(canonical_json_bytes(unsigned))


def bind_checksum(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result["artifact_checksum"] = record_checksum(result)
    return result


def validate_checksum(record: dict[str, Any]) -> None:
    expected = record.get("artifact_checksum")
    if not isinstance(expected, str) or expected != record_checksum(record):
        raise ProtocolError("record checksum mismatch")


def finite_number(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ProtocolError(f"{name} is not finite")
    return float(value)


def git_output(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout.strip()


def repository_identity(root: Path) -> dict[str, Any]:
    return {
        "project_commit": git_output(root, "rev-parse", "HEAD"),
        "nested_commit": git_output(root / "llama.cpp", "rev-parse", "HEAD"),
        "worktree_porcelain": git_output(root, "status", "--porcelain", "--untracked-files=all"),
    }


def require_frozen_runtime_identity(
    root: Path,
    *,
    authorized_project_commit: str,
    allow_dirty: bool = False,
) -> dict[str, Any]:
    identity = repository_identity(root)
    if identity["project_commit"] != authorized_project_commit:
        raise ProtocolError("project HEAD differs from independently authorized commit")
    if identity["nested_commit"] != NESTED_BASELINE:
        raise ProtocolError("nested llama.cpp commit drift")
    if identity["worktree_porcelain"] and not allow_dirty:
        raise ProtocolError("scored campaign requires a clean worktree")
    return identity

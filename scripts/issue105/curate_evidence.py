#!/usr/bin/env python3
"""Verify and curate the frozen issue-102/98 evidence for issue 105.

This command performs only offline ingestion and canonicalization.  It never
invokes a K3 binary, reads model weights, or executes a benchmark.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import pathlib
import re
import statistics
import subprocess
import tarfile
import urllib.request
from typing import Any, BinaryIO, Iterable, Iterator, Sequence


EVIDENCE_CLASSES = {
    "MEASURED_PHYSICAL",
    "MEASURED_OBSERVER",
    "CURATED_FROM_MEASURED",
    "EXACT_REPLAY",
    "FIXED_ROUTE_COUNTERFACTUAL",
    "EXACT_REPLAY_COUNTERFACTUAL",
    "TPS_PROJECTION",
    "SEMANTIC_SANITY",
    "POST_HOC_EXPLORATORY",
}
SOURCE_STATUSES = {
    "accepted",
    "missing",
    "failed",
    "superseded",
    "contextual_non_authoritative",
}
PHASE_RELATIVE = pathlib.Path(
    "results/2026-08-13/sergio-test-1/phase13-6pg-cross-prompt"
)
SELECTED_ARCHIVE_PATHS = {
    "host/observer-replay-v1/committee-pin-capacity-counterfactual.json",
    "host/observer-replay-v1/exact-capacity-mrc.json",
    "host/observer-replay-v1/family-length-capacity-extension.json",
    "host/observer-replay-v1/observer-replay-index.json",
    "host/observer-replay-v1/s2-fixed-route-capacity-counterfactual.json",
    "host/posthoc-analysis-v1/locality-throughput-calibration.json",
    "host/posthoc-analysis-v1/stage-a-family-length-analysis.json",
    "host/stage-b-analysis-v1/family-overlap-matrix.json",
    "host/stage-b-analysis-v1/family-route-fingerprints.json",
    "host/stage-b-analysis-v1/stage-b-route-analysis-index.json",
    "host/stage-b-analysis-v1/stage-b2-family-length-route-endpoints.json",
    "host/stage-b-analysis-v1/standing-committee-core-periphery.json",
    "host/stage-c-control-v2/progress.json",
}
COMMON_FIELDS = (
    "source_sha256",
    "derivation_id",
    "analysis_code_version",
    "source_evidence_class",
    "derived_evidence_class",
    "protocol",
    "authority_scope",
)
CASE_PATTERN = re.compile(r"(?:^|[-/])(\d{2}-[a-z0-9]+-b\d)(?:[-/]|$)")


class CurationError(ValueError):
    """Raised when evidence cannot be admitted without weakening the contract."""


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=pathlib.Path, required=True)
    parser.add_argument("--issue102-archive", type=pathlib.Path, required=True)
    parser.add_argument("--issue98-root", type=pathlib.Path, required=True)
    parser.add_argument("--output-root", type=pathlib.Path, required=True)
    parser.add_argument("--work-root", type=pathlib.Path, required=True)
    parser.add_argument("--analysis-code-version", required=True)
    parser.add_argument(
        "--source-lock",
        type=pathlib.Path,
        default=pathlib.Path(__file__).with_name("source-lock.json"),
    )
    parser.add_argument(
        "--github-repository", default="murillo128/k3-out-of-core"
    )
    return parser.parse_args()


def sha256_stream(stream: BinaryIO) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def sha256(path: pathlib.Path) -> str:
    with path.open("rb") as stream:
        return sha256_stream(stream)[1]


def identity(path: pathlib.Path) -> dict[str, Any]:
    path = path.resolve(strict=True)
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def write_json(path: pathlib.Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, path)
    return identity(path)


def require_identity(path: pathlib.Path, expected_sha256: str, expected_bytes: int | None = None) -> None:
    observed = identity(path)
    if observed["sha256"] != expected_sha256:
        raise CurationError(f"SHA-256 mismatch: {path}")
    if expected_bytes is not None and observed["bytes"] != expected_bytes:
        raise CurationError(f"byte-size mismatch: {path}")


def git_output(repository: pathlib.Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repository), *args])


def verify_git_target(repository: pathlib.Path, lock: dict[str, Any]) -> dict[str, Any]:
    target = lock["project_evidence_target"]
    resolved = git_output(repository, "rev-parse", f"{target}^{{commit}}").decode().strip()
    if resolved != target:
        raise CurationError("issue-102 project target does not resolve exactly")
    tree = git_output(repository, "ls-tree", target, "llama.cpp").decode().strip().split()
    if len(tree) < 3 or tree[2] != lock["nested_llama_cpp_sha"]:
        raise CurationError("issue-102 nested gitlink mismatch")
    artifacts = []
    for expected in lock["committed_artifacts"]:
        blob = git_output(repository, "show", f"{target}:{expected['path']}")
        digest = hashlib.sha256(blob).hexdigest()
        if digest != expected["sha256"]:
            raise CurationError(f"committed artifact mismatch: {expected['path']}")
        if "schema_version" in expected:
            parsed = json.loads(blob)
            if parsed.get("schema_version") != expected["schema_version"]:
                raise CurationError(f"committed schema mismatch: {expected['path']}")
        artifacts.append({"path": expected["path"], "bytes": len(blob), "sha256": digest})
    return {"project_target": target, "nested_target": tree[2], "artifacts": artifacts}


def github_comment(repository_name: str, comment_id: int) -> dict[str, Any]:
    command = ["gh", "api", f"repos/{repository_name}/issues/comments/{comment_id}"]
    try:
        return json.loads(subprocess.check_output(command))
    except (FileNotFoundError, subprocess.CalledProcessError):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository_name}/issues/comments/{comment_id}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "issue105-curator"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)


def verify_review_attestation(
    repository_name: str, review_lock: dict[str, Any]
) -> dict[str, Any]:
    comment = github_comment(repository_name, int(review_lock["comment_id"]))
    body = comment.get("body", "")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if digest != review_lock["body_sha256"]:
        raise CurationError(f"review comment body mismatch: {review_lock['comment_id']}")
    missing = [fragment for fragment in review_lock["required_fragments"] if fragment not in body]
    if missing:
        raise CurationError(f"review attestation missing fragments: {missing}")
    return {
        "comment_id": review_lock["comment_id"],
        "body_sha256": digest,
        "html_url": comment.get("html_url"),
        "status": "PASS",
    }


def close_decompressor(process: subprocess.Popen[bytes], label: str) -> None:
    stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise CurationError(f"{label} failed ({return_code}): {stderr}")


def verify_issue102_archive(
    archive: pathlib.Path,
    archive_index: dict[str, Any],
    extract_root: pathlib.Path,
) -> dict[str, Any]:
    expected_rows = archive_index.get("members", [])
    expected = {row["archive_path"]: row for row in expected_rows}
    if len(expected) != len(expected_rows):
        raise CurationError("duplicate archive paths in issue-102 index")
    extract_root.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        ["zstd", "-q", "-d", "-c", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise CurationError("zstd stdout unavailable")
    observed: set[str] = set()
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    raise CurationError(f"unexpected non-file issue-102 member: {member.name}")
                if member.name not in expected or member.name in observed:
                    raise CurationError(f"unexpected/duplicate issue-102 member: {member.name}")
                source = tar.extractfile(member)
                if source is None:
                    raise CurationError(f"unreadable issue-102 member: {member.name}")
                digest = hashlib.sha256()
                size = 0
                destination = None
                temporary = None
                if member.name in SELECTED_ARCHIVE_PATHS:
                    destination = extract_root / member.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary = destination.with_suffix(destination.suffix + ".tmp")
                    sink = temporary.open("wb")
                else:
                    sink = None
                try:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
                        if sink is not None:
                            sink.write(chunk)
                finally:
                    if sink is not None:
                        sink.close()
                row = expected[member.name]
                if size != row["bytes"] or digest.hexdigest() != row["sha256"]:
                    if temporary is not None and temporary.exists():
                        temporary.unlink()
                    raise CurationError(f"issue-102 member identity mismatch: {member.name}")
                if destination is not None and temporary is not None:
                    os.replace(temporary, destination)
                observed.add(member.name)
        close_decompressor(process, "issue-102 decompression")
    except BaseException:
        process.kill()
        process.wait()
        raise
    missing = sorted(set(expected) - observed)
    if missing:
        raise CurationError(f"issue-102 members missing: {missing[:10]}")
    archive_meta = archive_index.get("archive", {})
    if len(observed) != archive_meta.get("member_count"):
        raise CurationError("issue-102 member count mismatch")
    return {
        "member_count": len(observed),
        "member_bytes": sum(row["bytes"] for row in expected_rows),
        "member_validation": "PASS",
        "selected_members_materialized": len(SELECTED_ARCHIVE_PATHS),
    }


def normalize_checksum_path(path: str) -> str:
    path = path.strip()
    if path.startswith("./"):
        path = path[2:]
    return path


def verify_issue98_archive(
    archive: pathlib.Path,
    expected_file_count: int,
    external_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    process = subprocess.Popen(
        ["zstd", "-q", "-d", "-c", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None:
        raise CurationError("zstd stdout unavailable")
    files: dict[str, dict[str, Any]] = {}
    checksum_text = None
    prefix = None
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                if "/" not in member.name:
                    raise CurationError(f"unprefixed issue-98 member: {member.name}")
                current_prefix, relative = member.name.split("/", 1)
                if prefix is None:
                    prefix = current_prefix
                if current_prefix != prefix or relative in files:
                    raise CurationError(f"invalid issue-98 archive member: {member.name}")
                source = tar.extractfile(member)
                if source is None:
                    raise CurationError(f"unreadable issue-98 member: {member.name}")
                if relative == "SHA256SUMS.txt":
                    payload = source.read()
                    checksum_text = payload.decode("utf-8")
                    files[relative] = {
                        "bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                else:
                    size, digest = sha256_stream(source)
                    files[relative] = {"bytes": size, "sha256": digest}
        close_decompressor(process, "issue-98 decompression")
    except BaseException:
        process.kill()
        process.wait()
        raise
    if checksum_text is None:
        raise CurationError(f"issue-98 archive lacks SHA256SUMS.txt: {archive}")
    checksums = {}
    for line in checksum_text.splitlines():
        if not line.strip():
            continue
        digest, listed_path = line.split(None, 1)
        checksums[normalize_checksum_path(listed_path)] = digest
    observed_payload = {path: row for path, row in files.items() if path != "SHA256SUMS.txt"}
    if len(checksums) != expected_file_count or len(observed_payload) != expected_file_count:
        raise CurationError(f"issue-98 archive file count mismatch: {archive.name}")
    for path, digest in checksums.items():
        if path not in observed_payload or observed_payload[path]["sha256"] != digest:
            raise CurationError(f"issue-98 internal checksum mismatch: {path}")
    if external_index is not None:
        index_rows = {row["path"]: row for row in external_index.get("members", [])}
        if set(index_rows) != set(files):
            raise CurationError("issue-98 v3 external index member set mismatch")
        for path, row in files.items():
            expected = index_rows[path]
            if row["bytes"] != expected["bytes"] or row["sha256"] != expected["sha256"]:
                raise CurationError(f"issue-98 v3 external index mismatch: {path}")
    return {
        "archive": archive.name,
        "prefix": prefix,
        "file_count": len(observed_payload),
        "internal_checksums": "PASS",
        "external_index": "PASS" if external_index is not None else "not_applicable",
    }


def source_evidence_class(path: str) -> str:
    if "/observer-replay-v1/exact-capacity-mrc.json" in path:
        return "EXACT_REPLAY"
    if "/observer-replay-v1/family-length-capacity-extension.json" in path:
        return "EXACT_REPLAY_COUNTERFACTUAL"
    if "/observer-replay-v1/" in path:
        return "FIXED_ROUTE_COUNTERFACTUAL"
    if "/stage-b-observer/" in path:
        return "MEASURED_OBSERVER"
    if "/stage-b-analysis-v1/" in path:
        return "CURATED_FROM_MEASURED"
    if "/posthoc-analysis-v1/" in path:
        return "POST_HOC_EXPLORATORY"
    if "semantic-sanity" in path:
        return "SEMANTIC_SANITY"
    if any(token in path for token in ("/stage-a/", "/stage-a-sentinels/", "/stage-c-v2/")):
        return "MEASURED_PHYSICAL"
    return "CURATED_FROM_MEASURED"


def source_status(
    path: str, role: str, accepted_observer_prefixes: set[str]
) -> tuple[str, str, str, str]:
    if "/stage-a-invalid/" in path or "/stage-c-v1/" in path:
        return "failed", "preserved invalid/pre-context execution; no scientific authority", "", ""
    if "/stage-c-control-v1/" in path:
        return "superseded", "superseded by the final recovery control v2", "", "stage-c-control-v2"
    if path.startswith("host/stage-b-observer/") and not any(
        path.startswith(prefix) for prefix in accepted_observer_prefixes
    ):
        return "failed", "not one of the 44 final accepted observer capture directories", "", ""
    if role == "WORKFLOW_CONTEXT":
        return "contextual_non_authoritative", "workflow context retained outside scientific authority", "", ""
    return "accepted", "checksum-verified member within frozen authority", "", ""


def case_identity(path: str) -> str:
    match = CASE_PATTERN.search(path)
    return match.group(1) if match else ""


def source_protocol(path: str) -> str:
    if "/stage-b-observer/" in path or "/stage-b-analysis-v1/" in path:
        return "issue102_observer_semantic_order"
    if "/observer-replay-v1/" in path:
        return "issue102_offline_fixed_route_replay"
    return "issue102_full_prompt"


def source_policy(path: str) -> str:
    if "exact-capacity-mrc" in path:
        return "EXACT_LRU"
    if "s2-fixed-route" in path:
        return "S2_P50_FIXED_ROUTE"
    if "committee-pin" in path:
        return "COMMITTEE_PIN_FIXED_ROUTE"
    if "/stage-a/" in path or "/stage-a-sentinels/" in path:
        return "S2_P50"
    return ""


def schema_versions(extract_root: pathlib.Path) -> dict[str, str]:
    result = {}
    for relative in SELECTED_ARCHIVE_PATHS:
        path = extract_root / relative
        if path.suffix == ".json":
            value = load_json(path)
            result[relative] = str(value.get("schema_version", ""))
    return result


def accepted_observer_prefixes(
    index_rows: Sequence[dict[str, Any]], exact_mrc: dict[str, Any]
) -> set[str]:
    by_sha = {row["sha256"]: row["archive_path"] for row in index_rows}
    prefixes = set()
    for prompt in exact_mrc["prompt_rows"]:
        digest = prompt["observer_result"]["sha256"]
        path = by_sha.get(digest)
        if path is None or not path.endswith("/result.json"):
            raise CurationError(f"accepted observer result absent from archive: {digest}")
        prefixes.add(path.rsplit("/", 1)[0] + "/")
    if len(prefixes) != 44:
        raise CurationError("accepted observer directory count is not 44")
    return prefixes


def build_source_catalog(
    index: dict[str, Any],
    schemas: dict[str, str],
    observer_prefixes: set[str],
    issue98_lock: dict[str, Any],
    issue98_root: pathlib.Path,
) -> list[dict[str, Any]]:
    rows = []
    for member in index["members"]:
        path = member["archive_path"]
        status, reason, supersedes, superseded_by = source_status(
            path, member["role"], observer_prefixes
        )
        evidence_class = source_evidence_class(path)
        if evidence_class not in EVIDENCE_CLASSES or status not in SOURCE_STATUSES:
            raise CurationError(f"unclassified source: {path}")
        rows.append(
            {
                "source_issue_release": "#102/issue102-cross-prompt-v1",
                "archive_member_original_path": path,
                "sha256": member["sha256"],
                "bytes": int(member["bytes"]),
                "schema_version": schemas.get(path, ""),
                "case_run_identity": case_identity(path),
                "protocol": source_protocol(path),
                "policy": source_policy(path),
                "source_evidence_class": evidence_class,
                "authority_scope": "issue102_full_prompt" if status == "accepted" else "non_authoritative_context",
                "status": status,
                "reason": reason,
                "supersedes": supersedes,
                "superseded_by": superseded_by,
            }
        )
    for archive_lock in issue98_lock["archives"]:
        path = issue98_root / archive_lock["file"]
        rows.append(
            {
                "source_issue_release": f"#98/{archive_lock['release_tag']}",
                "archive_member_original_path": archive_lock["file"],
                "sha256": archive_lock["sha256"],
                "bytes": path.stat().st_size,
                "schema_version": "",
                "case_run_identity": "",
                "protocol": "legacy_first_full",
                "policy": "mixed_legacy_context",
                "source_evidence_class": "MEASURED_PHYSICAL",
                "authority_scope": "issue98_legacy_first_full_only",
                "status": "accepted",
                "reason": "checksum-verified protocol-distinct contextual archive",
                "supersedes": "",
                "superseded_by": "",
            }
        )
    for key in ("archive_index", "final_synthesis"):
        locked = issue98_lock[key]
        path = issue98_root / locked["file"]
        rows.append(
            {
                "source_issue_release": "#98/issue98-profile-shape-extension-v3",
                "archive_member_original_path": locked["file"],
                "sha256": locked["sha256"],
                "bytes": path.stat().st_size,
                "schema_version": locked.get("schema_version", ""),
                "case_run_identity": "",
                "protocol": "legacy_first_full",
                "policy": "mixed_legacy_context",
                "source_evidence_class": "CURATED_FROM_MEASURED",
                "authority_scope": "issue98_legacy_first_full_only",
                "status": "accepted",
                "reason": "verified protocol-distinct contextual synthesis/index",
                "supersedes": "",
                "superseded_by": "",
            }
        )
    rows = sorted(rows, key=lambda row: (
        row["source_issue_release"], row["archive_member_original_path"]
    ))
    validate_source_catalog(rows)
    return rows


def validate_source_catalog(rows: Sequence[dict[str, Any]]) -> None:
    identities = [
        (row["source_issue_release"], row["archive_member_original_path"])
        for row in rows
    ]
    if len(identities) != len(set(identities)):
        raise CurationError("duplicate source authority path")
    for row in rows:
        if row["source_evidence_class"] not in EVIDENCE_CLASSES:
            raise CurationError("source has unknown evidence class")
        if row["status"] not in SOURCE_STATUSES:
            raise CurationError("source has unknown catalog status")
        if row["status"] == "accepted" and row["superseded_by"]:
            raise CurationError("accepted source cannot simultaneously be superseded")
        if row["status"] == "superseded" and not row["superseded_by"]:
            raise CurationError("superseded source lacks successor link")


def common_provenance(
    source_sha256: Iterable[str],
    derivation_id: str,
    analysis_code_version: str,
    source_evidence: str,
    protocol: str,
    authority_scope: str = "issue102_full_prompt",
) -> dict[str, Any]:
    derived_evidence = (
        source_evidence
        if source_evidence in {
            "EXACT_REPLAY",
            "FIXED_ROUTE_COUNTERFACTUAL",
            "EXACT_REPLAY_COUNTERFACTUAL",
            "SEMANTIC_SANITY",
        }
        else "CURATED_FROM_MEASURED"
    )
    return {
        "source_sha256": json.dumps(sorted(set(source_sha256)), separators=(",", ":")),
        "derivation_id": derivation_id,
        "analysis_code_version": analysis_code_version,
        "source_evidence_class": source_evidence,
        "derived_evidence_class": derived_evidence,
        "protocol": protocol,
        "authority_scope": authority_scope,
    }


def case_rows(
    corpus: dict[str, Any],
    corpus_sha: str,
    exact_mrc: dict[str, Any],
    stage_c: dict[str, Any],
    code_version: str,
) -> list[dict[str, Any]]:
    roles: dict[str, set[str]] = {case["id"]: {"STAGE_A_PRIMARY"} for case in corpus["cases"]}
    for row in exact_mrc["prompt_rows"]:
        roles[row["case_id"]].add(row["selection_role"])
    for row in stage_c["prompt_rows"]:
        roles[row["case_id"]].add(row["selection_role"])
    result = []
    for case in corpus["cases"]:
        row = {
            "case_id": case["id"],
            "semantic_family": case["semantic_family"],
            "length_level": int(case["length_level"]),
            "templated_prompt_tokens": int(case["observed_templated_prompt_tokens"]),
            "corpus_sha256": corpus_sha,
            "prompt_sha256": hashlib.sha256(case["templated_prompt"].encode("utf-8")).hexdigest(),
            "execution_round": int(case["round"]),
            "execution_position": int(case["position"]),
            "selection_roles": json.dumps(sorted(roles[case["id"]]), separators=(",", ":")),
        }
        row.update(common_provenance(
            [corpus_sha], "issue105-cases-v1", code_version,
            "CURATED_FROM_MEASURED", "issue102_full_prompt"
        ))
        result.append(row)
    return sorted(result, key=lambda row: row["case_id"])


def nullable(value: Any) -> Any:
    return value if value is not None else None


def physical_rows(
    checkpoint: dict[str, Any],
    checkpoint_sha: str,
    stage_c_progress: dict[str, Any],
    progress_sha: str,
    code_version: str,
) -> list[dict[str, Any]]:
    rows = []
    for source in checkpoint["primary_rows"]:
        row = {
            "stage": "STAGE_A",
            "case_id": source["case_id"],
            "case_role": "primary",
            "semantic_family": source["semantic_family"],
            "length_level": int(source["length_level"]),
            "templated_prompt_tokens": int(source["templated_prompt_tokens"]),
            "policy": "S2_P50",
            "capacity_slots": 7849,
            "capacity_bytes": 137728475136,
            "decode_tok_s": float(source["decode_tok_s"]),
            "p50_forward_s": float(source["p50_forward_s"]),
            "p95_forward_s": float(source["p95_forward_s"]),
            "p99_forward_s": float(source["p99_forward_s"]),
            "hit_ratio": float(source["hit_ratio"]),
            "loads_per_token": float(source["loads_per_token"]),
            "bytes_per_token": float(source["bytes_per_token"]),
            "changed_fraction": float(source["changed_fraction"]),
            "swaps_per_token": float(source["swaps_per_token"]),
            "cumulative_score_regret": float(source["cumulative_score_regret"]),
            "mean_score_regret_per_realized_swap": float(
                source["mean_score_regret_per_realized_swap"]
            ),
            "result_sha256": source["result_sha256"],
            "envelope_sha256": source["envelope_sha256"],
            "generated_token_hash": source["generated_token_hash"],
            "safety_status": "pass",
        }
        row.update(common_provenance(
            [checkpoint_sha, source["result_sha256"], source["envelope_sha256"]],
            "issue105-physical-runs-v1", code_version,
            "MEASURED_PHYSICAL", "issue102_full_prompt"
        ))
        rows.append(row)
    for source in checkpoint["sentinels"]["runs"]:
        row = {
            "stage": "STAGE_A_SENTINEL",
            "case_id": f"sentinel-round-{int(source['round']):02d}",
            "case_role": "sentinel",
            "semantic_family": "",
            "length_level": None,
            "templated_prompt_tokens": None,
            "policy": "S2_P50",
            "capacity_slots": 7849,
            "capacity_bytes": 137728475136,
            "decode_tok_s": float(source["decode_tok_s"]),
            "p50_forward_s": None,
            "p95_forward_s": None,
            "p99_forward_s": None,
            "hit_ratio": None,
            "loads_per_token": None,
            "bytes_per_token": None,
            "changed_fraction": None,
            "swaps_per_token": None,
            "cumulative_score_regret": None,
            "mean_score_regret_per_realized_swap": None,
            "result_sha256": source["result_sha256"],
            "envelope_sha256": source["envelope_sha256"],
            "generated_token_hash": source["deterministic_signature_sha256"],
            "safety_status": source["status"],
        }
        row.update(common_provenance(
            [checkpoint_sha, source["result_sha256"], source["envelope_sha256"]],
            "issue105-physical-runs-v1", code_version,
            "MEASURED_PHYSICAL", "issue102_full_prompt"
        ))
        rows.append(row)
    for source in stage_c_progress["captures"]:
        result_artifact = source["artifacts"]["result.json"]
        envelope_artifact = source["artifacts"]["envelope.json"]
        routing = source["routing"]
        row = {
            "stage": "STAGE_C",
            "case_id": source["case_id"],
            "case_role": "primary",
            "semantic_family": source["semantic_family"],
            "length_level": int(source["length_level"]),
            "templated_prompt_tokens": int(source["prompt_tokens"]),
            "policy": source["point"],
            "capacity_slots": 7849,
            "capacity_bytes": 137728475136,
            "decode_tok_s": float(source["decode_tok_s"]),
            "p50_forward_s": None,
            "p95_forward_s": None,
            "p99_forward_s": None,
            "hit_ratio": float(source["hit_ratio"]),
            "loads_per_token": float(source["loads_per_token"]),
            "bytes_per_token": float(source["bytes_per_token"]),
            "changed_fraction": (
                float(routing["changed_decisions"]) / 5888.0
                if routing["changed_decisions"] is not None else None
            ),
            "swaps_per_token": (
                float(routing["realized_swaps"]) / 64.0
                if routing["realized_swaps"] is not None else None
            ),
            "cumulative_score_regret": nullable(routing["cumulative_score_regret"]),
            "mean_score_regret_per_realized_swap": (
                float(routing["cumulative_score_regret"]) / float(routing["realized_swaps"])
                if routing["realized_swaps"] else None
            ),
            "result_sha256": result_artifact["sha256"],
            "envelope_sha256": envelope_artifact["sha256"],
            "generated_token_hash": source["generated_token_hash"],
            "safety_status": "pass",
        }
        row.update(common_provenance(
            [progress_sha, result_artifact["sha256"], envelope_artifact["sha256"]],
            "issue105-physical-runs-v1", code_version,
            "MEASURED_PHYSICAL", "issue102_full_prompt"
        ))
        rows.append(row)
    rows.sort(key=lambda row: (row["stage"], row["case_id"], row["policy"]))
    validate_physical_rows(rows)
    return rows


def validate_physical_rows(rows: Sequence[dict[str, Any]]) -> None:
    if any(row["protocol"] != "issue102_full_prompt" for row in rows):
        raise CurationError("#98/#102 protocol pooling in physical_runs")
    if any(row["source_evidence_class"] != "MEASURED_PHYSICAL" for row in rows):
        raise CurationError("observer/replay timing admitted as physical TPS")
    stage_a = [row for row in rows if row["stage"] == "STAGE_A"]
    sentinels = [row for row in rows if row["stage"] == "STAGE_A_SENTINEL"]
    stage_c = [row for row in rows if row["stage"] == "STAGE_C"]
    if (len(stage_a), len(sentinels), len(stage_c)) != (128, 8, 48):
        raise CurationError("physical table completeness mismatch")
    keys = [(row["stage"], row["case_id"], row["policy"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise CurationError("duplicate physical stage/case/policy row")
    if any(row["policy"] == "S2_P50" for row in stage_c):
        raise CurationError("Stage-C S2 row would double-count Stage A")


def legacy_rows(
    synthesis: dict[str, Any], synthesis_sha: str, code_version: str
) -> list[dict[str, Any]]:
    rows = []
    capacity = synthesis["capacity_resolution"]["exploratory_max_safe"]
    for profile in synthesis["screening"]["profiles"]:
        measured = profile["measured"]
        row = {
            "study": "issue98_v3_screening",
            "policy": profile["name"],
            "replicates": len(profile["runs"]),
            "capacity_slots": int(capacity["slots"]),
            "capacity_bytes": int(capacity["bytes"]),
            "decode_tok_s_median": float(measured["decode_tok_s"]["median"]),
            "hit_ratio_median": float(measured["hit_ratio"]["median"]),
            "loads_per_token_median": float(measured["loads_per_token"]["median"]),
            "bytes_per_token_median": float(measured["bytes_per_token"]["median"]),
            "context_only": True,
        }
        row.update(common_provenance(
            [synthesis_sha], "issue105-legacy-context-v1", code_version,
            "MEASURED_PHYSICAL", "legacy_first_full", "issue98_legacy_first_full_only"
        ))
        rows.append(row)
    return sorted(rows, key=lambda row: row["policy"])


def mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def phase_core_sets(committee: dict[str, Any]) -> dict[str, dict[float, list[set[int]]]]:
    result: dict[str, dict[float, list[set[int]]]] = {}
    for phase, phase_value in committee["phases"].items():
        by_gamma = {}
        for gamma in phase_value["gamma_sensitivity"]:
            by_gamma[float(gamma["gamma"])] = [
                set(int(expert) for expert in layer["core_experts"])
                for layer in gamma["layers"]
            ]
        result[phase] = by_gamma
    return result


def route_feature_rows(
    fingerprints: dict[str, Any],
    fingerprints_sha: str,
    exact_mrc: dict[str, Any],
    exact_sha: str,
    committee: dict[str, Any],
    committee_sha: str,
    code_version: str,
) -> list[dict[str, Any]]:
    replay_by_case = {row["case_id"]: row for row in exact_mrc["prompt_rows"]}
    cores = phase_core_sets(committee)
    rows = []
    for profile in fingerprints["profiles"]:
        replay = replay_by_case[profile["case_id"]]
        for phase in ("PREFILL", "DECODE"):
            value = profile["phases"][phase]
            layers = value["layers"]
            matrix = value["selected_frequency_matrix_92x896"]
            selected = int(value["selected_occurrences"])
            top16 = sum(sum(item["count"] for item in layer["top_16_by_frequency"]) for layer in layers)
            core_masses = {}
            for gamma in (0.8, 1.0):
                core_count = 0
                for layer_index, frequency in enumerate(matrix):
                    core_count += sum(frequency[expert] for expert in cores[phase][gamma])
                core_masses[gamma] = core_count / selected if selected else None
            stack = replay[f"{phase.lower()}_stack_distance"]
            finite = stack["finite_stack_distance_slots"]
            row = {
                "case_id": profile["case_id"],
                "semantic_family": profile["semantic_family"],
                "length_level": int(profile["length_level"]),
                "selection_role": profile["selection_role"],
                "prompt_tokens": int(profile["prompt_tokens"]),
                "phase": phase,
                "selected_occurrences": selected,
                "distinct_expert_keys": int(value["whole_run_distinct_expert_keys"]),
                "mean_layer_entropy_bits": float(mean([layer["entropy_bits"] for layer in layers])),
                "mean_layer_effective_experts": float(mean([layer["effective_expert_count"] for layer in layers])),
                "median_layer_distinct_experts": float(median([layer["distinct_experts"] for layer in layers])),
                "top16_selected_mass_fraction": top16 / selected if selected else None,
                "mean_layer_mass50_experts": float(mean([
                    layer["cumulative_mass_set_sizes"]["0.5"] for layer in layers
                ])),
                "mean_layer_mass90_experts": float(mean([
                    layer["cumulative_mass_set_sizes"]["0.9"] for layer in layers
                ])),
                "cold_first_occurrences": int(stack["cold_first_occurrences"]),
                "finite_stack_distance_median": float(finite["median"]),
                "finite_stack_distance_mean": float(finite["mean"]),
                "finite_stack_distance_p90": float(finite["p90"]),
                "core_mass_gamma_0_8": core_masses[0.8],
                "core_mass_gamma_1_0": core_masses[1.0],
            }
            row.update(common_provenance(
                [fingerprints_sha, exact_sha, committee_sha, profile["result"]["sha256"]],
                "issue105-route-features-v1", code_version,
                "MEASURED_OBSERVER", "issue102_observer_semantic_order"
            ))
            rows.append(row)
    return sorted(rows, key=lambda row: (row["case_id"], row["phase"]))


def capacity_rows(
    exact: dict[str, Any], exact_sha: str,
    s2: dict[str, Any], s2_sha: str,
    committee: dict[str, Any], committee_sha: str,
    code_version: str,
) -> list[dict[str, Any]]:
    rows = []
    for prompt in exact["prompt_rows"]:
        for point in prompt["capacity_curve"]:
            for phase in ("prefill", "decode"):
                metric = point[phase]
                row = {
                    "case_id": prompt["case_id"],
                    "semantic_family": prompt["semantic_family"],
                    "length_level": int(prompt["length_level"]),
                    "selection_role": prompt["selection_role"],
                    "phase": phase.upper(),
                    "capacity_label": point["label"],
                    "capacity_slots": int(point["slots"]),
                    "capacity_bytes": int(point["actual_bytes"]),
                    "policy_result_class": "EXACT_LRU",
                    "gamma": None,
                    "status": "pass",
                    "hit_ratio": float(metric["hit_ratio"]),
                    "loads_per_token": float(metric["loads_per_token"]),
                    "bytes_per_token": float(metric["bytes_per_token"]),
                    "physical_anchor": bool(point["physical_anchor"]),
                    "validation_stratum": (
                        "observer_semantic_order_44_of_44"
                        if point["physical_anchor"] else "exact_replay_only"
                    ),
                }
                row.update(common_provenance(
                    [exact_sha, prompt["observer_result"]["sha256"]],
                    "issue105-capacity-curves-v1", code_version,
                    "EXACT_REPLAY", "issue102_offline_fixed_route_replay"
                ))
                rows.append(row)
    for prompt in s2["prompt_rows"]:
        for point in prompt["capacity_curve"]:
            metric = point["s2_fixed_route_decode"]
            row = {
                "case_id": prompt["case_id"],
                "semantic_family": prompt["semantic_family"],
                "length_level": int(prompt["length_level"]),
                "selection_role": prompt["selection_role"],
                "phase": "DECODE",
                "capacity_label": point["label"],
                "capacity_slots": int(point["slots"]),
                "capacity_bytes": int(point["actual_bytes"]),
                "policy_result_class": "S2_P50_FIXED_ROUTE",
                "gamma": None,
                "status": "pass",
                "hit_ratio": float(metric["hit_ratio"]),
                "loads_per_token": float(metric["loads_per_token"]),
                "bytes_per_token": float(metric["bytes_per_token"]),
                "physical_anchor": bool(point["physical_anchor"]),
                "validation_stratum": "fixed_route_counterfactual",
            }
            row.update(common_provenance(
                [s2_sha], "issue105-capacity-curves-v1", code_version,
                "FIXED_ROUTE_COUNTERFACTUAL", "issue102_offline_fixed_route_replay"
            ))
            rows.append(row)
    for prompt in committee["prompt_rows"]:
        for gamma_group in prompt["gamma_sensitivity"]:
            gamma = float(gamma_group["gamma"])
            for point in gamma_group["capacity_curve"]:
                metric = point.get("decode")
                row = {
                    "case_id": prompt["case_id"],
                    "semantic_family": prompt["semantic_family"],
                    "length_level": None,
                    "selection_role": "STAGE_B_REPRESENTATIVE",
                    "phase": "DECODE",
                    "capacity_label": point["label"],
                    "capacity_slots": int(point["slots"]),
                    "capacity_bytes": int(point["actual_bytes"]),
                    "policy_result_class": "COMMITTEE_PIN_FIXED_ROUTE",
                    "gamma": gamma,
                    "status": point["status"],
                    "hit_ratio": float(metric["hit_ratio"]) if metric else None,
                    "loads_per_token": float(metric["loads_per_token"]) if metric else None,
                    "bytes_per_token": float(metric["backing_bytes_per_token"]) if metric else None,
                    "physical_anchor": bool(point["physical_anchor"]),
                    "validation_stratum": "committee_pin_counterfactual",
                }
                row.update(common_provenance(
                    [committee_sha], "issue105-capacity-curves-v1", code_version,
                    "FIXED_ROUTE_COUNTERFACTUAL", "issue102_offline_fixed_route_replay"
                ))
                rows.append(row)
    return sorted(rows, key=lambda row: (
        row["case_id"], row["phase"], row["policy_result_class"],
        -1.0 if row["gamma"] is None else row["gamma"], row["capacity_slots"]
    ))


def semantic_rows(
    audit: dict[str, Any], audit_sha: str, code_version: str
) -> list[dict[str, Any]]:
    rows = []
    for source in audit["cases"]:
        row = {
            "case_id": source["case_id"],
            "semantic_family": source["semantic_family"],
            "length_level": int(source["length_level"]),
            "templated_prompt_tokens": int(source["templated_prompt_tokens"]),
            "generated_token_count": int(source["generated_token_count"]),
            "generated_token_hash": source["generated_token_hash"],
            "sanity_label": source["sanity_label"],
            "sanity_reason": source["sanity_reason"] or "",
        }
        row.update(common_provenance(
            [audit_sha], "issue105-semantic-sanity-v1", code_version,
            "SEMANTIC_SANITY", "issue102_full_prompt"
        ))
        rows.append(row)
    return sorted(rows, key=lambda row: row["case_id"])


def quantile(values: Iterable[float], probability: float) -> float:
    ordered = sorted(float(value) for value in values)
    position = probability * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def distribution(values: Iterable[float]) -> dict[str, Any]:
    rows = list(float(value) for value in values)
    return {
        "count": len(rows), "min": min(rows), "p10": quantile(rows, 0.1),
        "median": statistics.median(rows), "p90": quantile(rows, 0.9), "max": max(rows),
    }


def compare_nested_numbers(observed: Any, expected: Any, path: str = "") -> None:
    if isinstance(expected, dict):
        if not isinstance(observed, dict):
            raise CurationError(f"fidelity type mismatch at {path}")
        for key, value in expected.items():
            compare_nested_numbers(observed.get(key), value, f"{path}/{key}")
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if observed is None or not math.isclose(float(observed), float(expected), rel_tol=1e-12, abs_tol=1e-12):
            raise CurationError(f"fidelity numeric mismatch at {path}: {observed} != {expected}")
    elif observed != expected:
        raise CurationError(f"fidelity mismatch at {path}: {observed!r} != {expected!r}")


def fidelity_report(
    physical: Sequence[dict[str, Any]],
    checkpoint: dict[str, Any],
    stage_c: dict[str, Any],
    exact: dict[str, Any],
    fingerprints: dict[str, Any],
    overlap: dict[str, Any],
    endpoints: dict[str, Any],
    final_synthesis: dict[str, Any],
    secondary_identities: dict[str, str],
) -> dict[str, Any]:
    primary = [row for row in physical if row["stage"] == "STAGE_A"]
    observed_distributions = {}
    for key in checkpoint["overall_distributions"]:
        observed_distributions[key] = distribution(row[key] for row in primary)
    compare_nested_numbers(observed_distributions, checkpoint["overall_distributions"], "/stage_a")
    stage_c_rows = [row for row in physical if row["stage"] == "STAGE_C"]
    report = {
        "schema_version": "issue105-curated-fidelity-v1",
        "status": "PASS",
        "stage_a": {
            "primary_rows": len(primary),
            "sentinels": len([row for row in physical if row["case_role"] == "sentinel"]),
            "overall_distributions": observed_distributions,
        },
        "stage_c": {
            "physical_exact_knee_rows": len(stage_c_rows),
            "unique_prompts": len(set(row["case_id"] for row in stage_c_rows)),
            "s2_rows_reused_from_stage_a": len(set(row["case_id"] for row in stage_c_rows)),
            "double_counted_s2_rows": 0,
            "failed_cells": stage_c["completeness"]["failed_cells"],
        },
        "observer": {
            "capture_identities": len(fingerprints["profiles"]),
            "representative_overlap_set": sum(
                row["selection_role"] == "STAGE_B_REPRESENTATIVE"
                for row in fingerprints["profiles"]
            ),
            "b1_b8_endpoints": 2 * len(endpoints["within_family_b1_b8"]),
            "semantic_order_prevalidation_matches": sum(
                1 for row in exact["prompt_rows"]
                if next(point for point in row["capacity_curve"] if point["physical_anchor"])
                ["observer_capture_physical_prevalidation"]["status"] == "MATCH"
            ),
        },
        "physical_exact_replay_validation": stage_c["physical_replay_anchor_validation"]["exact_match_count"],
        "published_secondary_artifact_identities": secondary_identities,
        "published_final_outcomes": final_synthesis["primary_outcomes"],
    }
    expected = {
        "stage_a": {"primary_rows": 128, "sentinels": 8},
        "stage_c": {"physical_exact_knee_rows": 48, "unique_prompts": 24, "failed_cells": 0},
        "observer": {
            "capture_identities": 44,
            "representative_overlap_set": 16,
            "b1_b8_endpoints": 32,
            "semantic_order_prevalidation_matches": 44,
        },
        "physical_exact_replay_validation": 16,
    }
    compare_nested_numbers(report, expected, "/headline")
    return report


def csv_logical_hash(rows: Sequence[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_json_bytes(row))
        digest.update(b"\n")
    return digest.hexdigest()


def write_csv_table(
    path: pathlib.Path, rows: Sequence[dict[str, Any]], fields: Sequence[str]
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            if set(row) != set(fields):
                raise CurationError(f"CSV schema mismatch for {path.name}")
            writer.writerow(row)
    os.replace(temporary, path)
    result = identity(path)
    result.update({"logical_sha256": csv_logical_hash(rows), "row_count": len(rows)})
    return result


def pyarrow_modules() -> tuple[Any, Any]:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ModuleNotFoundError as error:
        raise CurationError(
            "pyarrow is required; install scripts/issue105/analysis-requirements.txt"
        ) from error
    return pa, pq


def parquet_schema(fields: Sequence[tuple[str, str, bool]]) -> Any:
    pa, _ = pyarrow_modules()
    types = {
        "string": pa.string(), "int64": pa.int64(), "float64": pa.float64(), "bool": pa.bool_()
    }
    return pa.schema([pa.field(name, types[type_name], nullable=nullable) for name, type_name, nullable in fields])


def write_parquet_table(
    path: pathlib.Path,
    rows: Sequence[dict[str, Any]],
    fields: Sequence[tuple[str, str, bool]],
) -> dict[str, Any]:
    pa, pq = pyarrow_modules()
    schema = parquet_schema(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    table = pa.Table.from_pylist(list(rows), schema=schema)
    pq.write_table(
        table, temporary, compression="zstd", compression_level=9,
        use_dictionary=True, write_statistics=True, version="2.6",
    )
    os.replace(temporary, path)
    result = identity(path)
    result.update({
        "logical_sha256": csv_logical_hash(rows), "row_count": len(rows),
        "writer": f"pyarrow {pa.__version__}",
        "settings": "parquet-2.6,zstd-9,dictionary,statistics",
    })
    return result


def write_sparse_observer_routes(
    path: pathlib.Path,
    fingerprints: dict[str, Any],
    fingerprints_sha: str,
    code_version: str,
) -> dict[str, Any]:
    pa, pq = pyarrow_modules()
    fields = [
        ("case_id", "string", False), ("semantic_family", "string", False),
        ("length_level", "int64", False), ("selection_role", "string", False),
        ("prompt_tokens", "int64", False), ("phase", "string", False),
        ("layer", "int64", False), ("expert", "int64", False),
        ("selected_count", "int64", False),
        ("source_sha256", "string", False), ("derivation_id", "string", False),
        ("analysis_code_version", "string", False),
        ("source_evidence_class", "string", False),
        ("derived_evidence_class", "string", False),
        ("protocol", "string", False), ("authority_scope", "string", False),
    ]
    schema = parquet_schema(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    writer = pq.ParquetWriter(
        temporary, schema, compression="zstd", compression_level=9,
        use_dictionary=True, write_statistics=True, version="2.6",
    )
    digest = hashlib.sha256()
    batch: list[dict[str, Any]] = []
    count = 0
    try:
        for profile in sorted(fingerprints["profiles"], key=lambda row: row["case_id"]):
            provenance = common_provenance(
                [fingerprints_sha, profile["result"]["sha256"]],
                "issue105-observer-routes-v1", code_version,
                "MEASURED_OBSERVER", "issue102_observer_semantic_order"
            )
            for phase in ("PREFILL", "DECODE"):
                matrix = profile["phases"][phase]["selected_frequency_matrix_92x896"]
                for layer_index, frequencies in enumerate(matrix, start=1):
                    for expert, selected_count in enumerate(frequencies):
                        if not selected_count:
                            continue
                        row = {
                            "case_id": profile["case_id"],
                            "semantic_family": profile["semantic_family"],
                            "length_level": int(profile["length_level"]),
                            "selection_role": profile["selection_role"],
                            "prompt_tokens": int(profile["prompt_tokens"]),
                            "phase": phase,
                            "layer": layer_index,
                            "expert": expert,
                            "selected_count": int(selected_count),
                        }
                        row.update(provenance)
                        digest.update(canonical_json_bytes(row))
                        digest.update(b"\n")
                        batch.append(row)
                        count += 1
                        if len(batch) >= 100000:
                            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                            batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()
    os.replace(temporary, path)
    result = identity(path)
    result.update({
        "logical_sha256": digest.hexdigest(), "row_count": count,
        "writer": f"pyarrow {pa.__version__}",
        "settings": "parquet-2.6,zstd-9,dictionary,statistics,batch-100000",
    })
    return result


def schema_sidecar(
    name: str,
    fields: Sequence[tuple[str, str, bool]],
    sort_order: Sequence[str],
    fmt: str,
) -> dict[str, Any]:
    return {
        "schema_version": f"issue105-{name}-v1",
        "format": fmt,
        "fields": [
            {"name": field_name, "type": type_name, "nullable": nullable}
            for field_name, type_name, nullable in fields
        ],
        "canonical_row_order": list(sort_order),
        "logical_hash": "SHA-256 over sorted-key compact UTF-8 JSON for each row plus LF",
    }


def fields_from_rows(rows: Sequence[dict[str, Any]]) -> list[str]:
    if not rows:
        raise CurationError("cannot infer fields from empty table")
    return list(rows[0].keys())


def materialized_documents(extract_root: pathlib.Path) -> dict[str, dict[str, Any]]:
    return {relative: load_json(extract_root / relative) for relative in SELECTED_ARCHIVE_PATHS}


def main() -> None:
    args = arguments()
    repository = args.repository_root.resolve(strict=True)
    archive = args.issue102_archive.resolve(strict=True)
    issue98_root = args.issue98_root.resolve(strict=True)
    output_root = args.output_root.resolve()
    work_root = args.work_root.resolve()
    lock = load_json(args.source_lock.resolve(strict=True))
    issue102_lock = lock["issue102"]
    issue98_lock = lock["issue98"]

    require_identity(
        archive,
        issue102_lock["archive"]["sha256"],
        issue102_lock["archive"]["bytes"],
    )
    git_attestation = verify_git_target(repository, issue102_lock)
    issue102_review = verify_review_attestation(args.github_repository, issue102_lock["final_review"])
    issue98_review = verify_review_attestation(args.github_repository, issue98_lock["final_review"])

    phase_root = repository / PHASE_RELATIVE
    archive_index_path = phase_root / "issue102-evidence-archive-index.json"
    archive_index = load_json(archive_index_path)
    if archive_index.get("schema_version") != "phase13-6pg-issue102-evidence-archive-index-v1":
        raise CurationError("issue-102 archive index schema mismatch")
    if len(archive_index["members"]) != issue102_lock["archive"]["member_count"]:
        raise CurationError("issue-102 archive index member count mismatch")
    extracted_root = work_root / "selected"
    archive_verification = verify_issue102_archive(archive, archive_index, extracted_root)
    documents = materialized_documents(extracted_root)
    secondary_identities = {}
    for relative, expected_sha in issue102_lock["secondary_artifacts"]:
        require_identity(extracted_root / relative, expected_sha)
        secondary_identities[relative] = expected_sha

    v3_index_path = issue98_root / issue98_lock["archive_index"]["file"]
    require_identity(v3_index_path, issue98_lock["archive_index"]["sha256"])
    v3_index = load_json(v3_index_path)
    issue98_verification = []
    for archive_lock in issue98_lock["archives"]:
        path = issue98_root / archive_lock["file"]
        require_identity(path, archive_lock["sha256"])
        issue98_verification.append(verify_issue98_archive(
            path,
            int(archive_lock["member_file_count"]),
            v3_index if archive_lock["file"].endswith("v3.tar.zst") else None,
        ))
    issue98_synthesis_path = issue98_root / issue98_lock["final_synthesis"]["file"]
    require_identity(issue98_synthesis_path, issue98_lock["final_synthesis"]["sha256"])
    issue98_synthesis = load_json(issue98_synthesis_path)
    if issue98_synthesis.get("schema_version") != issue98_lock["final_synthesis"]["schema_version"]:
        raise CurationError("issue-98 final synthesis schema mismatch")

    exact = documents["host/observer-replay-v1/exact-capacity-mrc.json"]
    observer_prefixes = accepted_observer_prefixes(archive_index["members"], exact)
    sources = build_source_catalog(
        archive_index, schema_versions(extracted_root), observer_prefixes,
        issue98_lock, issue98_root,
    )
    if any(
        row["status"] != "accepted"
        for row in sources
        if row["sha256"] in set(secondary_identities.values())
    ):
        raise CurationError("mandatory issue-102 secondary source was not accepted")

    corpus_path = repository / "corpus/phase13/issue102-cross-prompt-v1.json"
    checkpoint_path = phase_root / "stage-a-round-08-checkpoint.json"
    stage_c_path = phase_root / "stage-c-synthesis.json"
    semantic_path = phase_root / "semantic-sanity-audit.json"
    final_synthesis_path = phase_root / "issue102-final-synthesis.json"
    corpus = load_json(corpus_path)
    checkpoint = load_json(checkpoint_path)
    stage_c = load_json(stage_c_path)
    semantic_audit = load_json(semantic_path)
    final_synthesis = load_json(final_synthesis_path)
    corpus_sha = sha256(corpus_path)
    checkpoint_sha = sha256(checkpoint_path)
    stage_c_sha = sha256(stage_c_path)
    semantic_sha = sha256(semantic_path)
    final_synthesis_sha = sha256(final_synthesis_path)
    progress_sha = secondary_identities["host/stage-c-control-v2/progress.json"]
    fingerprints_sha = secondary_identities["host/stage-b-analysis-v1/family-route-fingerprints.json"]
    exact_sha = secondary_identities["host/observer-replay-v1/exact-capacity-mrc.json"]
    committee_sha = secondary_identities["host/stage-b-analysis-v1/standing-committee-core-periphery.json"]
    s2_sha = secondary_identities["host/observer-replay-v1/s2-fixed-route-capacity-counterfactual.json"]

    cases = case_rows(corpus, corpus_sha, exact, stage_c, args.analysis_code_version)
    physical = physical_rows(
        checkpoint, checkpoint_sha,
        documents["host/stage-c-control-v2/progress.json"], progress_sha,
        args.analysis_code_version,
    )
    legacy = legacy_rows(
        issue98_synthesis, issue98_lock["final_synthesis"]["sha256"],
        args.analysis_code_version,
    )
    fingerprints = documents["host/stage-b-analysis-v1/family-route-fingerprints.json"]
    committee = documents["host/stage-b-analysis-v1/standing-committee-core-periphery.json"]
    route_features = route_feature_rows(
        fingerprints, fingerprints_sha, exact, exact_sha, committee,
        committee_sha, args.analysis_code_version,
    )
    capacity = capacity_rows(
        exact, exact_sha,
        documents["host/observer-replay-v1/s2-fixed-route-capacity-counterfactual.json"], s2_sha,
        documents["host/observer-replay-v1/committee-pin-capacity-counterfactual.json"],
        secondary_identities["host/observer-replay-v1/committee-pin-capacity-counterfactual.json"],
        args.analysis_code_version,
    )
    semantic = semantic_rows(semantic_audit, semantic_sha, args.analysis_code_version)
    fidelity = fidelity_report(
        physical, checkpoint, stage_c, exact, fingerprints,
        documents["host/stage-b-analysis-v1/family-overlap-matrix.json"],
        documents["host/stage-b-analysis-v1/stage-b2-family-length-route-endpoints.json"],
        final_synthesis, secondary_identities,
    )
    fidelity["source_sha256"] = sorted({
        checkpoint_sha, stage_c_sha, exact_sha, fingerprints_sha, final_synthesis_sha,
    })
    fidelity["analysis_code_version"] = args.analysis_code_version
    fidelity["derived_evidence_class"] = "CURATED_FROM_MEASURED"

    output_root.mkdir(parents=True, exist_ok=True)
    table_root = output_root / "tables"
    schema_root = output_root / "schemas"
    artifacts = {}

    source_fields = fields_from_rows(sources)
    artifacts["source_catalog"] = write_csv_table(
        output_root / "source-catalog.csv", sources, source_fields
    )
    source_schema = load_json(repository / "schemas/issue105/source-catalog-v1.schema.json")
    artifacts["source_catalog_schema"] = write_json(
        output_root / "source-catalog.schema.json", source_schema
    )
    for name, rows, order in (
        ("cases", cases, ["case_id"]),
        ("physical_runs", physical, ["stage", "case_id", "policy"]),
        ("legacy_physical_context", legacy, ["policy"]),
        ("route_features", route_features, ["case_id", "phase"]),
        ("semantic_sanity", semantic, ["case_id"]),
    ):
        fields = fields_from_rows(rows)
        artifacts[name] = write_csv_table(table_root / f"{name}.csv", rows, fields)
        artifacts[f"{name}_schema"] = write_json(
            schema_root / f"{name}.schema.json",
            {
                "schema_version": f"issue105-{name}-v1",
                "format": "CSV/RFC4180 with LF records",
                "fields": fields,
                "nullable_fields": sorted(
                    field for field in fields if any(row[field] is None for row in rows)
                ),
                "canonical_row_order": order,
                "logical_hash": "SHA-256 over sorted-key compact UTF-8 JSON for each row plus LF",
            },
        )

    capacity_fields = [
        ("case_id", "string", False), ("semantic_family", "string", False),
        ("length_level", "int64", True), ("selection_role", "string", False),
        ("phase", "string", False), ("capacity_label", "string", False),
        ("capacity_slots", "int64", False), ("capacity_bytes", "int64", False),
        ("policy_result_class", "string", False), ("gamma", "float64", True),
        ("status", "string", False), ("hit_ratio", "float64", True),
        ("loads_per_token", "float64", True), ("bytes_per_token", "float64", True),
        ("physical_anchor", "bool", False), ("validation_stratum", "string", False),
    ] + [(field, "string", False) for field in COMMON_FIELDS]
    artifacts["capacity_curves"] = write_parquet_table(
        table_root / "capacity_curves.parquet", capacity, capacity_fields
    )
    artifacts["capacity_curves_schema"] = write_json(
        schema_root / "capacity_curves.schema.json",
        schema_sidecar(
            "capacity-curves", capacity_fields,
            ["case_id", "phase", "policy_result_class", "gamma", "capacity_slots"],
            "Parquet 2.6 + Zstd",
        ),
    )
    observer_fields = [
        ("case_id", "string", False), ("semantic_family", "string", False),
        ("length_level", "int64", False), ("selection_role", "string", False),
        ("prompt_tokens", "int64", False), ("phase", "string", False),
        ("layer", "int64", False), ("expert", "int64", False),
        ("selected_count", "int64", False),
    ] + [(field, "string", False) for field in COMMON_FIELDS]
    artifacts["observer_routes"] = write_sparse_observer_routes(
        table_root / "observer_routes.parquet", fingerprints,
        fingerprints_sha, args.analysis_code_version,
    )
    artifacts["observer_routes_schema"] = write_json(
        schema_root / "observer_routes.schema.json",
        schema_sidecar(
            "observer-routes", observer_fields,
            ["case_id", "phase=PREFILL,DECODE", "layer", "expert"],
            "Parquet 2.6 + Zstd",
        ),
    )
    artifacts["fidelity_report"] = write_json(output_root / "fidelity-report.json", fidelity)

    ingestion = {
        "schema_version": "issue105-ingestion-report-v1",
        "status": "PASS",
        "issue102": {
            "release_asset": {
                "name": issue102_lock["archive"]["name"],
                "bytes": archive.stat().st_size,
                "sha256": sha256(archive),
                "tag": issue102_lock["archive"]["release_tag"],
            },
            "git_attestation": git_attestation,
            "review_attestation": issue102_review,
            "archive_verification": archive_verification,
            "freeze_status_preserved": "pre-publication fields retained; post-freeze attestations verified separately",
        },
        "issue98": {
            "protocol": "legacy_first_full",
            "structurally_isolated": True,
            "archives": issue98_verification,
            "review_attestation": issue98_review,
            "final_synthesis_sha256": issue98_lock["final_synthesis"]["sha256"],
        },
        "catalog": {
            "rows": len(sources),
            "status_counts": {
                status: sum(row["status"] == status for row in sources)
                for status in sorted(SOURCE_STATUSES)
            },
            "mandatory_issue102_inputs_accepted": True,
            "authority_conflicts": 0,
        },
        "analysis_code_version": args.analysis_code_version,
        "limitations": [
            "No K3 model execution or missing-cell recovery was performed.",
            "Issue 98 remains protocol-distinct context and is never pooled with issue 102.",
            "Observer timing is not admitted as physical performance evidence.",
        ],
    }
    artifacts["ingestion_report"] = write_json(output_root / "ingestion-report.json", ingestion)

    catalog_rows = []
    for name, artifact in sorted(artifacts.items()):
        relative = pathlib.Path(artifact["path"]).relative_to(output_root).as_posix()
        catalog_rows.append({
            "artifact_id": name,
            "path": relative,
            "bytes": artifact["bytes"],
            "file_sha256": artifact["sha256"],
            "logical_sha256": artifact.get("logical_sha256", ""),
            "row_count": artifact.get("row_count"),
            "writer": artifact.get("writer", "python-json/csv deterministic writer"),
            "settings": artifact.get("settings", "UTF-8,LF,sorted JSON keys where applicable"),
        })
    catalog = {
        "schema_version": "issue105-canonical-catalog-v1",
        "status": "PASS",
        "analysis_code_version": args.analysis_code_version,
        "artifacts": catalog_rows,
        "self_reference": "canonical-catalog.json intentionally excludes its own recursive identity",
    }
    write_json(output_root / "canonical-catalog.json", catalog)
    print(json.dumps({
        "status": "PASS",
        "output_root": str(output_root),
        "source_rows": len(sources),
        "observer_route_rows": artifacts["observer_routes"]["row_count"],
        "capacity_curve_rows": artifacts["capacity_curves"]["row_count"],
        "physical_rows": len(physical),
    }, sort_keys=True))


if __name__ == "__main__":
    main()

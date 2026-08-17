#!/usr/bin/env python3
"""Freeze exact issue-102 output-only A/B/C page-cache hygiene targets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import stat
from typing import Any


EXPECTED_REPLAY_INDEX_SHA256 = "d49db861f81ade803ab3d9c9e07c10d803247d2f3bcaf15b84d8afbf8a9b3dac"
CLASS_A_ROOT = pathlib.Path("/mnt/nvme1/issue102/stage-b-observer")
CLASS_B_ROOTS = (
    pathlib.Path("/mnt/nvme1/issue102/stage-b-analysis-v1"),
    pathlib.Path("/mnt/nvme1/issue102/observer-replay-v1"),
    pathlib.Path("/mnt/nvme1/issue102/posthoc-analysis-v1"),
)
CLASS_C_ROOTS = (
    pathlib.Path("/mnt/nvme1/issue102/stage-c-v1"),
    pathlib.Path("/mnt/nvme1/issue102/stage-c-v2"),
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-allowlist", type=pathlib.Path)
    parser.add_argument("--expected-base-allowlist-sha256")
    parser.add_argument("--observer-allowlist", type=pathlib.Path)
    parser.add_argument("--expected-observer-allowlist-sha256", required=True)
    parser.add_argument("--handoff", type=pathlib.Path)
    parser.add_argument("--expected-handoff-sha256", required=True)
    parser.add_argument("--route-index", type=pathlib.Path)
    parser.add_argument("--expected-route-index-sha256", required=True)
    parser.add_argument("--replay-index", type=pathlib.Path)
    parser.add_argument("--posthoc-index", type=pathlib.Path)
    parser.add_argument("--expected-posthoc-index-sha256", required=True)
    parser.add_argument("--original-progress", type=pathlib.Path)
    parser.add_argument("--expected-original-progress-sha256", required=True)
    parser.add_argument("--amended-progress", type=pathlib.Path)
    parser.add_argument("--expected-amended-progress-sha256")
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


def load(path: pathlib.Path, expected: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    source = identity(path, expected)
    with path.resolve(strict=True).open() as stream:
        return source, json.load(stream)


def has_symlink_component(path: pathlib.Path, root: pathlib.Path) -> bool:
    current = root
    if current.is_symlink():
        return True
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def freeze_recorded_file(
    artifact_class: str, source: str, artifact: dict[str, Any], roots: tuple[pathlib.Path, ...],
) -> dict[str, Any]:
    declared = pathlib.Path(artifact["path"])
    if not declared.is_absolute():
        raise ValueError(f"output path is not absolute: {declared}")
    resolved = declared.resolve(strict=True)
    canonical_roots = [root.resolve(strict=False) for root in roots]
    matching_roots = [root for root in canonical_roots if resolved.is_relative_to(root)]
    if resolved != declared or len(matching_roots) != 1 or has_symlink_component(resolved, matching_roots[0]):
        raise ValueError(f"output path escapes/aliases its frozen class root: {declared}")
    metadata = os.lstat(resolved)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size != artifact["bytes"]
    ):
        raise ValueError(f"recorded output metadata changed: {resolved}")
    if "device" in artifact and metadata.st_dev != artifact["device"]:
        raise ValueError(f"recorded output device changed: {resolved}")
    if "inode" in artifact and metadata.st_ino != artifact["inode"]:
        raise ValueError(f"recorded output inode changed: {resolved}")
    return {
        "artifact_class": artifact_class,
        "source": source,
        "canonical_path": str(resolved),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "bytes": metadata.st_size,
        "sha256": artifact["sha256"],
        "content_identity_source": "ALREADY_PRESERVED_PRE_RELEASE",
    }


def class_b_files(
    handoff: dict[str, Any], route: dict[str, Any], replay: dict[str, Any], posthoc: dict[str, Any],
) -> list[dict[str, Any]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    route_index = handoff["artifacts"]["stage_b_route_analysis_index"]
    recorded.append(("stage-b-route-analysis-index", route_index))
    for name, artifact in route["artifacts"].items():
        recorded.append((f"stage-b-route-analysis:{name}", artifact))

    replay_path = pathlib.Path(replay["artifacts"]["exact_capacity_mrc"]).parent / "observer-replay-index.json"
    recorded.append(("observer-replay-index", {
        "path": str(replay_path.resolve(strict=True)),
        "bytes": replay_path.stat().st_size,
        "sha256": EXPECTED_REPLAY_INDEX_SHA256,
    }))
    replay_handoff_keys = {
        "exact_capacity_mrc",
        "s2_fixed_route_capacity_counterfactual",
        "committee_pin_capacity_counterfactual",
        "family_length_capacity_extension",
    }
    for name in sorted(replay_handoff_keys):
        recorded.append((f"observer-replay:{name}", handoff["artifacts"][name]))

    recorded.append(("stage-a-posthoc-analysis-index", handoff["artifacts"]["stage_a_posthoc_analysis_index"]))
    for name, artifact in posthoc["artifacts"].items():
        recorded.append((f"stage-a-posthoc:{name}", artifact))
    if len(recorded) != 14:
        raise AssertionError("class-B source set did not produce exactly 14 files")
    return [
        freeze_recorded_file("B_POSTPROCESSING_OUTPUT", source, artifact, CLASS_B_ROOTS)
        for source, artifact in recorded
    ]


def progress_artifacts(progress: dict[str, Any], source_prefix: str) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for capture in progress.get("captures", []):
        for name, artifact in capture["artifacts"].items():
            rows.append((f"{source_prefix}:accepted-{capture['run_ordinal']:03d}:{name}", artifact))
    for failure in progress.get("failures", []):
        for name, artifact in failure.get("artifacts", {}).items():
            rows.append((f"{source_prefix}:failed-{failure['run_ordinal']:03d}:{name}", artifact))
    return rows


def main() -> None:
    args = arguments()
    base_id = observer_id = handoff_id = route_id = replay_id = posthoc_id = original_id = None
    if args.base_allowlist is not None:
        if args.expected_base_allowlist_sha256 is None:
            raise ValueError("incremental mode requires the expected base allowlist SHA")
        base_id, base = load(args.base_allowlist, args.expected_base_allowlist_sha256)
        if (
            base.get("schema_version") != "phase13-6pg-stage-c-output-cache-allowlist-v1"
            or base.get("status") != "frozen"
            or base.get("class_file_counts", {}).get("A_OBSERVER_OUTPUT") != 182
            or base.get("class_file_counts", {}).get("B_POSTPROCESSING_OUTPUT") != 14
        ):
            raise ValueError("incremental base allowlist changed")
        roots_by_class = {
            "A_OBSERVER_OUTPUT": (CLASS_A_ROOT,),
            "B_POSTPROCESSING_OUTPUT": CLASS_B_ROOTS,
            "C_STAGE_C_OUTPUT": CLASS_C_ROOTS,
        }
        files = [
            freeze_recorded_file(
                row["artifact_class"], row["source"],
                {
                    "path": row["canonical_path"], "bytes": row["bytes"],
                    "sha256": row["sha256"], "device": row["device"], "inode": row["inode"],
                },
                roots_by_class[row["artifact_class"]],
            )
            for row in base["files"]
        ]
    else:
        full_paths = (
            args.observer_allowlist, args.handoff, args.route_index, args.replay_index,
            args.posthoc_index, args.original_progress,
        )
        if any(path is None for path in full_paths):
            raise ValueError("initial mode requires every frozen A/B/original-C source")
        observer_id, observer = load(args.observer_allowlist, args.expected_observer_allowlist_sha256)
        handoff_id, handoff = load(args.handoff, args.expected_handoff_sha256)
        route_id, route = load(args.route_index, args.expected_route_index_sha256)
        replay_id, replay = load(args.replay_index, EXPECTED_REPLAY_INDEX_SHA256)
        posthoc_id, posthoc = load(args.posthoc_index, args.expected_posthoc_index_sha256)
        original_id, original = load(args.original_progress, args.expected_original_progress_sha256)
        if (
            observer.get("status") != "frozen"
            or observer.get("file_count") != 182
            or handoff.get("status") != "pass"
            or route.get("status") != "pass"
            or replay.get("status") != "pass"
            or posthoc.get("status") != "pass"
            or original.get("status") != "failed"
            or original.get("accepted_cell_count") != 0
            or original.get("failed_cell_count") != 1
        ):
            raise ValueError("output hygiene sources are not in the expected frozen state")

        files = [
            freeze_recorded_file(
                "A_OBSERVER_OUTPUT", row.get("source", "observer-output"),
                {
                    "path": row["canonical_path"], "bytes": row["bytes"], "sha256": row["sha256"],
                    "device": row["device"], "inode": row["inode"],
                },
                (CLASS_A_ROOT,),
            )
            for row in observer["files"]
        ]
        files.extend(class_b_files(handoff, route, replay, posthoc))
        files.extend(
            freeze_recorded_file("C_STAGE_C_OUTPUT", source, artifact, CLASS_C_ROOTS)
            for source, artifact in progress_artifacts(original, "original-progress")
        )

    amended_id = None
    if args.amended_progress is not None:
        if args.expected_amended_progress_sha256 is None:
            raise ValueError("amended progress requires its expected SHA")
        amended_id, amended = load(args.amended_progress, args.expected_amended_progress_sha256)
        if amended.get("schema_version") != "phase13-6pg-stage-c-recovery-progress-v1":
            raise ValueError("amended Stage-C progress schema changed")
        existing_paths = {row["canonical_path"] for row in files}
        for source, artifact in progress_artifacts(amended, "recovery-progress"):
            if str(pathlib.Path(artifact["path"]).resolve(strict=True)) not in existing_paths:
                files.append(freeze_recorded_file(
                    "C_STAGE_C_OUTPUT", source, artifact, CLASS_C_ROOTS,
                ))

    paths = [row["canonical_path"] for row in files]
    inodes = [(row["device"], row["inode"]) for row in files]
    if len(set(paths)) != len(paths) or len(set(inodes)) != len(inodes):
        raise ValueError("A/B/C allowlist contains duplicate paths or inodes")
    counts = {
        artifact_class: sum(row["artifact_class"] == artifact_class for row in files)
        for artifact_class in (
            "A_OBSERVER_OUTPUT", "B_POSTPROCESSING_OUTPUT", "C_STAGE_C_OUTPUT",
        )
    }
    if counts["A_OBSERVER_OUTPUT"] != 182 or counts["B_POSTPROCESSING_OUTPUT"] != 14:
        raise ValueError("A/B class coverage changed")
    output = {
        "schema_version": "phase13-6pg-stage-c-output-cache-allowlist-v1",
        "status": "frozen",
        "purpose": "TARGETED_ISSUE102_OUTPUT_ONLY_PAGE_CACHE_RELEASE",
        "inputs": {
            "generator": identity(pathlib.Path(__file__)),
            "base_allowlist": base_id,
            "observer_allowlist": observer_id,
            "stage_b_capacity_handoff": handoff_id,
            "route_index": route_id,
            "replay_index": replay_id,
            "posthoc_index": posthoc_id,
            "original_failed_progress": original_id,
            "amended_progress": amended_id,
        },
        "syncfs_root": "/mnt/nvme1",
        "allowed_roots": {
            "A_OBSERVER_OUTPUT": [str(CLASS_A_ROOT)],
            "B_POSTPROCESSING_OUTPUT": [str(root) for root in CLASS_B_ROOTS],
            "C_STAGE_C_OUTPUT": [str(root) for root in CLASS_C_ROOTS],
        },
        "file_count": len(files),
        "class_file_counts": counts,
        "total_bytes": sum(row["bytes"] for row in files),
        "files": files,
        "operation": {
            "advice": "POSIX_FADV_DONTNEED",
            "payload_hash_or_read_during_build": False,
            "model_runtime_corpus_control_or_source_allowed": False,
            "global_cache_operation_allowed": False,
        },
        "disposition": "READY_FOR_STAGE_C_OUTPUT_ONLY_HYGIENE",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, args.output)
    print(json.dumps({
        "status": "pass", "output": identity(args.output),
        "file_count": len(files), "class_file_counts": counts,
    }, sort_keys=True))


if __name__ == "__main__":
    main()

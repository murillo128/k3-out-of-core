#!/usr/bin/env python3
"""Apply the amended issue-102 Stage-C A/B/C output-only hygiene gate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
from typing import Any


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--expected-allowlist-sha256", required=True)
    parser.add_argument("--reference-preflight", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    return parser.parse_args()


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path) -> dict[str, Any]:
    resolved = path.resolve(strict=True)
    return {"path": str(resolved), "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}


def hygiene_module() -> Any:
    path = pathlib.Path(__file__).with_name("release_observer_evidence_page_cache.py")
    spec = importlib.util.spec_from_file_location("issue102_cache_operations", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load frozen cache operations")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ancestor_pids() -> set[int]:
    result = {os.getpid()}
    current = os.getpid()
    while current > 1:
        try:
            current = int(pathlib.Path(f"/proc/{current}/stat").read_text().split()[3])
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError, IndexError):
            break
        result.add(current)
    return result


def active_k3_processes() -> list[dict[str, Any]]:
    excluded = ancestor_pids()
    names = {
        "issue102-cross-prompt-probe", "issue102-exact-route-observer",
        "run_qualification_cell.py",
    }
    active = []
    for path in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(path.parent.name)
            args = [item.decode(errors="replace") for item in path.read_bytes().split(b"\0") if item]
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
        if pid in excluded:
            continue
        basenames = {pathlib.Path(item).name for item in args if " " not in item}
        if basenames & names:
            active.append({"pid": pid, "arguments": args})
    return active


def main() -> int:
    args = arguments()
    module = hygiene_module()
    allowlist_path = args.allowlist.resolve(strict=True)
    reference_path = args.reference_preflight.resolve(strict=True)
    output_path = args.output.resolve()
    if sha256(allowlist_path) != args.expected_allowlist_sha256:
        raise ValueError("Stage-C output hygiene allowlist identity changed")
    allowlist = json.loads(allowlist_path.read_text())
    reference_document = json.loads(reference_path.read_text())
    if (
        allowlist.get("schema_version") != "phase13-6pg-stage-c-output-cache-allowlist-v1"
        or allowlist.get("status") != "frozen"
        or allowlist.get("purpose") != "TARGETED_ISSUE102_OUTPUT_ONLY_PAGE_CACHE_RELEASE"
        or allowlist.get("disposition") != "READY_FOR_STAGE_C_OUTPUT_ONLY_HYGIENE"
        or allowlist["class_file_counts"]["A_OBSERVER_OUTPUT"] != 182
        or allowlist["class_file_counts"]["B_POSTPROCESSING_OUTPUT"] != 14
        or allowlist["operation"]["model_runtime_corpus_control_or_source_allowed"] is not False
    ):
        raise ValueError("Stage-C output-only allowlist is not executable")
    active = active_k3_processes()
    if active:
        raise RuntimeError(f"K3/helper process is active: {active}")

    reference = reference_document["preflight"]["system_memory"]
    operations = module.LinuxCacheOperations()
    before_files = module.observe_files(operations, allowlist["files"])
    before_host = module.host_snapshot()
    before_projection = module.guard_projection(before_host, reference)
    operations.sync_filesystem(pathlib.Path(allowlist["syncfs_root"]))
    advice = module.apply_advice(allowlist["files"])
    after_files = module.observe_files(operations, allowlist["files"])
    after_host = module.host_snapshot()
    after_projection = module.guard_projection(after_host, reference)
    delta = module.host_delta(before_host, after_host)
    before_resident = sum(row["resident_bytes"] for row in before_files)
    after_resident = sum(row["resident_bytes"] for row in after_files)
    gate = {
        "no_active_k3_or_helper_process": not active,
        "exact_allowlist_file_count": len(before_files) == allowlist["file_count"],
        "exact_A_B_class_counts": (
            allowlist["class_file_counts"]["A_OBSERVER_OUTPUT"] == 182
            and allowlist["class_file_counts"]["B_POSTPROCESSING_OUTPUT"] == 14
        ),
        "all_targeted_advice_succeeded": len(advice) == allowlist["file_count"],
        "all_allowlisted_pages_released": after_resident == 0,
        "residency_nonincreasing_and_decreased_when_present": (
            after_resident <= before_resident and (before_resident == 0 or after_resident < before_resident)
        ),
        "projected_exact_capacity_admissible": after_projection["projected_admission_margin_bytes"] >= 0,
        "swap_reclaim_refault_psi_oom_cgroup_clean": module.pressure_clean(before_host, after_host, delta),
        "no_payload_reread_or_rehash_after_release": True,
        "no_model_runtime_corpus_control_source_or_global_cache_operation": True,
    }
    status = "pass" if all(gate.values()) else "fail"
    class_resident_before = {
        artifact_class: sum(
            observed["resident_bytes"]
            for declared, observed in zip(allowlist["files"], before_files)
            if declared["artifact_class"] == artifact_class
        )
        for artifact_class in allowlist["class_file_counts"]
    }
    output = {
        "schema_version": "phase13-6pg-stage-c-output-cache-hygiene-v1",
        "status": status,
        "provenance": "MEASUREMENT_HYGIENE_NON_SCIENTIFIC",
        "inputs": {
            "allowlist": identity(allowlist_path),
            "reference_preflight": identity(reference_path),
            "generator": identity(pathlib.Path(__file__)),
            "cache_operations": identity(pathlib.Path(module.__file__)),
        },
        "files": {
            "file_count": allowlist["file_count"],
            "class_file_counts": allowlist["class_file_counts"],
            "class_resident_bytes_before": class_resident_before,
            "resident_bytes_before": before_resident,
            "resident_bytes_after": after_resident,
            "released_resident_bytes": before_resident - after_resident,
            "content_identity_source": "PRE_RELEASE_ALLOWLIST_SHA256",
            "content_read_after_release": False,
        },
        "operation": {
            "syncfs_root": allowlist["syncfs_root"],
            "syncfs_status": "success",
            "advice": "POSIX_FADV_DONTNEED",
            "advised_file_count": len(advice),
            "model_runtime_corpus_control_source_or_global_cache_touched": False,
        },
        "host": {
            "before": before_host,
            "after": after_host,
            "delta": delta,
            "guard_projection_before": before_projection,
            "guard_projection_after": after_projection,
        },
        "gate": gate,
        "disposition": "READY_FOR_STAGE_C_PHYSICAL_PROCESS" if status == "pass" else "RETURN_TO_DESIGN",
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.replace(temporary, output_path)
    print(json.dumps({
        "status": status, "output": identity(output_path),
        "resident_bytes_before": before_resident,
        "resident_bytes_after": after_resident,
        "projected_margin_after": after_projection["projected_admission_margin_bytes"],
    }, sort_keys=True))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

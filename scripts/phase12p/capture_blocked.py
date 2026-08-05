#!/usr/bin/env python3
"""Capture an honest Phase 12P storage-capacity block before corpus creation."""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import platform
import subprocess
import tarfile
import tempfile
from pathlib import Path

from common import (
    CONFIG_SHA256, FULL_SCALE, GIB, PAYLOAD_VERSION, ROUTE_VERSION, SOURCE_REVISION,
    canonical_bytes, preflight, route_identity, sha256_file, write_json,
)

ROOT = Path(__file__).resolve().parents[2]
PHASE8 = ROOT / "results/2026-07-31/skynet/phase8-miss-execution/synthetic-store.json"
PHASE11 = ROOT / "results/2026-08-04/msi-edgexpert-gb10/phase11-uma/phase11-manifest.json"


def command(*arguments: str) -> dict[str, object]:
    completed = subprocess.run(arguments, text=True, capture_output=True)
    return {"command": list(arguments), "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def git(*arguments: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output(["git", *arguments], cwd=cwd, text=True).strip()


def io_uring_probe() -> dict[str, object]:
    # __NR_io_uring_setup is 425 on Linux aarch64 and x86_64.
    parameters = (ctypes.c_ubyte * 256)()
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(425, 1, ctypes.byref(parameters))
    saved_errno = ctypes.get_errno()
    if result >= 0:
        os.close(result)
    return {
        "syscall": "io_uring_setup", "entries": 1, "result": result,
        "errno": saved_errno if result < 0 else 0,
        "errno_name": errno.errorcode.get(saved_errno, "UNKNOWN") if result < 0 else "OK",
        "seccomp_status": Path("/proc/self/status").read_text().split("Seccomp:", 1)[1].splitlines()[0].strip(),
    }


def identity(path: Path) -> dict[str, object]:
    return {"path": str(path.relative_to(ROOT)), "git_blob": git("hash-object", str(path)), "size": path.stat().st_size, "sha256": sha256_file(path)}


def deterministic_tar(output: Path, members: list[Path]) -> None:
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in sorted(members, key=lambda item: item.name):
            data = path.read_bytes()
            info = tarfile.TarInfo(path.name); info.size = len(data); info.mtime = 0
            info.uid = info.gid = 0; info.uname = info.gname = ""; info.mode = 0o644
            with tempfile.SpooledTemporaryFile() as stream:
                stream.write(data); stream.seek(0); archive.addfile(info, stream)


def capture(output: Path) -> dict[str, object]:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    gate = preflight(ROOT)
    mount = command("findmnt", "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,USED,AVAIL", "-T", str(ROOT))
    block = command("lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,FSTYPE,SIZE,FSAVAIL,MOUNTPOINTS,MODEL,SERIAL,SCHED")
    device_nodes = {name: Path(name).exists() for name in ("/dev/nvme0n1", "/dev/nvme0n1p3")}
    capability = {
        "schema_version": "phase12p-capabilities-v1", "captured_at_utc": "2026-08-05",
        "host": platform.node(), "host_class": "NVIDIA GB10 coherent UMA",
        "kernel": platform.release(), "architecture": platform.machine(),
        "project_head": git("rev-parse", "HEAD"), "nested_head": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
        "storage_preflight": gate, "mount": mount, "block_devices": block,
        "device_nodes_visible": device_nodes, "io_uring": io_uring_probe(),
        "cgroup": {"memory_max": Path("/sys/fs/cgroup/memory.max").read_text().strip(), "memory_current": Path("/sys/fs/cgroup/memory.current").read_text().strip()},
        "temporary_paths": {str(path): preflight(path, 0) for path in (Path("/tmp"), Path("/dev/shm"))},
        "compression_dedup_reflink_gate": "UNPROVEN_AND_NOT_ACCEPTED",
    }
    write_json(output / "phase12p-capabilities.json", capability)

    _, route_sha = route_identity(FULL_SCALE)
    blocked = {
        "schema_version": "phase12p-blocked-evidence-v1", "status": "BLOCKED_BEFORE_CORPUS",
        "preliminary_screening_disposition": "SCREENING_BLOCKED",
        "reason": "No writable mounted filesystem in the execution container satisfies declared 64 GiB maximum new bytes plus the required max(96 GiB, 10% capacity) reserve.",
        "safety_action": "Stopped before creating the 1,446,456,066,048-byte logical sparse store or any generated payload.",
        "thresholds": gate,
        "observed_nvme": "A 3.6 TB NVMe partition is reported by lsblk/findmnt only through file bind mounts for /etc/hosts, /etc/hostname, and /etc/resolv.conf; neither its block node nor a writable filesystem mount is exposed.",
        "mandatory_gates_not_run": ["two_clean_full_generations", "physical_backing_proof", "full_matrix", "full_restart_cleanup", "optional_uma"],
    }
    write_json(output / "phase12p-blocked.json", blocked)

    scope = {
        "schema_version": "phase12p-scope-v1", "base": "450d5d1eb35a688b7380eee6b84bd3b0837a11c2",
        "head_at_capture": git("rev-parse", "HEAD"), "nested_gitlink": git("rev-parse", "HEAD:llama.cpp"),
        "nested_checkout": git("rev-parse", "HEAD", cwd=ROOT / "llama.cpp"),
        "nested_clean": not bool(git("status", "--porcelain", cwd=ROOT / "llama.cpp")),
        "allowed_prefixes": ["scripts/phase12p/", "schemas/phase12p/", "tests/phase12p/", "results/2026-08-05/msi-edgexpert-gb10/phase12p/"],
    }
    write_json(output / "phase12p-scope.json", scope)
    raw_members = [output / name for name in ("phase12p-capabilities.json", "phase12p-blocked.json", "phase12p-scope.json")]
    archive = output / "phase12p-raw.tar"
    deterministic_tar(archive, raw_members)
    archive_identity = {"path": str(archive.relative_to(ROOT)), "size": archive.stat().st_size, "sha256": sha256_file(archive)}
    checksum_index = {
        "schema_version": "phase12p-checksum-index-v1", "self_identity": "excluded_non_circular",
        "files": [{"path": str(path.relative_to(ROOT)), "size": path.stat().st_size, "sha256": sha256_file(path)} for path in raw_members] + [archive_identity],
    }
    write_json(output / "phase12p-checksums.json", checksum_index)
    manifest = {
        "phase12p_schema_version": "phase12p-manifest-v1", "execution_profile": "STANDARD",
        "status": "blocked-review-candidate", "phase12p_final_project_head": None,
        "phase12p_final_nested_gitlink": git("rev-parse", "HEAD:llama.cpp"),
        "base_project_head": "450d5d1eb35a688b7380eee6b84bd3b0837a11c2",
        "phase8_descriptor_identity": identity(PHASE8), "phase11_manifest_identity": identity(PHASE11),
        "corpus_identity": None, "generator_version": PAYLOAD_VERSION,
        "route_schema_version": ROUTE_VERSION, "route_artifact_sha256": route_sha,
        "layout_a_definition_sha256": None, "layout_b_definition_sha256": None,
        "per_layout_physical_allocation_and_extent_proof": None,
        "best_fair_buffered_pread_cell_per_layout_and_route_class": None,
        "all_shortlisted_qd_order_cache_state_cells": [],
        "correctness_and_negative_test_disposition": "TOOLING_FIXTURES_ONLY_FULL_SCALE_BLOCKED",
        "optional_uma_disposition": "BLOCKED_CORPUS_UNAVAILABLE",
        "preliminary_screening_disposition": "SCREENING_BLOCKED",
        "limitations": [
            "No full corpus or decision-driving measurement was created.",
            "Fixture tests are tooling validation and not storage performance evidence.",
            "No storage-layout conclusion, full-model inference, native io_uring, GDS, or cross-hardware claim is made.",
        ],
        "raw_archive_uri": str(archive.relative_to(ROOT)), "raw_archive_size": archive.stat().st_size,
        "raw_archive_sha256": archive_identity["sha256"],
        "checksum_index_sha256": sha256_file(output / "phase12p-checksums.json"),
        "blocking_evidence": blocked, "self_identity": "manifest excludes its own hash and final project head remains unset until publication",
    }
    write_json(output / "phase12p-manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); manifest = capture(args.output); print(json.dumps(manifest["blocking_evidence"], sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply and record the bounded issue-102 observer-evidence cache hygiene gate."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import pathlib
import stat
from typing import Any


PROT_NONE = 0
MAP_PRIVATE = 2
MAP_FAILED = ctypes.c_void_p(-1).value
EXPECTED_CACHE_BYTES = 137728475136
EXPECTED_CACHE_SLOTS = 7849
MEMORY_STAT_KEYS = (
    "anon", "file", "kernel", "file_mapped", "file_dirty", "file_writeback",
    "inactive_file", "active_file", "workingset_refault_file",
)
VMSTAT_PRESSURE_PREFIXES = (
    "allocstall_", "pgscan_", "pgsteal_", "workingset_refault_",
)
VMSTAT_PRESSURE_KEYS = ("pswpin", "pswpout", "oom_kill")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=pathlib.Path, required=True)
    parser.add_argument("--expected-allowlist-sha256", required=True)
    parser.add_argument("--reference-preflight", type=pathlib.Path, required=True)
    parser.add_argument(
        "--success-disposition", default="READY_FOR_SINGLE_CAPTURE_004_RETRY",
    )
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
    return {
        "path": str(resolved),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def read_numeric_fields(path: pathlib.Path, multiplier: int = 1) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        try:
            result[fields[0].rstrip(":")] = int(fields[1]) * multiplier
        except ValueError:
            continue
    return result


def cgroup_root() -> pathlib.Path:
    relative = "/"
    for line in pathlib.Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            relative = line[3:] or "/"
            break
    return pathlib.Path("/sys/fs/cgroup") / relative.lstrip("/")


def read_psi(path: pathlib.Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        lines = path.read_text().splitlines()
    except (FileNotFoundError, PermissionError):
        return result
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        total = next((item for item in fields[1:] if item.startswith("total=")), None)
        if total is not None:
            result[fields[0]] = int(total.split("=", 1)[1])
    return result


def host_snapshot() -> dict[str, Any]:
    root = cgroup_root()
    meminfo = read_numeric_fields(pathlib.Path("/proc/meminfo"), 1024)
    vmstat = read_numeric_fields(pathlib.Path("/proc/vmstat"))
    memory_stat = read_numeric_fields(root / "memory.stat")
    maximum_text = (root / "memory.max").read_text().strip()
    return {
        "meminfo": {
            key: meminfo[key]
            for key in (
                "MemTotal", "MemAvailable", "MemFree", "Cached", "SReclaimable",
                "Slab", "Shmem", "Unevictable", "Mlocked", "PageTables",
                "KernelStack", "SwapTotal", "SwapFree",
            )
        },
        "cgroup": {
            "path": str(root),
            "memory_current_bytes": int((root / "memory.current").read_text()),
            "memory_max": maximum_text,
            "memory_swap_current_bytes": int((root / "memory.swap.current").read_text()),
            "memory_stat": {
                key: memory_stat.get(key, 0) for key in MEMORY_STAT_KEYS
            },
            "memory_events": read_numeric_fields(root / "memory.events"),
            "memory_pressure_total_usec": read_psi(root / "memory.pressure"),
        },
        "system_memory_pressure_total_usec": read_psi(pathlib.Path("/proc/pressure/memory")),
        "vmstat": {
            key: value
            for key, value in vmstat.items()
            if key in VMSTAT_PRESSURE_KEYS or key.startswith(VMSTAT_PRESSURE_PREFIXES)
        },
    }


def subtract(current: dict[str, int], previous: dict[str, int]) -> dict[str, int]:
    return {key: current.get(key, 0) - previous.get(key, 0) for key in set(current) | set(previous)}


def host_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_current_bytes": (
            after["cgroup"]["memory_current_bytes"] - before["cgroup"]["memory_current_bytes"]
        ),
        "memory_stat": subtract(after["cgroup"]["memory_stat"], before["cgroup"]["memory_stat"]),
        "memory_events": subtract(after["cgroup"]["memory_events"], before["cgroup"]["memory_events"]),
        "cgroup_memory_pressure_total_usec": subtract(
            after["cgroup"]["memory_pressure_total_usec"],
            before["cgroup"]["memory_pressure_total_usec"],
        ),
        "system_memory_pressure_total_usec": subtract(
            after["system_memory_pressure_total_usec"],
            before["system_memory_pressure_total_usec"],
        ),
        "vmstat": subtract(after["vmstat"], before["vmstat"]),
    }


def active_k3_processes() -> list[dict[str, Any]]:
    active = []
    self_pid = os.getpid()
    for cmdline in pathlib.Path("/proc").glob("[0-9]*/cmdline"):
        try:
            pid = int(cmdline.parent.name)
            arguments = [value.decode(errors="replace") for value in cmdline.read_bytes().split(b"\0") if value]
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
        if pid == self_pid:
            continue
        joined = " ".join(arguments)
        if (
            "issue102-exact-route-observer" in joined
            or "issue102-cross-prompt-probe" in joined
            or "run_qualification_cell.py" in joined
            or "run_stage_b_observer_campaign.py" in joined
            or "run_stage_b_observer_resume_campaign.py" in joined
            or "run_stage_c_campaign.py" in joined
        ):
            active.append({"pid": pid, "arguments": arguments})
    return active


class LinuxCacheOperations:
    def __init__(self) -> None:
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.mmap.argtypes = (
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_longlong,
        )
        self.libc.mmap.restype = ctypes.c_void_p
        self.libc.mincore.argtypes = (ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p)
        self.libc.mincore.restype = ctypes.c_int
        self.libc.munmap.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
        self.libc.munmap.restype = ctypes.c_int
        self.libc.syncfs.argtypes = (ctypes.c_int,)
        self.libc.syncfs.restype = ctypes.c_int
        self.page_size = os.sysconf("SC_PAGE_SIZE")

    def resident_bytes(self, descriptor: int, size: int) -> int:
        if size == 0:
            return 0
        address = self.libc.mmap(None, size, PROT_NONE, MAP_PRIVATE, descriptor, 0)
        if address == MAP_FAILED:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        try:
            pages = (size + self.page_size - 1) // self.page_size
            vector = (ctypes.c_ubyte * pages)()
            if self.libc.mincore(address, size, vector) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            resident_pages = sum(value & 1 for value in vector)
            return min(size, resident_pages * self.page_size)
        finally:
            if self.libc.munmap(address, size) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))

    def sync_filesystem(self, root: pathlib.Path) -> None:
        descriptor = os.open(root, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if self.libc.syncfs(descriptor) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error), str(root))
        finally:
            os.close(descriptor)


def open_verified(row: dict[str, Any]) -> tuple[int, os.stat_result]:
    path = pathlib.Path(row["canonical_path"])
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_dev != row["device"]
        or metadata.st_ino != row["inode"]
        or metadata.st_size != row["bytes"]
    ):
        os.close(descriptor)
        raise ValueError(f"allowlisted file metadata changed: {path}")
    return descriptor, metadata


def observe_files(
    operations: LinuxCacheOperations,
    files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for row in files:
        descriptor, metadata = open_verified(row)
        try:
            resident = operations.resident_bytes(descriptor, metadata.st_size)
        finally:
            os.close(descriptor)
        result.append({
            "canonical_path": row["canonical_path"],
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "bytes": metadata.st_size,
            "sha256_before_release": row["sha256"],
            "resident_bytes": resident,
        })
    return result


def apply_advice(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not hasattr(os, "posix_fadvise") or not hasattr(os, "POSIX_FADV_DONTNEED"):
        raise RuntimeError("POSIX_FADV_DONTNEED is unavailable")
    result = []
    for row in files:
        descriptor, metadata = open_verified(row)
        try:
            os.posix_fadvise(descriptor, 0, 0, os.POSIX_FADV_DONTNEED)
        finally:
            os.close(descriptor)
        result.append({
            "canonical_path": row["canonical_path"],
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "bytes": metadata.st_size,
            "advice": "POSIX_FADV_DONTNEED",
            "status": "success",
        })
    return result


def guard_projection(snapshot: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    meminfo = snapshot["meminfo"]
    cgroup = snapshot["cgroup"]
    physical = meminfo["MemTotal"]
    maximum = physical if cgroup["memory_max"] == "max" else int(cgroup["memory_max"])
    effective = min(physical, maximum)
    current = cgroup["memory_current_bytes"]
    model_cache = reference["model_file_cache_resident_bytes"]
    model_virtual = reference["model_file_virtual_bytes"]
    measured_non_pool = max(0, current - model_cache) + model_virtual
    limit_headroom = max(0, effective - measured_non_pool)
    available_headroom = min(meminfo["MemAvailable"], max(0, maximum - current))
    reserve = reference["system_reserve_bytes"] + reference["runtime_reserve_bytes"]
    bound = min(limit_headroom, available_headroom)
    unrounded_safe = max(0, bound - reserve)
    stride = EXPECTED_CACHE_BYTES // EXPECTED_CACHE_SLOTS
    safe_pool = unrounded_safe // stride * stride
    admission_unrounded = max(0, safe_pool - reference["hysteresis_bytes"])
    admission_safe = admission_unrounded // stride * stride
    return {
        "classification": "PROJECTED_FROM_LAST_ACCEPTED_MODEL_RESIDENCY",
        "physical_ram_bytes": physical,
        "cgroup_memory_max_bytes": maximum,
        "cgroup_memory_current_bytes": current,
        "memory_available_bytes": meminfo["MemAvailable"],
        "reference_model_file_cache_resident_bytes": model_cache,
        "reference_model_file_virtual_bytes": model_virtual,
        "projected_measured_non_pool_committed_bytes": measured_non_pool,
        "limit_headroom_bytes": limit_headroom,
        "available_headroom_bytes": available_headroom,
        "system_reserve_bytes": reference["system_reserve_bytes"],
        "runtime_reserve_bytes": reference["runtime_reserve_bytes"],
        "hysteresis_bytes": reference["hysteresis_bytes"],
        "safe_pool_bytes": safe_pool,
        "admission_safe_pool_bytes": admission_safe,
        "requested_pool_bytes": EXPECTED_CACHE_BYTES,
        "projected_admission_margin_bytes": admission_safe - EXPECTED_CACHE_BYTES,
        "production_guard_is_final_authority": True,
    }


def pressure_clean(before: dict[str, Any], after: dict[str, Any], delta: dict[str, Any]) -> bool:
    vmstat_clean = all(value == 0 for value in delta["vmstat"].values())
    events_clean = all(value == 0 for value in delta["memory_events"].values())
    full_clean = (
        delta["cgroup_memory_pressure_total_usec"].get("full", 0) == 0
        and delta["system_memory_pressure_total_usec"].get("full", 0) == 0
    )
    swap_clean = (
        before["meminfo"]["SwapTotal"] == 0
        and after["meminfo"]["SwapTotal"] == 0
        and before["cgroup"]["memory_swap_current_bytes"] == 0
        and after["cgroup"]["memory_swap_current_bytes"] == 0
    )
    return vmstat_clean and events_clean and full_clean and swap_clean


def main() -> int:
    args = arguments()
    allowlist_path = args.allowlist.resolve(strict=True)
    reference_path = args.reference_preflight.resolve(strict=True)
    output_path = args.output.resolve()
    if sha256(allowlist_path) != args.expected_allowlist_sha256:
        raise ValueError("observer evidence allowlist identity changed")
    allowlist_identity = identity(allowlist_path)
    reference_identity = identity(reference_path)
    generator_identity = identity(pathlib.Path(__file__))
    allowlist = json.loads(allowlist_path.read_text())
    if (
        allowlist["status"] != "frozen"
        or allowlist["disposition"] != "READY_FOR_TARGETED_HYGIENE_GATE"
        or allowlist["purpose"] != "TARGETED_OBSERVER_OUTPUT_PAGE_CACHE_RELEASE"
    ):
        raise ValueError("observer evidence allowlist is not executable")
    reference_document = json.loads(reference_path.read_text())
    reference = reference_document["preflight"]["system_memory"]
    active = active_k3_processes()
    if active:
        raise RuntimeError(f"K3/helper process is active: {active}")

    operations = LinuxCacheOperations()
    before_files = observe_files(operations, allowlist["files"])
    before_host = host_snapshot()
    before_projection = guard_projection(before_host, reference)
    operations.sync_filesystem(pathlib.Path(allowlist["evidence_root"]))
    advice = apply_advice(allowlist["files"])
    after_files = observe_files(operations, allowlist["files"])
    after_host = host_snapshot()
    after_projection = guard_projection(after_host, reference)
    delta = host_delta(before_host, after_host)
    before_resident = sum(row["resident_bytes"] for row in before_files)
    after_resident = sum(row["resident_bytes"] for row in after_files)
    gate = {
        "no_active_k3_or_helper_process": not active,
        "exact_allowlist_file_count": len(before_files) == allowlist["file_count"],
        "all_targeted_advice_succeeded": len(advice) == allowlist["file_count"],
        "all_allowlisted_pages_released": after_resident == 0,
        "resident_bytes_decreased": after_resident < before_resident,
        "projected_exact_capacity_admissible": (
            after_projection["projected_admission_margin_bytes"] >= 0
        ),
        "projected_margin_improved": (
            after_projection["projected_admission_margin_bytes"]
            > before_projection["projected_admission_margin_bytes"]
        ),
        "swap_reclaim_refault_psi_oom_cgroup_clean": pressure_clean(
            before_host, after_host, delta,
        ),
        "no_payload_reread_or_rehash_after_release": True,
    }
    status = "pass" if all(gate.values()) else "fail"
    output = {
        "schema_version": "phase13-6pg-observer-evidence-cache-hygiene-gate-v1",
        "status": status,
        "provenance": "MEASUREMENT_HYGIENE_NON_SCIENTIFIC",
        "inputs": {
            "allowlist": allowlist_identity,
            "reference_preflight": reference_identity,
            "generator": generator_identity,
        },
        "operation": {
            "syncfs_root": allowlist["evidence_root"],
            "syncfs_status": "success",
            "advice": advice,
            "model_or_runtime_file_touched": False,
        },
        "files": {
            "before": before_files,
            "after": after_files,
            "resident_bytes_before": before_resident,
            "resident_bytes_after": after_resident,
            "released_resident_bytes": before_resident - after_resident,
            "content_identity_source": "PRE_RELEASE_ALLOWLIST_SHA256",
            "content_read_after_release": False,
        },
        "host": {
            "before": before_host,
            "after": after_host,
            "delta": delta,
            "guard_projection_before": before_projection,
            "guard_projection_after": after_projection,
        },
        "gate": gate,
        "disposition": (
            args.success_disposition
            if status == "pass" else "RETURN_TO_DESIGN"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(output_path),
        "sha256": sha256(output_path),
        "status": status,
        "resident_bytes_before": before_resident,
        "resident_bytes_after": after_resident,
        "projected_margin_before": before_projection["projected_admission_margin_bytes"],
        "projected_margin_after": after_projection["projected_admission_margin_bytes"],
    }, sort_keys=True))
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

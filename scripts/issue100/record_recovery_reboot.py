#!/usr/bin/env python3
"""Record the single clean reboot authorized for issue #100 recovery."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from protocol import atomic_json, load_json


class RebootEvidenceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def scalar(path: Path) -> int | str | None:
    try:
        value = path.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return None
    try:
        return int(value)
    except ValueError:
        return value


def text_value(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except (FileNotFoundError, PermissionError):
        return None


def cgroup_root() -> Path:
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0":
            return Path("/sys/fs/cgroup") / fields[2].lstrip("/")
    raise RebootEvidenceError("unified cgroup path is unavailable")


def snapshot() -> dict:
    meminfo = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        fields = line.replace(":", "").split()
        if len(fields) >= 2 and fields[1].isdigit():
            meminfo[fields[0]] = int(fields[1])*1024
    root = cgroup_root()
    events = {}
    events_path = root / "memory.events"
    if events_path.is_file():
        for line in events_path.read_text().splitlines():
            key, value = line.split()
            events[key] = int(value)
    return {
        "timestamp": utc_now(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "uptime_seconds": float(Path("/proc/uptime").read_text().split()[0]),
        "mem_total_bytes": meminfo.get("MemTotal"),
        "mem_available_bytes": meminfo.get("MemAvailable"),
        "cached_bytes": meminfo.get("Cached"),
        "swap_total_bytes": meminfo.get("SwapTotal"),
        "swap_free_bytes": meminfo.get("SwapFree"),
        "cgroup_path": str(root),
        "cgroup_memory_current_bytes": scalar(root / "memory.current"),
        "cgroup_memory_max_bytes": scalar(root / "memory.max"),
        "cgroup_swap_current_bytes": scalar(root / "memory.swap.current"),
        "cgroup_swap_max_bytes": scalar(root / "memory.swap.max"),
        "cgroup_memory_pressure": text_value(root / "memory.pressure"),
        "cgroup_memory_events": events,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--before", action="store_true")
    group.add_argument("--after", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output.resolve()
    if args.before:
        if output.exists():
            raise RebootEvidenceError("reboot evidence already exists")
        atomic_json(output, {
            "schema_version": "issue100-recovery-reboot-v1",
            "status": "pending",
            "before": snapshot(),
        })
        print("ISSUE100_REBOOT_EVIDENCE status=pending", flush=True)
        return 0

    value = load_json(output)
    if value.get("schema_version") != "issue100-recovery-reboot-v1" or \
            value.get("status") != "pending" or not value.get("before", {}).get("boot_id"):
        raise RebootEvidenceError("pre-reboot evidence is invalid")
    after = snapshot()
    if after["boot_id"] == value["before"]["boot_id"]:
        raise RebootEvidenceError("boot identity did not change")
    value["status"] = "pass"
    value["after"] = after
    atomic_json(output, value)
    print("ISSUE100_REBOOT_EVIDENCE status=pass", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"issue100 reboot evidence: {error}", flush=True)
        raise SystemExit(1)

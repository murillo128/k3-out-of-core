#!/usr/bin/env python3
"""Capture the issue-102 host-memory inventory without mutating host state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import subprocess
from typing import Any


def read_text(path: pathlib.Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return None


def command(*args: str) -> dict[str, Any]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return {
        "argv": list(args),
        "exit_status": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def parse_kib(text: str | None) -> dict[str, int | str]:
    values: dict[str, int | str] = {}
    if text is None:
        return values
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        fields = raw.strip().split()
        if not fields:
            continue
        try:
            values[key] = int(fields[0])
        except ValueError:
            values[key] = raw.strip()
    return values


def process_inventory() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in pathlib.Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        status = parse_kib(read_text(entry / "status"))
        cmdline = read_text(entry / "cmdline")
        if cmdline is not None:
            cmdline = cmdline.replace("\0", " ").strip()
        records.append({
            "pid": int(entry.name),
            "ppid": status.get("PPid"),
            "name": status.get("Name"),
            "uid": status.get("Uid"),
            "rss_kib": status.get("VmRSS", 0),
            "hwm_kib": status.get("VmHWM", 0),
            "swap_kib": status.get("VmSwap", 0),
            "cmdline": cmdline,
            "smaps_rollup_kib": parse_kib(read_text(entry / "smaps_rollup")),
        })
    return sorted(records, key=lambda item: int(item.get("rss_kib", 0)), reverse=True)


def cgroup_root() -> pathlib.Path | None:
    text = read_text(pathlib.Path("/proc/self/cgroup")) or ""
    for line in text.splitlines():
        fields = line.split(":", 2)
        if len(fields) == 3 and fields[0] == "0" and fields[1] == "":
            return pathlib.Path("/sys/fs/cgroup") / fields[2].lstrip("/")
    return None


def cgroup_inventory(root: pathlib.Path | None) -> dict[str, str | None]:
    if root is None:
        return {}
    names = (
        "memory.current", "memory.min", "memory.low", "memory.high", "memory.max",
        "memory.swap.current", "memory.swap.max", "memory.events", "memory.stat",
        "memory.pressure", "pids.current", "pids.max",
    )
    return {name: read_text(root / name) for name in names}


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    root = cgroup_root()
    processes = process_inventory()
    keywords = (
        "k3", "llama", "issue102", "cmake", "ctest", "ninja", "make",
        "gcc", "g++", "c++", "codex",
    )
    inventory = {
        "schema_version": "issue102-host-memory-inventory-v1",
        "label": args.label,
        "capture_pid": os.getpid(),
        "boot_id": (read_text(pathlib.Path("/proc/sys/kernel/random/boot_id")) or "").strip(),
        "uptime": read_text(pathlib.Path("/proc/uptime")),
        "meminfo_kib": parse_kib(read_text(pathlib.Path("/proc/meminfo"))),
        "swap": read_text(pathlib.Path("/proc/swaps")),
        "pressure_memory": read_text(pathlib.Path("/proc/pressure/memory")),
        "cgroup_path": str(root) if root is not None else None,
        "cgroup": cgroup_inventory(root),
        "processes_by_rss": processes,
        "relevant_live_processes": [
            item for item in processes
            if any(word in str(item.get("cmdline", "")).lower() for word in keywords)
        ],
        "systemd_services": command(
            "systemctl", "--no-pager", "--plain", "--all", "--type=service", "list-units",
        ),
        "systemd_cgroup_memory": command(
            "systemd-cgtop", "-b", "-n", "1", "--depth=4", "--order=memory",
        ),
        "dev_shm_usage": command("df", "-B1", "/dev/shm"),
        "tmpfs_mounts": command("findmnt", "-J", "-t", "tmpfs"),
        "sysv_shared_memory": command("ipcs", "-m"),
        "mount_nvme0": command("findmnt", "-J", "/mnt/nvme0"),
        "mount_nvme1": command("findmnt", "-J", "/mnt/nvme1"),
        "block_devices": command("lsblk", "-J", "-o", "NAME,PATH,TYPE,SIZE,FSTYPE,MOUNTPOINTS"),
        "logged_in_users": command("who"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, args.output)
    print(json.dumps({"output": str(args.output), "sha256": sha256(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

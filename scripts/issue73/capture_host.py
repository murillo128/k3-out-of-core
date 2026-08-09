#!/usr/bin/env python3
"""Capture the decision-driving issue 73 host in machine-readable form."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

from common import file_identity, write_json


def output(command: list[str]) -> str:
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=True)
    return completed.stdout.strip()


def json_output(command: list[str]) -> object:
    return json.loads(output(command))


def swap_inventory() -> object:
    rows = []
    for line in Path("/proc/swaps").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) != 5:
            raise RuntimeError("unexpected /proc/swaps row")
        rows.append({
            "filename": fields[0], "type": fields[1],
            "size_bytes": int(fields[2]) * 1024,
            "used_bytes": int(fields[3]) * 1024, "priority": int(fields[4]),
        })
    return {"swapdevices": rows}


def read(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return None


def meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, _, value = line.partition(":")
        amount, unit = (value.strip().split() + [""])[:2]
        if amount.isdigit():
            result[key] = int(amount) * (1024 if unit == "kB" else 1)
    return result


def os_release() -> dict[str, str]:
    result = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator:
            result[key] = value.strip('"')
    return result


def gpu_inventory() -> list[dict[str, object]]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,uuid,pci.bus_id,name,memory.total,driver_version,compute_cap",
        "--format=csv,noheader,nounits",
    ]
    rows = []
    for line in output(command).splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 7:
            raise RuntimeError("unexpected nvidia-smi inventory row")
        rows.append({
            "cuda_ordinal": int(fields[0]), "uuid": fields[1], "pci_bdf": fields[2],
            "name": fields[3], "memory_total_mib": int(fields[4]),
            "driver_version": fields[5], "compute_capability": fields[6],
        })
    return rows


def block_limits(device: str) -> dict[str, object]:
    root = Path("/sys/block") / device
    fields = (
        "logical_block_size", "physical_block_size", "max_sectors_kb",
        "max_segment_size", "max_segments", "nr_requests", "read_ahead_kb",
        "scheduler", "rotational", "minimum_io_size", "optimal_io_size",
    )
    result = {name: read(root / "queue" / name) for name in fields}
    result.update({"model": read(root / "device/model"), "vendor": read(root / "device/vendor")})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--topology", type=Path, required=True)
    parser.add_argument("--project-revision", required=True)
    parser.add_argument("--nested-revision", required=True)
    parser.add_argument("--data-path", type=Path, default=Path("/var/lib/k3/issue73"))
    args = parser.parse_args()
    if not args.topology.is_file() or not args.data_path.is_dir():
        raise SystemExit("topology or data path is missing")

    filesystem = os.statvfs(args.data_path)
    tracefs = Path("/sys/kernel/tracing")
    document = {
        "schema_version": "issue73-host-v1", "status": "pass",
        "contract": {
            "provider_product": "Akamai/Linode GPU VM, RTX4000 Ada x4 Medium",
            "storage_claim_limit": "guest-visible ext4 on virtio-SCSI /dev/sda; physical media unspecified",
        },
        "revisions": {"project": args.project_revision, "nested": args.nested_revision},
        "system": {
            "platform": platform.platform(), "uname": list(platform.uname()),
            "os_release": os_release(), "lscpu": json_output(["lscpu", "-J"]),
            "online_numa_nodes": read(Path("/sys/devices/system/node/online")),
            "meminfo_bytes": meminfo(),
        },
        "gpus": gpu_inventory(),
        "nvidia_topology": output(["nvidia-smi", "topo", "-m"]),
        "measured_topology": file_identity(args.topology),
        "storage": {
            "lsblk": json_output([
                "lsblk", "-J", "-b", "-o",
                "NAME,KNAME,TYPE,SIZE,ROTA,TRAN,MODEL,LOG-SEC,PHY-SEC,MOUNTPOINTS,FSTYPE",
            ]),
            "findmnt": json_output(["findmnt", "-J", "-T", str(args.data_path)]),
            "sda_limits": block_limits("sda"),
            "nvme_device_nodes": sorted(str(path) for path in Path("/dev").glob("nvme*")),
            "data_path": str(args.data_path),
            "capacity_bytes": filesystem.f_blocks * filesystem.f_frsize,
            "available_bytes_at_capture": filesystem.f_bavail * filesystem.f_frsize,
        },
        "swap": {
            "proc_swaps": Path("/proc/swaps").read_text(),
            "swapon": swap_inventory(),
        },
        "capabilities": {
            "perf_event_paranoid": read(Path("/proc/sys/kernel/perf_event_paranoid")),
            "tracefs_mounted": tracefs.is_mount(),
            "tracefs_path": str(tracefs),
            "io_uring_disabled": read(Path("/proc/sys/kernel/io_uring_disabled")),
        },
        "toolchain": {
            "cmake": output(["cmake", "--version"]).splitlines()[0],
            "compiler": output(["c++", "--version"]).splitlines()[0],
            "nvcc": output(["/usr/local/cuda/bin/nvcc", "--version"]),
            "perf": output(["perf", "--version"]),
            "nsys": output(["nsys", "--version"]),
            "perfetto": output(["/opt/perfetto-v50.1/linux-amd64/perfetto", "--version"]),
            "trace_processor": output([
                "/opt/perfetto-v50.1/linux-amd64/trace_processor_shell", "--version"]),
            "nccl_packages": output([
                "dpkg-query", "-W", "-f=${Package} ${Version}\\n", "libnccl2", "libnccl-dev"]),
        },
    }
    write_json(args.output, document)
    print(f"ISSUE73_HOST_CAPTURE status=pass gpus={len(document['gpus'])} output={args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Capture the bounded DenseIO/NVMe capability record required by issue #58."""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import urllib.request
from pathlib import Path


def command(arguments: list[str]) -> dict[str, object]:
    completed = subprocess.run(arguments, text=True, capture_output=True)
    return {
        "command": arguments,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def json_command(arguments: list[str]) -> dict[str, object]:
    result = command(arguments)
    if result["returncode"] == 0:
        try:
            result["parsed"] = json.loads(str(result["stdout"]))
        except json.JSONDecodeError:
            result["parse_error"] = True
    return result


def read_optional(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def instance_shape() -> dict[str, object]:
    request = urllib.request.Request(
        "http://169.254.169.254/opc/v2/instance/",
        headers={"Authorization": "Bearer Oracle"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            instance = json.load(response)
        config = instance.get("shapeConfig", {})
        return {
            "status": "AVAILABLE",
            "shape": instance.get("shape"),
            "ocpus": config.get("ocpus"),
            "memory_in_gbs": config.get("memoryInGBs"),
            "networking_bandwidth_in_gbps": config.get("networkingBandwidthInGbps"),
        }
    except Exception as error:  # pragma: no cover - host capability path
        return {"status": "UNAVAILABLE", "error": str(error)}


def filesystem(path: Path) -> dict[str, object]:
    stats = os.statvfs(path)
    return {
        "path": str(path),
        "capacity_bytes": stats.f_blocks * stats.f_frsize,
        "available_bytes": stats.f_bavail * stats.f_frsize,
        "available_inodes": stats.f_favail,
        "findmnt": json_command([
            "findmnt", "--json", "--bytes", "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,AVAIL,USE%", "--target", str(path),
        ]),
        "xfs_info": command(["xfs_info", str(path)]),
        "quota": command(["xfs_quota", "-x", "-c", "state", str(path)]),
    }


def block_queue(device: str) -> dict[str, object]:
    root = Path("/sys/block") / device
    fields = (
        "scheduler", "nr_requests", "read_ahead_kb", "max_sectors_kb",
        "max_hw_sectors_kb", "logical_block_size", "physical_block_size",
        "minimum_io_size", "optimal_io_size", "rotational",
    )
    return {
        "device": device,
        "sysfs_device": os.path.realpath(root / "device"),
        "queue": {field: read_optional(root / "queue" / field) for field in fields},
    }


def fio_probe(path: Path, *, ioengine: str, direct: bool) -> dict[str, object]:
    arguments = [
        "fio", "--name=phase12_nvme_preflight", f"--filename={path}",
        "--readonly", "--offset=4096", "--size=64m", "--rw=read", "--bs=1m",
        f"--ioengine={ioengine}", f"--direct={int(direct)}",
        f"--iodepth={1 if ioengine == 'psync' else 2}",
        "--numjobs=1", "--output-format=json",
    ]
    result = json_command(arguments)
    parsed = result.get("parsed", {})
    jobs = parsed.get("jobs", []) if isinstance(parsed, dict) else []
    job = jobs[0] if jobs else {}
    read = job.get("read", {}) if isinstance(job, dict) else {}
    result["summary"] = {
        "error": job.get("error") if isinstance(job, dict) else None,
        "io_bytes": read.get("io_bytes") if isinstance(read, dict) else None,
        "short_ios": read.get("short_ios") if isinstance(read, dict) else None,
        "drop_ios": read.get("drop_ios") if isinstance(read, dict) else None,
    }
    result.pop("parsed", None)
    result["stdout"] = "omitted; parsed summary retained"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = args.corpus.resolve()
    source = corpus / "layout-b/contiguous-experts.bin"
    if not source.is_file():
        raise ValueError(f"missing measured physical store: {source}")
    probes = {
        "buffered_positional_pread": fio_probe(source, ioengine="psync", direct=False),
        "direct_positional_pread": fio_probe(source, ioengine="psync", direct=True),
        "buffered_native_io_uring": fio_probe(source, ioengine="io_uring", direct=False),
        "direct_native_io_uring": fio_probe(source, ioengine="io_uring", direct=True),
    }
    probe_pass = all(
        probe["returncode"] == 0
        and probe["summary"]["error"] == 0
        and probe["summary"]["io_bytes"] == 64 * 1024 * 1024
        and probe["summary"]["short_ios"] == 0
        and probe["summary"]["drop_ios"] == 0
        for probe in probes.values()
    )
    shape = instance_shape()
    document = {
        "schema_version": "phase12-nvme-host-preflight-v1",
        "status": "PASS" if probe_pass else "FAIL",
        "project_revision": command(["git", "rev-parse", "HEAD"])["stdout"],
        "nested_revision": command(["git", "-C", "llama.cpp", "rev-parse", "HEAD"])["stdout"],
        "hostname": platform.node(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "oci_instance": shape,
        "contract_deviation": {
            "status": "RECORDED_LIMITATION",
            "nominal": "VM.DenseIO.E4.Flex, 16 OCPU, 256 GB, Ubuntu 24.04",
            "observed": f"{shape.get('shape')}, {shape.get('ocpus')} OCPU, {shape.get('memory_in_gbs')} GB, Oracle Linux 9.8",
            "impact": "same-host two-independent-NVMe campaign is valid; nominal shape/OS/RAM portability remains a handoff limitation",
        },
        "os_release": read_optional(Path("/etc/os-release")),
        "cpu": json_command(["lscpu", "--json"]),
        "numa": command(["numactl", "--hardware"]),
        "memory": {
            key: int(value.split()[0]) * 1024
            for key, value in (
                line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()
            )
            if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
        },
        "swap": command(["swapon", "--show", "--bytes"]),
        "cgroup": {
            "membership": read_optional(Path("/proc/self/cgroup")),
            "memory_max": read_optional(Path("/sys/fs/cgroup/memory.max")),
            "swap_max": read_optional(Path("/sys/fs/cgroup/memory.swap.max")),
            "cpu_max": read_optional(Path("/sys/fs/cgroup/cpu.max")),
            "pids_max": read_optional(Path("/sys/fs/cgroup/pids.max")),
        },
        "filesystems": [filesystem(Path("/mnt/nvme0")), filesystem(Path("/mnt/nvme1"))],
        "block_devices": json_command([
            "lsblk", "--json", "--bytes", "--output",
            "NAME,KNAME,PATH,TYPE,SIZE,MODEL,SERIAL,REV,FSTYPE,MOUNTPOINTS,ROTA,LOG-SEC,PHY-SEC",
        ]),
        "nvme": {
            "list": json_command(["nvme", "list", "--output-format=json"]),
            "controllers": [
                json_command(["nvme", "id-ctrl", f"/dev/{device}", "--output-format=json"])
                for device in ("nvme0", "nvme1")
            ],
            "pci": command(["lspci", "-D", "-nn"]),
            "queues": [block_queue("nvme0n1"), block_queue("nvme1n1")],
        },
        "capability_probes": probes,
        "measured_store": str(source),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": document["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if document["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture or verify bounded Phase 11 Checkpoint A GB10 evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def run(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in read(Path("/proc/meminfo")).splitlines():
        name, value = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            values[name] = int(value.strip().split()[0]) * 1024
    return values


def gitlink(project_head: str) -> str:
    fields = run(["git", "-C", str(ROOT), "ls-tree", project_head, "--", "llama.cpp"]).split()
    if len(fields) < 3:
        raise ValueError("project revision has no llama.cpp gitlink")
    return fields[2]


def validate(document: dict[str, Any]) -> None:
    required = {"schema_version", "status", "scope", "revisions", "platform", "gpu_inventory", "toolkit",
        "storage_inventory", "filesystem", "ats_evidence", "c2c_evidence", "memlock_limit_bytes", "probe", "commands"}
    if set(document) != required or document["schema_version"] != "phase11-capabilities-v1" or \
            document["status"] != "pass" or document["scope"] != "gb10_coherent_uma_buffered_storage_fallback":
        raise ValueError("unsupported Phase 11 capability document")
    probe = document["probe"]
    if document["platform"]["board"] != "EdgeXpert (MS-C931)" or \
            document["platform"]["product"] != "MS-C931":
        raise ValueError("unsupported GB10 board identity")
    if probe["gpu"] != "NVIDIA GB10" or probe["compute_capability"] != "12.1" or \
            probe["device_count"] != 1 or probe["status"] != "pass":
        raise ValueError("GB10 coherent probe did not pass")
    if probe["native_io_uring"] != "unavailable_host_seccomp" or probe["io_uring_errno"] != 1 or \
            probe["memlock_limit_bytes"] != 8 * 1024 * 1024 or \
            probe["registered_large_buffer_evidence"] != "unavailable":
        raise ValueError("amended host capability record mismatch")
    if probe["storage_transport"] != "buffered_pread" or \
            probe["pageable_memory_access"] != 1 or probe["pageable_uses_host_page_tables"] != 1:
        raise ValueError("final-allocation coherence/fallback proof is absent")
    if document["revisions"]["gitlink"] != document["revisions"]["nested_head"]:
        raise ValueError("project/nested gitlink mismatch")
    if document["memlock_limit_bytes"] != 8 * 1024 * 1024 or len(document["commands"]) < 5:
        raise ValueError("capability evidence is incomplete")


def capture(probe_path: Path, project_head: str, nested_head: str) -> dict[str, Any]:
    actual_project = run(["git", "-C", str(ROOT), "rev-parse", f"{project_head}^{{commit}}"])
    actual_nested = run(["git", "-C", str(ROOT / "llama.cpp"), "rev-parse", f"{nested_head}^{{commit}}"])
    if actual_project != project_head or actual_nested != nested_head:
        raise ValueError("evidence revisions must be full exact commit IDs")
    probe_run = subprocess.run([str(probe_path.resolve())], check=True, capture_output=True, text=True)
    probe_lines = [line for line in probe_run.stdout.splitlines() if line.startswith("{")]
    if len(probe_lines) != 1:
        raise ValueError("native probe did not emit exactly one JSON document")
    probe = json.loads(probe_lines[0])
    memory = meminfo()
    filesystem = json.loads(run(["findmnt", "-J", "-T", str(ROOT)]))["filesystems"][0]
    filesystem["options"] = [option for option in filesystem["options"].split(",")
        if "=" not in option]
    storage = json.loads(run(["lsblk", "-dn", "-J", "-o", "NAME,MODEL,SERIAL,SIZE,ROTA,TRAN"]))
    storage["blockdevices"] = [device for device in storage["blockdevices"] if device["tran"] == "nvme"]
    for device in storage["blockdevices"]:
        controller = device["name"].rsplit("n", 1)[0]
        firmware_path = Path("/sys/class/nvme") / controller / "firmware_rev"
        device["firmware"] = read(firmware_path) if firmware_path.exists() else "unavailable"
    document = {
        "schema_version": "phase11-capabilities-v1",
        "status": "pass",
        "scope": "gb10_coherent_uma_buffered_storage_fallback",
        "revisions": {"project_head": project_head, "nested_head": nested_head,
            "gitlink": gitlink(project_head)},
        "platform": {
            "board": read(Path("/sys/class/dmi/id/board_name")),
            "product": read(Path("/sys/class/dmi/id/product_name")),
            "architecture": platform.machine(), "kernel": platform.release(),
            "cpu_count": int(run(["nproc"])), "mem_total_bytes": memory["MemTotal"],
            "mem_available_bytes": memory["MemAvailable"],
            "cgroup_memory_max_bytes": int(read(Path("/sys/fs/cgroup/memory.max"))),
            "cgroup_memory_current_bytes": int(read(Path("/sys/fs/cgroup/memory.current"))),
            "swap_total_bytes": memory["SwapTotal"],
            "seccomp_mode": int(next(line.split(":", 1)[1] for line in read(Path("/proc/self/status")).splitlines()
                if line.startswith("Seccomp:"))),
        },
        "gpu_inventory": run(["nvidia-smi", "--query-gpu=name,uuid,pci.bus_id,compute_cap,driver_version",
            "--format=csv,noheader,nounits"]),
        "toolkit": run(["nvcc", "--version"]).splitlines()[-1],
        "storage_inventory": storage,
        "filesystem": filesystem,
        "ats_evidence": "arm-smmu-v3 PMCG pcie_ats_trans_* events exposed",
        "c2c_evidence": "NVIDIA GB10 coherent CPU/GPU final-address probe",
        "memlock_limit_bytes": resource.getrlimit(resource.RLIMIT_MEMLOCK)[0],
        "probe": probe,
        "commands": [
            "cmake --build build-phase11-a-cpu --target test-expert-uma -j20",
            "ctest --test-dir build-phase11-a-cpu -R ^test-expert-uma$ --output-on-failure",
            "cmake --build build-phase11-a-cuda --target test-expert-uma phase11-uma-probe -j20",
            "ctest --test-dir build-phase11-a-cuda -R ^test-expert-uma$ --output-on-failure",
            "./build-phase11-a-cuda/bin/phase11-uma-probe",
        ],
    }
    validate(document)
    return document


def canonical(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--project-head")
    parser.add_argument("--nested-head")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        document = json.loads(args.verify.read_text(encoding="utf-8"))
        validate(document)
        print(f"{args.verify} {hashlib.sha256(canonical(document)).hexdigest()}")
        return 0
    if not all((args.probe, args.project_head, args.nested_head, args.output)):
        parser.error("capture requires --probe, --project-head, --nested-head, and --output")
    document = capture(args.probe, args.project_head, args.nested_head)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical(document))
    print(f"{args.output} {hashlib.sha256(canonical(document)).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

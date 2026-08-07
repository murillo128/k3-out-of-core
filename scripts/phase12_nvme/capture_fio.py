#!/usr/bin/env python3
"""Capture bounded fio/NVMe device-ceiling characterization for issue #58."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

QDS = (1, 2, 4, 8, 16, 32)
USEFUL_BYTES = 25_829_572_608
BUNDLE_BYTES = 17_547_264


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run_case(source: Path, raw: Path, workload: str, qd: int) -> dict[str, object]:
    output = raw / f"{workload}__qd-{qd}.json"
    bs = "1m" if workload == "large-sequential" else str(BUNDLE_BYTES)
    rw = "read" if workload == "large-sequential" else "randread"
    command = [
        "fio", "--name=phase12_nvme_device_ceiling", f"--filename={source}",
        "--readonly", "--offset=4096", f"--size={USEFUL_BYTES}", f"--rw={rw}",
        f"--bs={bs}", "--ioengine=io_uring", "--direct=1", f"--iodepth={qd}",
        "--numjobs=1", "--group_reporting=1", "--randrepeat=1", "--randseed=2608",
        "--random_generator=tausworthe64", "--output-format=json", f"--output={output}",
    ]
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"fio failed: {command}: {completed.stderr}")
    document = json.loads(output.read_text())
    job = document["jobs"][0]
    read = job["read"]
    if int(job["error"]) or int(read["io_bytes"]) != USEFUL_BYTES or int(read["short_ios"]) or int(read["drop_ios"]):
        raise ValueError(f"fio correctness failure for {workload} QD{qd}")
    result = {
        "workload": workload,
        "requested_qd": qd,
        "io_bytes": int(read["io_bytes"]),
        "bw_bytes": int(read["bw_bytes"]),
        "iops": float(read["iops"]),
        "runtime_ms": int(read["runtime"]),
        "submission_latency_ns": read["slat_ns"],
        "completion_latency_ns": read["clat_ns"],
        "latency_ns": read["lat_ns"],
        "cpu": {"user_percent": job["usr_cpu"], "system_percent": job["sys_cpu"], "context_switches": job["ctx"]},
        "disk_util": document.get("disk_util", []),
        "raw": {"path": str(output), "size": output.stat().st_size, "sha256": sha256_file(output)},
        "command": command,
    }
    print(f"fio {workload} QD{qd}: {result['bw_bytes'] / 1e9:.3f} GB/s", flush=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()
    source = (args.corpus.resolve() / "layout-b/contiguous-experts.bin")
    raw = args.raw_output.resolve()
    raw.mkdir(parents=True, exist_ok=True)
    cases = [run_case(source, raw, workload, qd) for workload in ("large-sequential", "expert-offset-distributed") for qd in QDS]
    document = {
        "schema_version": "phase12-nvme-fio-characterization-v1",
        "status": "PASS",
        "source": str(source),
        "source_logical_size": source.stat().st_size,
        "fio_version": subprocess.check_output(["fio", "--version"], text=True).strip(),
        "engine": "native io_uring with O_DIRECT; no buffered fallback",
        "queue_depths": list(QDS),
        "cases": cases,
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "PASS", "cases": len(cases)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

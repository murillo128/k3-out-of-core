#!/usr/bin/env python3
"""Create bounded, path-sanitized Checkpoint B evidence from local captures."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from common import file_identity, sha256, write_json


def identity_digest(document: dict[str, Any]) -> str:
    selected = {key: document.get(key) for key in
        ("prompt_ids", "generated_ids", "generated_text", "logits_fnv64", "routes")}
    encoded = json.dumps(selected, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def sanitize(value: Any, replacements: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for source, destination in replacements:
            value = value.replace(source, destination)
        return value
    if isinstance(value, list):
        return [sanitize(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, replacements) for key, item in value.items()}
    return value


def workload_summary(path: Path, reference: Path | None) -> dict[str, Any]:
    document = json.loads(path.read_text())
    result: dict[str, Any] = {
        "schema_version": "phase12-5-workload-summary-v1",
        "status": document.get("status"),
        "mode": document.get("mode"),
        "identity_sha256": identity_digest(document),
        "prompt_ids_sha256": hashlib.sha256(json.dumps(document.get("prompt_ids"),
            separators=(",", ":")).encode()).hexdigest(),
        "generated_ids": document.get("generated_ids"),
        "generated_text_sha256": hashlib.sha256(str(document.get("generated_text", "")).encode()).hexdigest(),
        "logits_fnv64": document.get("logits_fnv64"),
        "route_records": len(document.get("routes", [])),
        "latency_us": document.get("latency_us"),
        "capacities": document.get("capacities"),
        "mechanism": document.get("mechanism"),
        "storage": document.get("storage"),
        "lifecycle": document.get("lifecycle"),
    }
    if reference is not None:
        baseline = json.loads(reference.read_text())
        result["reference"] = file_identity(reference)
        result["reference_identity_sha256"] = identity_digest(baseline)
        result["exact_identity_match"] = result["identity_sha256"] == result["reference_identity_sha256"]
        if not result["exact_identity_match"]:
            raise ValueError(f"workload identity differs from {reference}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--case-name", required=True)
    parser.add_argument("--reference-workload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = args.output / args.case_name
    if output.exists():
        raise FileExistsError(output)
    replacements = sorted((
        (str(args.capture_root), "<CAPTURE_ROOT>"),
        ("/workspace/k3-out-of-core", "<PROJECT_ROOT>"),
        ("/workspace/builds/k3-issue54-on", "<TRACE_BUILD>"),
        ("/workspace/builds/k3-issue54-tools", "<PERFETTO_TOOLS>"),
        ("/workspace/models/DeepSeek-V4-Flash-85ce4196-UD-Q3_K_XL", "<DEEPSEEK_ARTIFACT_ROOT>"),
        ("/dev/shm/k3-issue54-tiny", "<TINY_ARTIFACT_ROOT>"),
    ), key=lambda item: len(item[0]), reverse=True)
    inputs = {
        "capture": args.capture_root / "capture.json",
        "verification": args.capture_root / "verification.json",
        "query-output": args.capture_root / "query-output.json",
    }
    output.mkdir(parents=True)
    identities = {}
    for label, path in inputs.items():
        document = sanitize(json.loads(path.read_text()), replacements)
        destination = output / f"{label}.json"
        write_json(destination, document)
        identities[label] = file_identity(destination)
    summary = sanitize(workload_summary(args.capture_root / "workload.json", args.reference_workload), replacements)
    write_json(output / "workload-summary.json", summary)
    identities["workload-summary"] = file_identity(output / "workload-summary.json")
    index = {
        "schema_version": "phase12-5-checkpoint-b-case-v1",
        "status": "valid",
        "case_name": args.case_name,
        "local_raw_trace": sanitize(file_identity(next(args.capture_root.glob("*.pftrace"))), replacements),
        "files": identities,
    }
    write_json(output / "index.json", index)
    print(json.dumps({"status": "valid", "case": args.case_name,
        "identity_sha256": summary["identity_sha256"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

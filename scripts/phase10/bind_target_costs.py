#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from prefetch_common import Phase10Error, canonical_bytes, load_json, validate_profile, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def bind(profile: dict[str, Any], paths: list[Path]) -> dict[str, Any]:
    validate_profile(profile)
    target = profile["target"]
    costs = {}
    measurements = []
    for path in paths:
        document = load_json(path)
        provenance = document.get("path_provenance", {})
        if document.get("schema_version") != "phase10-transport-break-even-v1" or \
                document.get("status") != "pass" or provenance.get("exact_runtime_provider_path") is not True:
            raise Phase10Error("target cost input is not eligible exact-runtime evidence")
        measured_package = provenance.get("package_sha256")
        if measured_package is None and len(target["files"]) == 1 and \
                provenance.get("model_sha256") == target["files"][0]["sha256"] and \
                provenance.get("model_size") == target["files"][0]["size"]:
            measured_package = target["package_sha256"]
        if measured_package != target["package_sha256"]:
            raise Phase10Error("target cost input describes a different package")
        measurements.append({"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path),
            "project_head": document["project_head"], "nested_head": document["nested_head"],
            "profile_sha256": document["profile_sha256"],
            "storage_map_sha256": provenance["storage_map_sha256"]})
        for envelope in document["envelopes"]:
            if envelope["eligible"]:
                record = envelope["profile_record"]
                key = (record["transport"], record["readiness"])
                if key in costs and costs[key] != record:
                    raise Phase10Error("target cost inputs disagree on an envelope")
                costs[key] = record
    if not costs:
        raise Phase10Error("target cost inputs contain no eligible envelope")
    return {"schema_version": "phase10-target-costs-v1", "status": "pass",
        "target": {"package_sha256": target["package_sha256"], "files": target["files"],
            "tensor_layout_sha256": target["tensor_layout_sha256"],
            "expert_bytes_sha256": hashlib.sha256(canonical_bytes(target["expert_bytes"])).hexdigest()},
        "measurements": measurements,
        "costs": [costs[key] for key in sorted(costs)]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bind measured runtime cost envelopes to an exact target package")
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--measurement", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        write_json(args.output, bind(load_json(args.profile), [path.resolve() for path in args.measurement]))
        print(args.output)
        return 0
    except (OSError, KeyError, TypeError, ValueError, Phase10Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

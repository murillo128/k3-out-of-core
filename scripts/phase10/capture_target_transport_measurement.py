#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from capture_transport_measurements import validate_measurement
from prefetch_common import Phase10Error, load_json, require_capture_heads, validate_profile, write_json


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one exact target-package transport measurement")
    parser.add_argument("--probe", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--storage-map", type=Path, required=True)
    parser.add_argument("--project-head", required=True)
    parser.add_argument("--nested-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        require_capture_heads(args.project_head, args.nested_head)
        profile = load_json(args.profile)
        validate_profile(profile)
        identity = f"{args.project_head}:{args.nested_head}"
        command = [str(args.probe.resolve()), "--profile", str(args.profile.resolve()),
            "--model", str(args.model.resolve()), "--identity", identity,
            "--storage-map", str(args.storage_map.resolve())]
        completed = subprocess.run(command, check=False, capture_output=True)
        if completed.returncode != 0:
            tail = completed.stderr.decode(errors="replace").splitlines()[-24:]
            raise Phase10Error(f"target transport probe failed ({completed.returncode}):\n" + "\n".join(tail))
        document = json.loads(completed.stdout)
        validate_measurement(document, args.project_head, args.nested_head,
            sha256_file(args.profile), sha256_file(args.storage_map))
        if document["path_provenance"].get("package_sha256") != profile["target"]["package_sha256"]:
            raise Phase10Error("target transport measurement package identity mismatch")
        write_json(args.output, document)
        print(args.output)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, Phase10Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

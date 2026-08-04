#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from prefetch_common import Phase10Error, load_json, write_json


def combine(f16_path: str, mxfp4_path: str) -> dict:
    artifacts = {"f16": load_json(f16_path), "mxfp4": load_json(mxfp4_path)}
    identities = {(document["project_head"], document["nested_head"], document["host"])
        for document in artifacts.values()}
    if len(identities) != 1:
        raise Phase10Error("transport evidence identities differ")
    for document in artifacts.values():
        if document["schema_version"] != "phase10-transport-break-even-v1" or \
                document["formula"] != "waste/(hidden+waste)" or \
                document["waste_external_threshold_transferred"] is not False:
            raise Phase10Error("invalid transport break-even input")
    project_head, nested_head, host = identities.pop()
    return {"schema_version": "phase10-transport-break-even-matrix-v1", "status": "pass" if all(
        document["status"] == "pass" for document in artifacts.values()) else "fail",
        "project_head": project_head, "nested_head": nested_head, "host": host,
        "formula": "waste/(hidden+waste)", "conservative_basis":
            "p50 useful lower envelope; p95 predictor/service/delay/displacement waste upper envelope",
        "waste_external_threshold_transferred": False, "artifacts": artifacts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--f16", required=True)
    parser.add_argument("--mxfp4", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = combine(args.f16, args.mxfp4)
        write_json(args.output, result)
        print(Path(args.output))
        return 0 if result["status"] == "pass" else 2
    except (OSError, Phase10Error, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())

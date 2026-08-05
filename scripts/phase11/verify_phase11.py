#!/usr/bin/env python3
"""Verify the Phase 11 closeout without external Python dependencies."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT/"results/2026-08-04/msi-edgexpert-gb10/phase11-uma"
def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024*1024), b""): digest.update(chunk)
    return digest.hexdigest()
def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--manifest", type=Path, default=RESULTS/"phase11-manifest.json")
    args = parser.parse_args(); document = json.loads(args.manifest.read_text())
    spec = importlib.util.spec_from_file_location("build_phase11_closeout", ROOT/"scripts/phase11/build_phase11_closeout.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module); module.validate_manifest(document)
    for item in document["evidence"] + document["closeout_artifacts"]:
        path = ROOT/item["path"]
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]: raise ValueError(f"identity drift: {item['path']}")
    index = json.loads((RESULTS/"phase11-checksums.json").read_text())
    for item in index["files"]:
        path = ROOT/item["path"]
        if path.stat().st_size != item["size"] or sha256(path) != item["sha256"]: raise ValueError(f"index drift: {item['path']}")
    print("phase11 closeout verified"); return 0
if __name__ == "__main__": raise SystemExit(main())

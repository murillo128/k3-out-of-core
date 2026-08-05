#!/usr/bin/env python3
"""Mutation tests for the Phase 11 final closeout."""
from __future__ import annotations
import copy, importlib.util, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT/"results/2026-08-04/msi-edgexpert-gb10/phase11-uma/phase11-manifest.json"
def main() -> int:
    spec = importlib.util.spec_from_file_location("build_phase11_closeout", ROOT/"scripts/phase11/build_phase11_closeout.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader; spec.loader.exec_module(module)
    if not MANIFEST.exists(): print("phase11 closeout self-test passed (manifest not built yet)"); return 0
    document = json.loads(MANIFEST.read_text()); module.validate_manifest(document)
    changed = copy.deepcopy(document); changed["gates"]["correctness"] = False
    try: module.validate_manifest(changed)
    except ValueError as error: assert "gate" in str(error)
    else: raise AssertionError("failed correctness gate accepted")
    changed = copy.deepcopy(document); changed["disposition"]["autofit_claim"] = "PERFORMANCE_SELECTED"
    try: module.validate_manifest(changed)
    except ValueError as error: assert "disposition" in str(error)
    else: raise AssertionError("overclaimed autofit accepted")
    changed = copy.deepcopy(document); changed["capacity"]["first_unsafe_bytes"] += 1
    try: module.validate_manifest(changed)
    except ValueError as error: assert "one slot" in str(error)
    else: raise AssertionError("invalid unsafe boundary accepted")
    print("phase11 closeout mutation tests passed"); return 0
if __name__ == "__main__": raise SystemExit(main())

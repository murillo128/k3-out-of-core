#!/usr/bin/env python3
"""Verifier tests for Phase 11 Checkpoint D evidence."""
from __future__ import annotations
import copy, importlib.util, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT/"scripts/phase11/capture_checkpoint_d.py"
EVIDENCE = ROOT/"results/2026-08-04/msi-edgexpert-gb10/phase11-uma/phase11-checkpoint-d.json"
def main() -> int:
    spec = importlib.util.spec_from_file_location("capture_checkpoint_d", SCRIPT); module = importlib.util.module_from_spec(spec)
    assert spec.loader; spec.loader.exec_module(module)
    if not EVIDENCE.exists(): print("phase11 checkpoint D verifier self-test passed (evidence not captured yet)"); return 0
    document = json.loads(EVIDENCE.read_text()); module.validate(document)
    changed = copy.deepcopy(document); changed["tiny_matrix"]["f16"]["uma"]["w"][0]["diagnostics"]["ring_h2d_bytes"] = 1
    try: module.validate(changed)
    except ValueError as error: assert "safety/resource" in str(error)
    else: raise AssertionError("unsafe H2D evidence passed")
    changed = copy.deepcopy(document); changed["lifecycle"]["max_return_delta_bytes"] = changed["lifecycle"]["return_threshold_bytes"] + 1
    try: module.validate(changed)
    except ValueError as error: assert "25-cycle" in str(error)
    else: raise AssertionError("leaking lifecycle evidence passed")
    changed = copy.deepcopy(document); changed["statistics"]["mxfp4"]["autofit_vs_best_explicit"]["estimate"] = .1
    module.validate(changed)
    changed = copy.deepcopy(document); changed["structural_equivalence"]["mxfp4"]["equivalent"] = False
    try: module.validate(changed)
    except ValueError as error: assert "structural equivalence" in str(error)
    else: raise AssertionError("structurally divergent autofit evidence passed")
    print("phase11 checkpoint D evidence verifier passed"); return 0
if __name__ == "__main__": raise SystemExit(main())

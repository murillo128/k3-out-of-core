#!/usr/bin/env python3
"""Verifier tests for the Phase 11 Checkpoint C evidence contract."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase11/capture_checkpoint_c.py"
EVIDENCE = ROOT / "results/2026-08-04/msi-edgexpert-gb10/phase11-uma/phase11-checkpoint-c.json"


def load_module():
    spec = importlib.util.spec_from_file_location("capture_checkpoint_c", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def expect_failure(module, document, fragment: str) -> None:
    try:
        module.validate(document)
    except ValueError as error:
        assert fragment in str(error)
    else:
        raise AssertionError("invalid evidence unexpectedly passed")


def main() -> int:
    module = load_module()
    if not EVIDENCE.exists():
        print("phase11 checkpoint C verifier self-test passed (evidence not captured yet)")
        return 0
    document = json.loads(EVIDENCE.read_text())
    module.validate(document)

    changed = copy.deepcopy(document)
    changed["cases"]["f16"]["autofit_uma"]["diagnostics"]["uma_degraded_hits"] = 1
    expect_failure(module, changed, "no-copy/pressure/residency")

    changed = copy.deepcopy(document)
    changed["cases"]["mxfp4"]["explicit_uma"]["diagnostics"]["hot_policy"] = 1
    expect_failure(module, changed, "defaults drifted")

    changed = copy.deepcopy(document)
    changed["sanitizers"]["compute_sanitizer"]["error_summary"] = 1
    expect_failure(module, changed, "sanitizer evidence")

    changed = copy.deepcopy(document)
    changed["policy_pressure_lifecycle"]["before_io"] = 0
    expect_failure(module, changed, "policy/pressure/trim/surrender")

    print("phase11 checkpoint C evidence verifier passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

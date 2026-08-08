#!/usr/bin/env python3
"""Unit/integration checks for issue 65's bounded MAX_SAFE workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


MODULE_PATH = Path(__file__).with_name("discover_max_safe.py")
SPEC = importlib.util.spec_from_file_location("issue65_discover_max_safe", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def fake_evidence(slots: int) -> dict:
    return {
        "status": "pass",
        "generated_ids": list(range(24)),
        "lifecycle": {key: 0 for key in MODULE.LIFECYCLE_ZERO_KEYS},
        "multi_gpu": {"devices": [{
            "uuid": "GPU-target",
            "hot_requested_slots": slots,
            "hot_effective_slots": slots,
        }]},
    }


def main() -> None:
    selected, records = MODULE.bounded_binary_search(
        268, 1000, 16,
        lambda candidate: MODULE.ProbeDecision(
            "pass" if candidate <= 731 else "reject", "synthetic"),
    )
    assert selected == 731
    assert records[0][0] == 268 and records[1][0] == 1000
    assert len(records) <= 16

    samples = [{"gpus": [{"uuid": "GPU-target", "free_bytes": 2_000_000_000}]}]
    passed = MODULE.classify_candidate(
        0, fake_evidence(536), "", samples, "GPU-target", 536, 1_073_741_824)
    assert passed.outcome == "pass"
    low_headroom = MODULE.classify_candidate(
        0, fake_evidence(536), "", [{"gpus": [{"uuid": "GPU-target", "free_bytes": 1}]}],
        "GPU-target", 536, 1_073_741_824)
    assert low_headroom == MODULE.ProbeDecision("reject", "safety_reserve_not_preserved")
    oom = MODULE.classify_candidate(
        3, None, "CUDA error: out of memory", [], "GPU-target", 900, 1)
    assert oom == MODULE.ProbeDecision("reject", "allocation_or_oom")
    correctness = MODULE.classify_candidate(
        9, None, "decode correctness failed", [], "GPU-target", 900, 1)
    assert correctness.outcome == "abort"
    clamped = fake_evidence(535)
    clamp_decision = MODULE.classify_candidate(
        0, clamped, "", samples, "GPU-target", 536, 1)
    assert clamp_decision == MODULE.ProbeDecision("abort", "requested_capacity_not_honored_exactly")

    print("ISSUE65_MAX_SAFE_WORKFLOW status=pass selected=731 oom=reject correctness=abort clamp=abort")


if __name__ == "__main__":
    main()

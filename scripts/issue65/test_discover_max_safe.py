#!/usr/bin/env python3
"""Unit/integration checks for issue 65's bounded MAX_SAFE workflow."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace


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
    assert oom == MODULE.ProbeDecision("reject", "allocation_or_memory_budget")
    cold_budget = MODULE.classify_candidate(
        6, None, "expert cache initialization failed at shared cold cache (provider error 8)",
        [], "GPU-target", 900, 1)
    assert cold_budget == MODULE.ProbeDecision("reject", "allocation_or_memory_budget")
    correctness = MODULE.classify_candidate(
        9, None, "decode correctness failed", [], "GPU-target", 900, 1)
    assert correctness.outcome == "abort"
    clamped = fake_evidence(535)
    clamp_decision = MODULE.classify_candidate(
        0, clamped, "", samples, "GPU-target", 536, 1)
    assert clamp_decision == MODULE.ProbeDecision("abort", "requested_capacity_not_honored_exactly")

    manifest_args = SimpleNamespace(
        project_revision="parent", nested_revision="nested", resident_device=0,
        role_template="1:{candidate}", target_device=1, target_uuid="GPU-target",
        target_bdf="00000000:00:0a.0", peer_staging_bytes=67_108_864,
        slot_stride=11_835_264, reserve_bytes=1_073_741_824, lower_bound=268,
        max_probes=32, sample_period=0.25, raw_dir=Path("/tmp/issue65-max-safe-test"),
    )
    inventory = [{
        "cuda_ordinal": 1, "uuid": "GPU-target", "pci_bdf": "00000000:00:0a.0",
        "total_vram_bytes": 24_000_000_000, "used_bytes": 1, "free_bytes": 23_999_999_999,
    }]
    selected_evidence = fake_evidence(731)
    selected_evidence["multi_gpu"]["devices"][0].update({
        "hot_pool_bytes": 8_000_000_000, "ring_actual_bytes": 67_000_000,
    })
    manifest = MODULE.build_capacity_manifest(
        manifest_args, status="pass", abort_reason=None, inventory=inventory,
        artifact={
            "identity_manifest": "checkpoint-a/manifest.json",
            "identity_manifest_sha256": "a" * 64,
            "model_repository": "repo", "model_revision": "revision", "variant": "variant",
            "total_bytes": 123, "files": [{"name": "model.gguf", "size": 123, "sha256": "b" * 64}],
            "runtime_model_path": "/models/model.gguf",
        },
        cuda_runtime={
            "nvidia_driver_version": "580.173.02", "cuda_driver_api_version": "13.0",
            "cuda_runtime_version": "12.8", "cuda_toolkit_version": "12.8",
        },
        deterministic_upper=1000, upper=1000,
        ordered=[(268, MODULE.ProbeDecision("pass", "safe")),
                 (1000, MODULE.ProbeDecision("reject", "allocation_or_memory_budget")),
                 (731, MODULE.ProbeDecision("pass", "safe"))],
        selected=731, selected_evidence=selected_evidence,
        selected_samples=[{"gpus": [{
            "uuid": "GPU-target", "free_bytes": 2_000_000_000, "used_bytes": 22_000_000_000,
        }]}], selected_log="")
    assert manifest["schema_version"] == "issue65-max-safe-capacity-v2"
    assert manifest["runtime"]["cuda_runtime_version"] == "12.8"
    assert manifest["configuration"]["cold_cache_bytes"] == 17_179_869_184
    assert manifest["configuration"]["transfer_ring_bytes"] == 67_173_120
    assert manifest["configuration"]["queue_depth"] == 256
    assert manifest["configuration"]["peer_staging_bytes"] == 67_108_864
    assert manifest["artifact"]["files"][0]["sha256"] == "b" * 64
    incomplete = dict(manifest)
    incomplete["configuration"] = dict(manifest["configuration"])
    del incomplete["configuration"]["queue_depth"]
    try:
        MODULE.validate_capacity_manifest(incomplete)
        raise AssertionError("incomplete MAX_SAFE manifest was accepted")
    except RuntimeError:
        pass

    print("ISSUE65_MAX_SAFE_WORKFLOW status=pass selected=731 oom=reject correctness=abort "
          "clamp=abort manifest_schema=pass")


if __name__ == "__main__":
    main()

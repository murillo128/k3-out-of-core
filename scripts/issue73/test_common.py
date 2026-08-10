#!/usr/bin/env python3
"""Deterministic checks for issue 73's full-K3 command construction."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from common import DEFAULT_PEER_STAGING_BYTES, PROMPT, probe_command, validate_workload
from run_matrix import artifact_identity, block_delta, revision_state


def option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def main() -> None:
    local = probe_command(
        Path("probe"), Path("model"), Path("output"),
        role_devices="0:64", n_gpu_layers=8)
    assert option(local, "--peer-staging-bytes") == "0"
    assert option(local, "--n-gpu-layers") == "8"
    assert option(local, "--n-ubatch") == "4"
    assert option(local, "--max-generate") == "24"
    assert option(local, "--transport") == "POSITIONAL"
    assert option(local, "--miss-policy") == "PROMOTE_AND_GPU"
    assert option(local, "--prompt") == PROMPT

    cpu = probe_command(
        Path("probe"), Path("model"), Path("output"),
        role_devices="0:64", n_gpu_layers=8, miss_policy="CPU_FALLBACK")
    assert option(cpu, "--miss-policy") == "CPU_FALLBACK"

    try:
        probe_command(
            Path("probe"), Path("model"), Path("output"),
            role_devices="0:64", n_gpu_layers=8, miss_policy="SILENT_FALLBACK")
        raise AssertionError("unsupported miss policy accepted")
    except ValueError as error:
        assert "unsupported miss policy" in str(error)

    remote = probe_command(
        Path("probe"), Path("model"), Path("output"),
        role_devices="1:128,2:256,3:512", n_gpu_layers=6,
        n_ubatch=2, max_generate=256, transport="DIRECT_IO", async_cold_fill=True)
    assert option(remote, "--hot-slots") == "896"
    assert option(remote, "--peer-staging-bytes") == str(DEFAULT_PEER_STAGING_BYTES)
    assert option(remote, "--max-generate") == "256"
    assert option(remote, "--n-ubatch") == "2"
    assert option(remote, "--async-cold-fill") == "1"

    delta = block_delta(
        {"read_operations": 1, "read_sectors": 2, "read_ticks_ms": 3, "in_flight": 0,
         "io_ticks_ms": 4, "weighted_ticks_ms": 5},
        {"read_operations": 2, "read_sectors": 5, "read_ticks_ms": 7, "in_flight": 0,
         "io_ticks_ms": 9, "weighted_ticks_ms": 11},
    )
    assert delta["read_bytes"] == 1536
    revisions = revision_state()
    assert len(revisions["project"]) == 40
    assert len(revisions["nested"]) == 40

    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        model = temporary / "model.gguf"
        model.write_bytes(b"model")
        identity = temporary / "identity.json"
        identity.write_text(json.dumps({"artifact": {
            "repository": "moonshotai/Kimi-K3",
            "revision": "9f62e4e9fffbd0a83ddd60e1c209d828994b3569",
            "variant": "fixture", "total_bytes": 5,
            "files": [{"name": "model.gguf"}],
        }}))
        captured_identity = artifact_identity(identity, model)
        assert captured_identity["file_count"] == 1
        assert len(captured_identity["manifest_sha256"]) == 64

    workload = {
        "status": "pass", "generated_ids": [1], "logits_fnv64": [2], "latency_us": [3],
        "transport_requested": "DIRECT_IO", "io_access_effective": "NORMAL",
        "storage": {
            "sealed": True, "poisoned": False, "source_file_count": 33,
            "direct_source_count": 33,
        },
        "lifecycle": {}, "transfer": {}, "mechanism": {},
        "multi_gpu": {
            "directory_owner_only_violations": 0,
            "devices": [{"scheduler": {}}], "peer_diagnostics": [{}],
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "workload.json"
        path.write_text(json.dumps(workload))
        assert validate_workload(path, 1)["generated_ids"] == [1]
        workload["storage"]["short_reads"] = 1
        path.write_text(json.dumps(workload))
        try:
            validate_workload(path, 1)
            raise AssertionError("short read accepted")
        except RuntimeError as error:
            assert "storage terminal" in str(error)

    print("ISSUE73_COMMON status=pass local_staging=0 remote_staging=bounded "
          "max_generate=256 fail_closed=pass")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Deterministic checks for issue 73's full-K3 command construction."""

from __future__ import annotations

from pathlib import Path

from common import DEFAULT_PEER_STAGING_BYTES, PROMPT, probe_command


def option(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def main() -> None:
    local = probe_command(
        Path("probe"), Path("model"), Path("output"),
        role_devices="0:64", n_gpu_layers=8)
    assert option(local, "--peer-staging-bytes") == "0"
    assert option(local, "--n-gpu-layers") == "8"
    assert option(local, "--max-generate") == "24"
    assert option(local, "--transport") == "POSITIONAL"
    assert option(local, "--prompt") == PROMPT

    remote = probe_command(
        Path("probe"), Path("model"), Path("output"),
        role_devices="1:128,2:256,3:512", n_gpu_layers=6,
        max_generate=256, transport="DIRECT_IO", async_cold_fill=True)
    assert option(remote, "--hot-slots") == "896"
    assert option(remote, "--peer-staging-bytes") == str(DEFAULT_PEER_STAGING_BYTES)
    assert option(remote, "--max-generate") == "256"
    assert option(remote, "--async-cold-fill") == "1"

    print("ISSUE73_COMMON status=pass local_staging=0 remote_staging=bounded max_generate=256")


if __name__ == "__main__":
    main()

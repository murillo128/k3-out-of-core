#!/usr/bin/env python3
"""Fetch and verify release-only #105 v3 route-structure inputs."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from scripts.common import FIGURES, sha256


REPOSITORY = "murillo128/k3-out-of-core"
RELEASE = "issue105-curated-analysis-v3"
TARGET_COMMIT = "6db0c3ddecf2ab8ff3ca7c729dfac98ef75be468"
ASSET = f"{RELEASE}.tar.zst"
ASSET_SHA256 = "e0fe96c2f4dd3d2cfc8ced16901949936ba3e72c79ebdd4eb412f371fe843fb3"
ASSET_SIZE = 15_031_406
PREFIX = f"{RELEASE}/inputs/host/stage-b-analysis-v1"
MEMBERS = {
    "family-overlap-matrix.json": (
        679_545,
        "c1ef18ce953d15231c997e115980e9dcb1a315e8fa581ee32f15fb0324d6ea73",
    ),
    "stage-b2-family-length-route-endpoints.json": (
        1_449_552,
        "24d48e8cf0fdf2c981562231d1492f6c6890b01eccaae239d1ac7c8b63d05b47",
    ),
}
URL = f"https://github.com/{REPOSITORY}/releases/download/{RELEASE}/{ASSET}"


def cache_root() -> Path:
    override = os.environ.get("K3_PAPER_FIGURE_CACHE")
    return Path(override).expanduser().resolve() if override else FIGURES / ".cache"


def _verify(path: Path, expected_size: int, expected_sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == expected_size and sha256(path) == expected_sha256


def ensure_issue105_inputs(*, offline: bool = False) -> dict[str, Path]:
    """Return the two checksum-verified route-structure JSON members."""
    base = cache_root() / RELEASE
    asset = base / ASSET
    outputs = {name: base / "inputs" / name for name in MEMBERS}
    if all(_verify(outputs[name], *MEMBERS[name]) for name in MEMBERS):
        return outputs

    base.mkdir(parents=True, exist_ok=True)
    if not _verify(asset, ASSET_SIZE, ASSET_SHA256):
        if offline:
            raise FileNotFoundError(f"offline mode: verified #105 v3 route inputs unavailable under {base}")
        with tempfile.NamedTemporaryFile(dir=base, prefix=f".{ASSET}.", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(URL) as response, tmp_path.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if not _verify(tmp_path, ASSET_SIZE, ASSET_SHA256):
                raise ValueError(f"downloaded #105 v3 asset failed size/SHA-256 verification: {URL}")
            os.replace(tmp_path, asset)
        finally:
            tmp_path.unlink(missing_ok=True)
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd is required to extract the verified #105 v3 release asset")

    member_paths = [f"{PREFIX}/{name}" for name in MEMBERS]
    listing = subprocess.run(
        ["tar", "--use-compress-program=unzstd", "-tf", str(asset)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    for member in member_paths:
        if listing.count(member) != 1:
            raise ValueError(f"immutable #105 v3 release must contain exactly one {member!r}")

    with tempfile.TemporaryDirectory(dir=base, prefix=".extract-") as temporary:
        temporary_path = Path(temporary)
        subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-xf", str(asset), "-C", str(temporary_path), *member_paths],
            check=True,
        )
        output_dir = base / "inputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, member in zip(MEMBERS, member_paths):
            extracted = temporary_path / member
            if not _verify(extracted, *MEMBERS[name]):
                raise ValueError(f"extracted #105 v3 member failed size/SHA-256 verification: {member}")
            shutil.copyfile(extracted, outputs[name])

    if not all(_verify(outputs[name], *MEMBERS[name]) for name in MEMBERS):
        raise ValueError("cached #105 v3 route inputs failed validation")
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="fail instead of downloading a missing asset")
    args = parser.parse_args()
    paths = ensure_issue105_inputs(offline=args.offline)
    for name, path in paths.items():
        print(f"verified {name}: {path} ({MEMBERS[name][1]})")


if __name__ == "__main__":
    main()

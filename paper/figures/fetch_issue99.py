#!/usr/bin/env python3
"""Fetch and verify the immutable #99 release member used by the quality figures."""

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
RELEASE = "issue99-long-horizon-quality-v1"
TARGET_COMMIT = "eeaab5fa3f62047e8617ab3ed408ccbddbb56872"
ASSET = "issue99-long-horizon-quality-v1-core.tar.zst"
ASSET_SHA256 = "59590e168f2d122ef8948d60aa1b1102c79e553846b4d4570ca62cdb4a7e3763"
ASSET_SIZE = 225_619_088
MEMBER = f"{RELEASE}/analysis/datasets/longrun-checkpoints.parquet"
MEMBER_SHA256 = "ea9d98b5bccaa91d5ed356f214a8f81de0e7ed3fcdf6f9e4dd761eda1d6f64e6"
MEMBER_SIZE = 60_198
URL = f"https://github.com/{REPOSITORY}/releases/download/{RELEASE}/{ASSET}"


def cache_root() -> Path:
    override = os.environ.get("K3_PAPER_FIGURE_CACHE")
    return Path(override).expanduser().resolve() if override else FIGURES / ".cache"


def _verify(path: Path, expected_size: int, expected_sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == expected_size and sha256(path) == expected_sha256


def ensure_issue99_inputs(*, offline: bool = False) -> Path:
    """Return the verified checkpoint parquet, fetching/extracting it when necessary."""
    base = cache_root() / RELEASE
    asset = base / ASSET
    parquet = base / "datasets" / "longrun-checkpoints.parquet"
    if _verify(parquet, MEMBER_SIZE, MEMBER_SHA256):
        return parquet

    base.mkdir(parents=True, exist_ok=True)
    if not _verify(asset, ASSET_SIZE, ASSET_SHA256):
        if offline:
            raise FileNotFoundError(
                f"offline mode: verified #99 asset/member unavailable under {base}"
            )
        with tempfile.NamedTemporaryFile(dir=base, prefix=f".{ASSET}.", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            with urllib.request.urlopen(URL) as response, tmp_path.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            if not _verify(tmp_path, ASSET_SIZE, ASSET_SHA256):
                raise ValueError(f"downloaded release asset failed size/SHA-256 verification: {URL}")
            os.replace(tmp_path, asset)
        finally:
            tmp_path.unlink(missing_ok=True)

    if not _verify(asset, ASSET_SIZE, ASSET_SHA256):
        raise ValueError(f"cached release asset failed verification: {asset}")
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd is required to extract the verified #99 release asset")

    with tempfile.TemporaryDirectory(dir=base, prefix=".extract-") as temporary:
        temporary_path = Path(temporary)
        listing = subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-tf", str(asset)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if listing.count(MEMBER) != 1:
            raise ValueError(f"immutable release must contain exactly one {MEMBER!r}; got {listing.count(MEMBER)}")
        subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-xf", str(asset), "-C", str(temporary_path), MEMBER],
            check=True,
        )
        extracted = temporary_path / MEMBER
        if not _verify(extracted, MEMBER_SIZE, MEMBER_SHA256):
            raise ValueError(f"extracted release member failed size/SHA-256 verification: {MEMBER}")
        parquet.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(extracted, parquet)

    if not _verify(parquet, MEMBER_SIZE, MEMBER_SHA256):
        raise ValueError(f"cached release member failed verification: {parquet}")
    return parquet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="fail instead of downloading a missing asset")
    args = parser.parse_args()
    path = ensure_issue99_inputs(offline=args.offline)
    print(f"verified {path} ({MEMBER_SHA256})")


if __name__ == "__main__":
    main()

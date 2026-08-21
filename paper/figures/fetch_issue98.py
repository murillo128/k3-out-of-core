#!/usr/bin/env python3
"""Fetch and verify the immutable #98 v3 policy-selection evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from scripts.common import FIGURES, sha256


REPOSITORY = "murillo128/k3-out-of-core"
RELEASE = "issue98-profile-shape-extension-v3"
TARGET_COMMIT = "485819939e9d074f99a646443a2bbab8f1466eb8"
ASSET = f"{RELEASE}.tar.zst"
ASSET_SHA256 = "f85b8fae5e4122956592723f537c4e0c905d97f47dab586da50b5e34c9356643"
ASSET_SIZE = 198_462
FINAL_MEMBER = f"{RELEASE}/final/final-synthesis.json"
FINAL_SHA256 = "5142b1ce19dfe0b55e40b2f23e8932cb9ec025b9569781c56fceda0eb2766dc9"
FINAL_SIZE = 60_307
URL = f"https://github.com/{REPOSITORY}/releases/download/{RELEASE}/{ASSET}"


def cache_root() -> Path:
    override = os.environ.get("K3_PAPER_FIGURE_CACHE")
    return Path(override).expanduser().resolve() if override else FIGURES / ".cache"


def _verify(path: Path, expected_size: int, expected_sha256: str) -> bool:
    return path.is_file() and path.stat().st_size == expected_size and sha256(path) == expected_sha256


def _download(asset: Path, *, offline: bool) -> None:
    if _verify(asset, ASSET_SIZE, ASSET_SHA256):
        return
    if offline:
        raise FileNotFoundError(f"offline mode: verified #98 asset unavailable at {asset}")
    asset.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=asset.parent, prefix=f".{ASSET}.", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(URL) as response, tmp_path.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if not _verify(tmp_path, ASSET_SIZE, ASSET_SHA256):
            raise ValueError(f"downloaded #98 asset failed size/SHA-256 verification: {URL}")
        os.replace(tmp_path, asset)
    finally:
        tmp_path.unlink(missing_ok=True)


def _validate(root: Path) -> bool:
    final = root / "final" / "final-synthesis.json"
    if not _verify(final, FINAL_SIZE, FINAL_SHA256):
        return False
    screening = sorted((root / "screening").glob("run-*/validated-summary.json"))
    confirmation = sorted((root / "confirmation").glob("run-*/validated-summary.json"))
    if len(screening) != 21 or len(confirmation) != 6:
        return False
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in screening + confirmation]
    return all(row.get("status") == "pass" for row in rows)


def ensure_issue98_inputs(*, offline: bool = False) -> Path:
    """Return the verified extracted #98 release root."""
    base = cache_root() / RELEASE
    asset = base / ASSET
    extracted = base / "extracted" / RELEASE
    if _validate(extracted):
        return extracted

    _download(asset, offline=offline)
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd is required to extract the verified #98 release asset")
    with tempfile.TemporaryDirectory(dir=base, prefix=".extract-") as temporary:
        temporary_path = Path(temporary)
        subprocess.run(
            ["tar", "--use-compress-program=unzstd", "-xf", str(asset), "-C", str(temporary_path)],
            check=True,
        )
        candidate = temporary_path / RELEASE
        if not _validate(candidate):
            raise ValueError("extracted #98 evidence failed final-hash/cardinality validation")
        destination = base / "extracted"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(temporary_path, destination)

    if not _validate(extracted):
        raise ValueError("cached #98 evidence failed validation")
    return extracted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="fail instead of downloading a missing asset")
    args = parser.parse_args()
    root = ensure_issue98_inputs(offline=args.offline)
    print(f"verified {root} ({ASSET_SHA256})")


if __name__ == "__main__":
    main()

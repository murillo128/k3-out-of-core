#!/usr/bin/env python3
"""Fetch and verify the immutable #77 decision-summary evidence."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

from scripts.common import FIGURES, sha256


REPOSITORY = "murillo128/k3-out-of-core"
RELEASE = "issue77-phase13-6-evidence-v1"
TARGET_COMMIT = "9d0433896032055d9e114b61686717ec172e0329"
ASSET = "EVIDENCE.md"
ASSET_SHA256 = "e2adfc7f29f65218198e72ef3e16fae74fdac113fd5b8d6c81f1a98b1434d9c5"
ASSET_SIZE = 4_860
URL = f"https://github.com/{REPOSITORY}/releases/download/{RELEASE}/{ASSET}"


def cache_root() -> Path:
    override = os.environ.get("K3_PAPER_FIGURE_CACHE")
    return Path(override).expanduser().resolve() if override else FIGURES / ".cache"


def _verify(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != ASSET_SIZE or sha256(path) != ASSET_SHA256:
        return False
    text = path.read_text(encoding="utf-8")
    required = [
        f"project: `{TARGET_COMMIT}`",
        "Free-generation decision points at 96 GiB / top-32:",
        "Teacher-forced quality/locality correlation for every retained changed point:",
        "| Stress p50 / max16 | 2,723 | 7 | 8.043% | 3,521 | 22.899% |",
        "| Mean KL / JS | 0.000507 / 0.000125 | 0.000783 / 0.000192 | 0.000860 / 0.000213 | 0.001641 / 0.000407 |",
    ]
    return all(fragment in text for fragment in required)


def ensure_issue77_inputs(*, offline: bool = False) -> Path:
    """Return the verified #77 release summary used by the policy-evolution plot."""
    base = cache_root() / RELEASE
    asset = base / ASSET
    if _verify(asset):
        return asset
    if offline:
        raise FileNotFoundError(f"offline mode: verified #77 asset unavailable at {asset}")

    base.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=base, prefix=f".{ASSET}.", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with urllib.request.urlopen(URL) as response, tmp_path.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if not _verify(tmp_path):
            raise ValueError(f"downloaded #77 asset failed size/SHA-256/content verification: {URL}")
        os.replace(tmp_path, asset)
    finally:
        tmp_path.unlink(missing_ok=True)

    if not _verify(asset):
        raise ValueError("cached #77 evidence failed validation")
    return asset


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="fail instead of downloading a missing asset")
    args = parser.parse_args()
    path = ensure_issue77_inputs(offline=args.offline)
    print(f"verified {path} ({ASSET_SHA256})")


if __name__ == "__main__":
    main()

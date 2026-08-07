#!/usr/bin/env python3
"""Native API parity and fail-closed fixture checks, executed by CTest."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))
from common import Scale  # noqa: E402
from corpus import generate  # noqa: E402
from plan import build_plan, encode_plan  # noqa: E402


def invoke(
    binary: Path,
    plan: Path,
    api: str,
    qd: int,
    output: Path,
    *,
    expect_success: bool,
    expected_error: str | None = None,
    extra: tuple[str, ...] = (),
    iterations: int = 2,
) -> dict[str, object] | None:
    completed = subprocess.run([
        str(binary), "--plan", str(plan), "--api", api,
        "--cache-state", "OS_COLD_VERIFIED", "--qd", str(qd),
        "--iterations", str(iterations), "--output", str(output),
        *extra,
    ], text=True, capture_output=True)
    if expect_success and completed.returncode:
        raise AssertionError(f"{api} failed: {completed.stderr}")
    if not expect_success and not completed.returncode:
        raise AssertionError(f"{api} unexpectedly accepted invalid input")
    if expected_error and expected_error not in completed.stderr:
        raise AssertionError(f"{api} did not report {expected_error!r}: {completed.stderr}")
    return json.loads(output.read_text()) if expect_success else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    fixture = Scale(layers=2, experts=32, selected=16, projection_bytes=4096, tokens=4)
    with tempfile.TemporaryDirectory(prefix="phase12-nvme-native-") as temporary:
        root = Path(temporary)
        corpus = root / "corpus"
        generate(corpus, fixture)
        plan = root / "plan.tsv"
        plan.write_bytes(encode_plan(build_plan(corpus, "A", "COLD_SPREAD", 0, "LOGICAL_SELECTED")))
        sinks = set()
        for api, qd in (
            ("buffered-pread", 2),
            ("direct-pread", 2),
            ("buffered-io-uring", 2),
            ("direct-io-uring", 2),
            ("mmap-buffered", 1),
        ):
            result = invoke(args.binary, plan, api, qd, root / f"{api}.json", expect_success=True, iterations=8)
            assert result is not None
            if result["status"] != "PASS" or result["short_reads"] != 0:
                raise AssertionError(f"invalid successful result for {api}")
            if result["lifetime_resources"] != {"fd_delta": 0, "thread_delta": 0}:
                raise AssertionError(f"resource leak detected for {api}: {result['lifetime_resources']}")
            sinks.add(result["checksum_sink_sha256"])
        if len(sinks) != 1:
            raise AssertionError("API checksum sinks differ")

        invalid_alignment = root / "invalid-alignment.tsv"
        fields = [line for line in plan.read_text().splitlines() if not line.startswith("#")][0].split("\t")
        fields[3] = str(int(fields[3]) + 1)
        invalid_alignment.write_text("\t".join(fields) + "\n")
        invoke(
            args.binary, invalid_alignment, "direct-pread", 1, root / "invalid.json",
            expect_success=False, expected_error="direct range is not 4 KiB aligned",
        )

        malformed = root / "malformed.tsv"
        malformed.write_text("0\t0\ttoo\tfew\tfields\n")
        invoke(
            args.binary, malformed, "buffered-pread", 1, root / "malformed.json",
            expect_success=False, expected_error="six tab-separated fields",
        )

        missing = root / "missing.tsv"
        missing_fields = [line for line in plan.read_text().splitlines() if not line.startswith("#")][0].split("\t")
        missing_fields[2] = str(root / "source-disappeared.bin")
        missing.write_text("\t".join(missing_fields) + "\n")
        invoke(
            args.binary, missing, "buffered-io-uring", 1, root / "missing.json",
            expect_success=False, expected_error="open failed",
        )

        corrupt = root / "corrupt"
        shutil.copytree(corpus, corrupt)
        corrupt_path = corrupt / "layout-b/contiguous-experts.bin"
        with corrupt_path.open("r+b") as stream:
            stream.seek(4096)
            byte = stream.read(1)
            stream.seek(4096)
            stream.write(bytes((byte[0] ^ 1,)))
        corrupt_plan = root / "corrupt.tsv"
        corrupt_plan.write_bytes(encode_plan(build_plan(corrupt, "B", "COLD_SPREAD", 0, "LOGICAL_SELECTED")))
        invoke(
            args.binary, corrupt_plan, "buffered-pread", 1, root / "corrupt.json",
            expect_success=False, expected_error="bundle checksum mismatch",
        )

        truncated = root / "truncated"
        shutil.copytree(corpus, truncated)
        truncated_plan = root / "truncated.tsv"
        truncated_plan.write_bytes(encode_plan(build_plan(truncated, "B", "COLD_SPREAD", 0, "LOGICAL_SELECTED")))
        truncated_path = truncated / "layout-b/contiguous-experts.bin"
        with truncated_path.open("r+b") as stream:
            stream.truncate(4096 + fixture.bundle_bytes - 1)
        invoke(
            args.binary, truncated_plan, "buffered-io-uring", 2, root / "truncated.json",
            expect_success=False, expected_error="operation extends past EOF",
        )

        invoke(
            args.binary,
            plan,
            "direct-io-uring",
            4,
            root / "eio.json",
            expect_success=False,
            expected_error="injected EIO completion",
            extra=("--inject-eio-after", "3"),
        )
        invoke(
            args.binary,
            plan,
            "direct-io-uring",
            4,
            root / "stale.json",
            expect_success=False,
            expected_error="stale io_uring completion",
            extra=("--inject-stale-after", "3"),
        )

        invoke(
            args.binary,
            plan,
            "direct-io-uring",
            4,
            root / "cancelled.json",
            expect_success=False,
            expected_error="injected cancellation",
            extra=("--cancel-after", "3"),
        )
        retry = invoke(args.binary, plan, "direct-io-uring", 4, root / "retry.json", expect_success=True)
        assert retry is not None and retry["status"] == "PASS"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

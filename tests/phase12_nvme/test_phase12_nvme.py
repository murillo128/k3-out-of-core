#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/phase12p"))
sys.path.insert(0, str(ROOT / "scripts/phase12_nvme"))

from common import Scale  # noqa: E402
from corpus import generate  # noqa: E402
from plan import build_plan, encode_plan  # noqa: E402


FIXTURE = Scale(layers=2, experts=32, selected=16, projection_bytes=4096, tokens=4)


class Phase12NvmePlanTests(unittest.TestCase):
    def test_orders_preserve_logical_identity_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-plan-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            plans = {
                order: build_plan(corpus, "A", "LOGICAL_SHUFFLE", 1, order)
                for order in ("LOGICAL_SELECTED", "PHYSICAL_OFFSET", "LOCALITY_WINDOW_8")
            }
            identities = {
                order: sorted((item.ordinal, item.layer, item.expert, item.sha256) for item in operations)
                for order, operations in plans.items()
            }
            self.assertEqual(identities["LOGICAL_SELECTED"], identities["PHYSICAL_OFFSET"])
            self.assertEqual(identities["LOGICAL_SELECTED"], identities["LOCALITY_WINDOW_8"])
            self.assertNotEqual(
                [item.ordinal for item in plans["LOGICAL_SELECTED"]],
                [item.ordinal for item in plans["PHYSICAL_OFFSET"]],
            )
            self.assertEqual(len(encode_plan(plans["LOGICAL_SELECTED"]).splitlines()), 33)

    def test_layouts_are_plan_checksum_equivalent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-layout-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            layout_a = build_plan(corpus, "A", "HALF_HOT", 3, "LOGICAL_SELECTED")
            layout_b = build_plan(corpus, "B", "HALF_HOT", 3, "LOGICAL_SELECTED")
            self.assertEqual(
                [(item.ordinal, item.layer, item.expert, item.length, item.sha256) for item in layout_a],
                [(item.ordinal, item.layer, item.expert, item.length, item.sha256) for item in layout_b],
            )

    def test_malformed_layout_b_index_fails_plan_construction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="phase12-nvme-index-") as temporary:
            corpus = Path(temporary) / "corpus"
            generate(corpus, FIXTURE)
            path = corpus / "layout-b/contiguous-experts.bin"
            with path.open("r+b") as stream:
                header = json.loads(stream.read(4096).rstrip(b"\0"))
                stream.seek(header["index_offset"])
                byte = stream.read(1)
                stream.seek(header["index_offset"])
                stream.write(bytes((byte[0] ^ 1,)))
            with self.assertRaisesRegex(ValueError, "Layout B index checksum mismatch"):
                build_plan(corpus, "B", "COLD_SPREAD", 0, "LOGICAL_SELECTED")


if __name__ == "__main__":
    unittest.main(verbosity=2)

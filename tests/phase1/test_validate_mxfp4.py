from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/phase1/validate_mxfp4.py"
SPEC = importlib.util.spec_from_file_location("validate_mxfp4", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
mxfp4 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mxfp4)


class ValidateMXFP4Tests(unittest.TestCase):
    def test_e2m1_complete_codebook(self) -> None:
        expected = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0]
        expected += [-value for value in expected]
        self.assertEqual([mxfp4.decode_e2m1(code) for code in range(16)], expected)

    def test_e2m1_rejects_out_of_range_code(self) -> None:
        with self.assertRaisesRegex(mxfp4.ValidationError, "outside four bits"):
            mxfp4.decode_e2m1(16)

    def test_e8m0_known_values(self) -> None:
        self.assertEqual(mxfp4.decode_e8m0(0), 2.0**-127)
        self.assertEqual(mxfp4.decode_e8m0(126), 0.5)
        self.assertEqual(mxfp4.decode_e8m0(127), 1.0)
        self.assertEqual(mxfp4.decode_e8m0(128), 2.0)
        self.assertEqual(mxfp4.decode_e8m0(254), 2.0**127)

    def test_e8m0_rejects_reserved_nan(self) -> None:
        with self.assertRaisesRegex(mxfp4.ValidationError, "reserved for NaN"):
            mxfp4.decode_e8m0(0xFF)

    def test_source_nibble_order(self) -> None:
        source = bytes.fromhex("1032547698badcfe" * 2)
        self.assertEqual(mxfp4.unpack_source_codes(source), list(range(16)) * 2)

    def test_repack_known_vector(self) -> None:
        source = bytes.fromhex("1032547698badcfe" * 2)
        expected = bytes((127,)) + bytes(index | (index << 4) for index in range(16))
        self.assertEqual(mxfp4.repack_source_block(127, source), expected)

    def test_gguf_nibble_order(self) -> None:
        block = bytes((127,)) + bytes(index | ((15 - index) << 4) for index in range(16))
        scale, codes = mxfp4.unpack_gguf_codes(block)
        self.assertEqual(scale, 127)
        self.assertEqual(codes, list(range(16)) + list(reversed(range(16))))

    def test_validate_sample_matches_exact_layout(self) -> None:
        source = np.array([list(bytes.fromhex("1032547698badcfe" * 2))], dtype=np.uint8)
        scales = np.array([[127]], dtype=np.uint8)
        expected = mxfp4.repack_source_block(127, source[0])
        gguf = np.zeros((8, 1, 17), dtype=np.uint8)
        gguf[3, 0] = np.frombuffer(expected, dtype=np.uint8)
        result = mxfp4.validate_sample(
            layer=1,
            projection="w1",
            expert=3,
            position="first",
            packed=source,
            scales=scales,
            gguf_data=gguf,
        )
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["maximum_absolute_error"], 0.0)

    def test_validate_sample_rejects_byte_mismatch(self) -> None:
        source = np.zeros((1, 16), dtype=np.uint8)
        scales = np.array([[127]], dtype=np.uint8)
        gguf = np.zeros((8, 1, 17), dtype=np.uint8)
        gguf[0, 0, 0] = 126
        with self.assertRaisesRegex(mxfp4.ValidationError, "failed"):
            mxfp4.validate_sample(
                layer=1,
                projection="w1",
                expert=0,
                position="first",
                packed=source,
                scales=scales,
                gguf_data=gguf,
            )

    def test_block_positions_are_row_major(self) -> None:
        self.assertEqual(
            mxfp4.block_indexes(9),
            {"first": 0, "middle": 4, "last": 8},
        )


if __name__ == "__main__":
    unittest.main()

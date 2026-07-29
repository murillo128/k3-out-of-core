from __future__ import annotations

import unittest

from scripts.phase2.expert_storage_map import classify_spans, validate_projection_layout


def projection(spans: list[dict[str, int]], kind: str) -> dict:
    return {
        "spans": spans,
        "layout_kind": kind,
        "expert_slice_bytes": sum(span["length"] for span in spans),
    }


class ExpertStorageLayoutTests(unittest.TestCase):
    def test_contiguous_layout(self) -> None:
        value = projection(
            [{"file_offset": 4096, "length": 256, "logical_offset": 0}], "contiguous"
        )
        validate_projection_layout(value)
        self.assertEqual(classify_spans(value["spans"]), "contiguous")

    def test_strided_layout(self) -> None:
        value = projection(
            [
                {"file_offset": 4096, "length": 64, "logical_offset": 0},
                {"file_offset": 4224, "length": 64, "logical_offset": 64},
                {"file_offset": 4352, "length": 64, "logical_offset": 128},
            ],
            "strided",
        )
        validate_projection_layout(value)
        self.assertEqual(classify_spans(value["spans"]), "strided")

    def test_segmented_layout(self) -> None:
        value = projection(
            [
                {"file_offset": 4096, "length": 64, "logical_offset": 0},
                {"file_offset": 5000, "length": 32, "logical_offset": 64},
                {"file_offset": 8192, "length": 96, "logical_offset": 96},
            ],
            "segmented",
        )
        validate_projection_layout(value)
        self.assertEqual(classify_spans(value["spans"]), "segmented")

    def test_rejects_gap_in_logical_reconstruction(self) -> None:
        value = projection(
            [
                {"file_offset": 100, "length": 16, "logical_offset": 0},
                {"file_offset": 200, "length": 16, "logical_offset": 17},
            ],
            "strided",
        )
        with self.assertRaisesRegex(ValueError, "non-covering"):
            validate_projection_layout(value)

    def test_rejects_layout_misclassification(self) -> None:
        value = projection(
            [{"file_offset": 100, "length": 16, "logical_offset": 0}], "segmented"
        )
        with self.assertRaisesRegex(ValueError, "disagrees"):
            validate_projection_layout(value)


if __name__ == "__main__":
    unittest.main()

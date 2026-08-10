#!/usr/bin/env python3

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "issue75"))

from analyze_router_geometry import (  # noqa: E402
    adjusted_rand_index,
    analyze_layer,
    robust_zscores,
    silhouette_from_distances,
)


class StatisticalHelperTests(unittest.TestCase):
    def test_adjusted_rand_is_label_permutation_invariant(self) -> None:
        left = np.asarray([0, 0, 1, 1, 2, 2])
        right = np.asarray([7, 7, 3, 3, 9, 9])
        self.assertAlmostEqual(adjusted_rand_index(left, right), 1.0)

    def test_silhouette_identifies_separated_groups(self) -> None:
        distances = np.asarray(
            [
                [0.0, 0.1, 1.0, 1.0],
                [0.1, 0.0, 1.0, 1.0],
                [1.0, 1.0, 0.0, 0.1],
                [1.0, 1.0, 0.1, 0.0],
            ]
        )
        score = silhouette_from_distances(distances, np.asarray([0, 0, 1, 1]))
        self.assertGreater(score, 0.85)

    def test_silhouette_scores_singleton_sample_as_zero(self) -> None:
        distances = np.asarray(
            [
                [0.0, 1.0, 1.0],
                [1.0, 0.0, 0.1],
                [1.0, 0.1, 0.0],
            ]
        )
        score = silhouette_from_distances(distances, np.asarray([0, 1, 1]))
        self.assertAlmostEqual(score, 0.6)

    def test_robust_zscore_is_zero_for_constant_input(self) -> None:
        np.testing.assert_array_equal(robust_zscores(np.ones(8)), np.zeros(8))


class LayerAnalysisTests(unittest.TestCase):
    def test_exact_geometry_and_bias_relationships(self) -> None:
        weights = np.zeros((12, 14), dtype=np.float32)
        for expert in range(6):
            weights[expert, 0] = 1.0
            weights[expert, 2 + expert] = 0.01
        for expert in range(6, 12):
            weights[expert, 1] = 1.0
            weights[expert, 2 + expert] = 0.01
        bias = np.asarray([0.5] * 6 + [-0.5] * 6, dtype=np.float32)
        summary, details, runtime = analyze_layer(
            1, weights, bias, cluster_counts=(2, 3)
        )
        self.assertEqual(summary["layer"], 1)
        self.assertEqual(summary["cosine_pairs_ge_0p9"], 30)
        self.assertGreater(summary["cluster_best_silhouette"], 0.4)
        self.assertGreaterEqual(summary["cluster_best_smallest_size"], 6)
        self.assertGreater(summary["bias_neighbor_mean_spearman"], 0.9)
        self.assertLess(summary["bias_neighbor_difference_ratio"], 0.1)
        self.assertEqual(len(details["top_cosine_pairs"]), 10)
        self.assertEqual(runtime["unit_vectors"].shape, weights.shape)

    def test_non_finite_input_fails(self) -> None:
        weights = np.eye(4, dtype=np.float32)
        weights[0, 0] = np.nan
        with self.assertRaisesRegex(Exception, "NaN or Inf"):
            analyze_layer(1, weights, np.zeros(4), cluster_counts=(2,))


if __name__ == "__main__":
    unittest.main()

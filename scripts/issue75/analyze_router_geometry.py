#!/usr/bin/env python3
"""Analyze static Kimi K3 router geometry from the immutable issue-75 pack."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import squareform
from scipy.stats import spearmanr

from router_pack import (
    PackError,
    load_json,
    validate_inventory,
    validate_payload_tree,
    write_json,
)


CLUSTER_COUNTS = (2, 4, 8, 16, 32)
REDUNDANCY_THRESHOLDS = (0.3, 0.5, 0.7, 0.9)
OUTLIER_THRESHOLD = 5.0
NEIGHBOR_COUNT = 5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args()


def finite_float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise PackError(f"analysis produced a non-finite value: {value}")
    return result


def quantiles(values: np.ndarray, prefix: str) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0 or not np.all(np.isfinite(array)):
        raise PackError(f"cannot summarize invalid {prefix} values")
    levels = np.quantile(array, [0.01, 0.05, 0.5, 0.95, 0.99])
    return {
        f"{prefix}_min": finite_float(np.min(array)),
        f"{prefix}_p01": finite_float(levels[0]),
        f"{prefix}_p05": finite_float(levels[1]),
        f"{prefix}_median": finite_float(levels[2]),
        f"{prefix}_mean": finite_float(np.mean(array)),
        f"{prefix}_p95": finite_float(levels[3]),
        f"{prefix}_p99": finite_float(levels[4]),
        f"{prefix}_max": finite_float(np.max(array)),
        f"{prefix}_std": finite_float(np.std(array)),
    }


def robust_zscores(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    median = np.median(array)
    mad = np.median(np.abs(array - median))
    if mad <= np.finfo(np.float64).eps:
        return np.zeros_like(array)
    return 0.6744897501960817 * (array - median) / mad


def safe_spearman(left: np.ndarray, right: np.ndarray) -> float:
    left_array = np.asarray(left, dtype=np.float64)
    right_array = np.asarray(right, dtype=np.float64)
    if np.ptp(left_array) == 0.0 or np.ptp(right_array) == 0.0:
        return 0.0
    statistic = spearmanr(left_array, right_array).statistic
    return finite_float(statistic)


def silhouette_from_distances(distances: np.ndarray, labels: np.ndarray) -> float:
    labels = np.asarray(labels)
    unique = np.unique(labels)
    if unique.size < 2 or unique.size >= labels.size:
        return 0.0
    means = np.empty((labels.size, unique.size), dtype=np.float64)
    own = np.empty(labels.size, dtype=np.float64)
    for column, label in enumerate(unique):
        members = labels == label
        size = int(np.count_nonzero(members))
        sums = np.sum(distances[:, members], axis=1, dtype=np.float64)
        means[:, column] = sums / size
        if size == 1:
            own[members] = 0.0
        else:
            own[members] = sums[members] / (size - 1)
    other = means.copy()
    for column, label in enumerate(unique):
        other[labels == label, column] = np.inf
    nearest_other = np.min(other, axis=1)
    denominator = np.maximum(own, nearest_other)
    scores = np.divide(
        nearest_other - own,
        denominator,
        out=np.zeros_like(denominator),
        where=denominator > 0.0,
    )
    return finite_float(np.mean(scores))


def adjusted_rand_index(left: np.ndarray, right: np.ndarray) -> float:
    left = np.asarray(left)
    right = np.asarray(right)
    if left.shape != right.shape or left.ndim != 1:
        raise ValueError("ARI inputs must be equal-length vectors")
    _, left_ids = np.unique(left, return_inverse=True)
    _, right_ids = np.unique(right, return_inverse=True)
    contingency = np.zeros(
        (int(left_ids.max()) + 1, int(right_ids.max()) + 1), dtype=np.int64
    )
    np.add.at(contingency, (left_ids, right_ids), 1)

    def choose_two(values: np.ndarray) -> float:
        values = values.astype(np.float64)
        return finite_float(np.sum(values * (values - 1.0) / 2.0))

    pair_count = left.size * (left.size - 1.0) / 2.0
    if pair_count == 0.0:
        return 1.0
    sum_cells = choose_two(contingency)
    sum_rows = choose_two(np.sum(contingency, axis=1))
    sum_columns = choose_two(np.sum(contingency, axis=0))
    expected = sum_rows * sum_columns / pair_count
    maximum = 0.5 * (sum_rows + sum_columns)
    if maximum == expected:
        return 1.0
    return finite_float((sum_cells - expected) / (maximum - expected))


def _top_neighbor_indexes(cosine: np.ndarray, count: int) -> np.ndarray:
    if count >= cosine.shape[0]:
        raise ValueError("neighbor count must be smaller than expert count")
    working = cosine.copy()
    np.fill_diagonal(working, -np.inf)
    candidates = np.argpartition(working, -count, axis=1)[:, -count:]
    candidate_values = np.take_along_axis(working, candidates, axis=1)
    order = np.argsort(candidate_values, axis=1)[:, ::-1]
    return np.take_along_axis(candidates, order, axis=1)


def _outlier_records(
    layer: int,
    expert_metrics: dict[str, np.ndarray],
    threshold: float,
) -> list[dict[str, Any]]:
    records = []
    for metric, values in expert_metrics.items():
        scores = robust_zscores(values)
        indexes = np.flatnonzero(np.abs(scores) >= threshold)
        for expert in indexes:
            records.append(
                {
                    "layer": layer,
                    "expert": int(expert),
                    "metric": metric,
                    "value": finite_float(values[expert]),
                    "robust_z": finite_float(scores[expert]),
                }
            )
    records.sort(key=lambda item: abs(item["robust_z"]), reverse=True)
    return records


def analyze_layer(
    layer: int,
    weights: np.ndarray,
    bias: np.ndarray,
    cluster_counts: Iterable[int] = CLUSTER_COUNTS,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, np.ndarray]]:
    weights = np.asarray(weights, dtype=np.float32)
    bias = np.asarray(bias, dtype=np.float64)
    if weights.ndim != 2 or bias.shape != (weights.shape[0],):
        raise PackError(
            f"layer {layer} has incompatible weights/bias shapes: {weights.shape}, {bias.shape}"
        )
    if not np.all(np.isfinite(weights)) or not np.all(np.isfinite(bias)):
        raise PackError(f"layer {layer} contains NaN or Inf")

    expert_count = weights.shape[0]
    norms = np.linalg.norm(weights, axis=1).astype(np.float64)
    if np.any(norms == 0.0):
        raise PackError(f"layer {layer} contains a zero-norm router vector")
    unit = weights / norms.astype(np.float32)[:, None]
    cosine = np.asarray(unit @ unit.T, dtype=np.float64)
    cosine = 0.5 * (cosine + cosine.T)
    np.fill_diagonal(cosine, 1.0)
    upper = np.triu_indices(expert_count, 1)
    pairwise = cosine[upper]

    neighbor_count = min(NEIGHBOR_COUNT, expert_count - 1)
    neighbor_indexes = _top_neighbor_indexes(cosine, neighbor_count)
    nearest_indexes = neighbor_indexes[:, 0]
    nearest_cosines = cosine[np.arange(expert_count), nearest_indexes]

    centroid = np.mean(unit, axis=0, dtype=np.float64)
    centroid_projection = np.asarray(unit @ centroid, dtype=np.float64)
    row_mean = np.mean(cosine, axis=1, dtype=np.float64)
    grand_mean = finite_float(np.mean(row_mean))
    centered_gram = cosine - row_mean[:, None] - row_mean[None, :] + grand_mean
    centered_gram = 0.5 * (centered_gram + centered_gram.T)
    eigenvalues, eigenvectors = np.linalg.eigh(centered_gram)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    spectral_total = finite_float(np.sum(eigenvalues))
    if spectral_total <= 0.0:
        raise PackError(f"layer {layer} has a degenerate centered spectrum")
    spectrum = eigenvalues / spectral_total
    positive = spectrum[spectrum > 0.0]
    effective_rank = finite_float(np.exp(-np.sum(positive * np.log(positive))))
    participation_rank = finite_float(1.0 / np.sum(spectrum * spectrum))
    top_components = min(8, expert_count - 1)
    leverage = np.sum(eigenvectors[:, -top_components:] ** 2, axis=1)

    distances = np.clip(1.0 - cosine, 0.0, 2.0)
    np.fill_diagonal(distances, 0.0)
    hierarchy = linkage(squareform(distances, checks=False), method="average")
    silhouettes: dict[int, float] = {}
    labels_by_count: dict[int, np.ndarray] = {}
    for requested_count in cluster_counts:
        if not 2 <= requested_count < expert_count:
            continue
        labels = fcluster(hierarchy, t=requested_count, criterion="maxclust")
        actual_count = int(np.unique(labels).size)
        if actual_count < 2:
            continue
        labels_by_count[requested_count] = labels
        silhouettes[requested_count] = silhouette_from_distances(distances, labels)
    if not silhouettes:
        raise PackError(f"layer {layer} produced no valid cluster partitions")
    best_cluster_count = max(silhouettes, key=silhouettes.get)
    recurrence_count = 16 if 16 in labels_by_count else best_cluster_count

    centered_bias = bias - np.mean(bias)
    bias_energy = finite_float(np.dot(centered_bias, centered_bias))

    def bias_pc_fraction(count: int) -> float:
        count = min(count, expert_count - 1)
        if bias_energy == 0.0:
            return 0.0
        coefficients = eigenvectors[:, -count:].T @ centered_bias
        return finite_float(np.dot(coefficients, coefficients) / bias_energy)

    neighbor_bias = np.mean(bias[neighbor_indexes], axis=1)
    neighbor_absolute_difference = np.mean(
        np.abs(bias[:, None] - bias[neighbor_indexes]), axis=1
    )
    all_pair_bias_difference = np.abs(bias[:, None] - bias[None, :])[upper]
    pair_bias_scale = finite_float(np.mean(all_pair_bias_difference))
    geometry_smoothness = (
        finite_float(np.mean(neighbor_absolute_difference) / pair_bias_scale)
        if pair_bias_scale > 0.0
        else 1.0
    )

    summary: dict[str, Any] = {"layer": layer}
    summary.update(quantiles(norms, "router_norm"))
    summary["router_norm_cv"] = finite_float(np.std(norms) / np.mean(norms))
    summary.update(quantiles(pairwise, "pairwise_cosine"))
    summary.update(quantiles(nearest_cosines, "nearest_cosine"))
    summary["direction_centroid_norm"] = finite_float(np.linalg.norm(centroid))
    summary["centered_spectral_effective_rank"] = effective_rank
    summary["centered_spectral_participation_rank"] = participation_rank
    for count in (1, 8, 16, 32):
        usable = min(count, eigenvalues.size)
        summary[f"centered_spectral_top{count}_fraction"] = finite_float(
            np.sum(eigenvalues[-usable:]) / spectral_total
        )
    for threshold in REDUNDANCY_THRESHOLDS:
        name = str(threshold).replace(".", "p")
        summary[f"cosine_pairs_ge_{name}"] = int(np.count_nonzero(pairwise >= threshold))
    for count, score in silhouettes.items():
        summary[f"cluster_silhouette_k{count}"] = score
    summary["cluster_best_k"] = int(best_cluster_count)
    summary["cluster_best_silhouette"] = silhouettes[best_cluster_count]
    summary.update(quantiles(bias, "correction_bias"))
    summary["correction_bias_l2"] = finite_float(np.linalg.norm(bias))
    summary["correction_bias_negative_count"] = int(np.count_nonzero(bias < 0.0))
    summary["correction_bias_positive_count"] = int(np.count_nonzero(bias > 0.0))
    summary["correction_bias_unique_count"] = int(np.unique(bias).size)
    summary["bias_router_norm_spearman"] = safe_spearman(bias, norms)
    summary["bias_centroid_projection_spearman"] = safe_spearman(
        bias, centroid_projection
    )
    summary["bias_neighbor_mean_spearman"] = safe_spearman(bias, neighbor_bias)
    summary["bias_pair_geometry_spearman"] = safe_spearman(
        pairwise, -all_pair_bias_difference
    )
    summary["bias_neighbor_difference_ratio"] = geometry_smoothness
    summary["bias_variance_explained_by_router_pc8"] = bias_pc_fraction(8)
    summary["bias_variance_explained_by_router_pc32"] = bias_pc_fraction(32)

    pair_order = np.argsort(pairwise)[-10:][::-1]
    top_pairs = [
        {
            "expert_a": int(upper[0][index]),
            "expert_b": int(upper[1][index]),
            "cosine": finite_float(pairwise[index]),
        }
        for index in pair_order
    ]
    metric_values = {
        "router_norm": norms,
        "correction_bias": bias,
        "nearest_cosine": nearest_cosines,
        "top8_spectral_leverage": leverage,
    }
    outliers = _outlier_records(layer, metric_values, OUTLIER_THRESHOLD)
    details = {
        "layer": layer,
        "highest_norm_experts": [
            {"expert": int(index), "norm": finite_float(norms[index])}
            for index in np.argsort(norms)[-5:][::-1]
        ],
        "lowest_norm_experts": [
            {"expert": int(index), "norm": finite_float(norms[index])}
            for index in np.argsort(norms)[:5]
        ],
        "highest_correction_experts": [
            {"expert": int(index), "bias": finite_float(bias[index])}
            for index in np.argsort(bias)[-5:][::-1]
        ],
        "lowest_correction_experts": [
            {"expert": int(index), "bias": finite_float(bias[index])}
            for index in np.argsort(bias)[:5]
        ],
        "top_cosine_pairs": top_pairs,
        "highest_spectral_leverage_experts": [
            {"expert": int(index), "top8_leverage": finite_float(leverage[index])}
            for index in np.argsort(leverage)[-5:][::-1]
        ],
        "robust_outliers": outliers,
    }
    runtime = {
        "unit_vectors": unit,
        "nearest_indexes": nearest_indexes,
        "neighbor_indexes": neighbor_indexes,
        "cluster_labels": labels_by_count[recurrence_count],
        "pca_coordinates": eigenvectors[:, -2:] * np.sqrt(eigenvalues[-2:]),
        "bias": bias,
        "norms": norms,
    }
    return summary, details, runtime


def read_layer_arrays(
    payload_root: Path,
    gate_record: dict[str, Any],
    bias_record: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray]:
    hidden, experts = [int(value) for value in gate_record["shape"]]
    gate = np.memmap(
        payload_root / gate_record["payload_path"],
        dtype="<f4",
        mode="r",
        shape=(experts, hidden),
    )
    bias = np.fromfile(payload_root / bias_record["payload_path"], dtype="<f4")
    if bias.shape != (experts,):
        raise PackError(f"short correction vector for layer {gate_record['layer']}")
    return gate, bias


def _rank_correlation(left: np.ndarray, right: np.ndarray) -> float:
    return safe_spearman(left, right)


def adjacent_comparison(
    previous_layer: int,
    previous: dict[str, np.ndarray],
    layer: int,
    current: dict[str, np.ndarray],
) -> dict[str, Any]:
    same_expert_cosine = np.sum(
        previous["unit_vectors"] * current["unit_vectors"], axis=1, dtype=np.float64
    )
    previous_neighbors = previous["neighbor_indexes"]
    current_neighbors = current["neighbor_indexes"]
    jaccard = []
    for expert in range(previous_neighbors.shape[0]):
        left = set(int(value) for value in previous_neighbors[expert])
        right = set(int(value) for value in current_neighbors[expert])
        jaccard.append(len(left & right) / len(left | right))
    return {
        "previous_layer": previous_layer,
        "layer": layer,
        "cluster_ari_k16": adjusted_rand_index(
            previous["cluster_labels"], current["cluster_labels"]
        ),
        "nearest_expert_repeat_fraction": finite_float(
            np.mean(previous["nearest_indexes"] == current["nearest_indexes"])
        ),
        "top5_neighbor_jaccard_mean": finite_float(np.mean(jaccard)),
        "same_expert_direction_cosine_mean": finite_float(np.mean(same_expert_cosine)),
        "same_expert_direction_cosine_median": finite_float(
            np.median(same_expert_cosine)
        ),
        "router_norm_spearman": _rank_correlation(
            previous["norms"], current["norms"]
        ),
        "correction_bias_spearman": _rank_correlation(
            previous["bias"], current["bias"]
        ),
    }


def metric_range(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    values = np.asarray([row[metric] for row in rows], dtype=np.float64)
    minimum = int(np.argmin(values))
    maximum = int(np.argmax(values))
    result = {
        "minimum": finite_float(values[minimum]),
        "minimum_layer": int(rows[minimum]["layer"]),
        "median": finite_float(np.median(values)),
        "maximum": finite_float(values[maximum]),
        "maximum_layer": int(rows[maximum]["layer"]),
        "standard_deviation": finite_float(np.std(values)),
    }
    if np.all(values >= 0.0) or np.all(values <= 0.0):
        result["cross_layer_cv"] = (
            finite_float(np.std(values) / abs(np.mean(values)))
            if np.mean(values) != 0.0
            else 0.0
        )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise PackError(f"refusing to write empty CSV {path}")
    fields: list[str] = []
    seen = set()
    for row in rows:
        for field in row:
            if field not in seen:
                fields.append(field)
                seen.add(field)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _plot_overview(rows: list[dict[str, Any]], output: Path) -> None:
    layers = np.asarray([row["layer"] for row in rows])
    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    axes[0, 0].plot(layers, [row["router_norm_mean"] for row in rows], label="mean norm")
    axes[0, 0].fill_between(
        layers,
        [row["router_norm_p05"] for row in rows],
        [row["router_norm_p95"] for row in rows],
        alpha=0.25,
        label="p05-p95",
    )
    axes[0, 0].set_title("Router-vector norms")
    axes[0, 0].legend()

    axes[0, 1].plot(
        layers, [row["pairwise_cosine_median"] for row in rows], label="pair median"
    )
    axes[0, 1].plot(
        layers, [row["nearest_cosine_median"] for row in rows], label="NN median"
    )
    axes[0, 1].plot(
        layers, [row["nearest_cosine_max"] for row in rows], label="NN maximum"
    )
    axes[0, 1].set_title("Directional similarity")
    axes[0, 1].legend()

    axes[1, 0].plot(
        layers,
        [row["centered_spectral_effective_rank"] for row in rows],
        label="effective rank",
    )
    axes[1, 0].plot(
        layers,
        [row["centered_spectral_participation_rank"] for row in rows],
        label="participation rank",
    )
    axes[1, 0].set_title("Centered directional spectrum")
    axes[1, 0].legend()

    axes[1, 1].fill_between(
        layers,
        [row["correction_bias_min"] for row in rows],
        [row["correction_bias_max"] for row in rows],
        alpha=0.25,
        label="min-max",
    )
    axes[1, 1].plot(
        layers, [row["correction_bias_median"] for row in rows], label="median"
    )
    axes[1, 1].plot(
        layers, [row["correction_bias_std"] for row in rows], label="std"
    )
    axes[1, 1].set_title("Selection-correction bias")
    axes[1, 1].legend()
    for axis in axes.flat:
        axis.set_xlabel("routed layer")
        axis.grid(alpha=0.2)
    figure.savefig(output, dpi=160, metadata={"Software": "issue75 static analysis"})
    plt.close(figure)


def _plot_bias_relationships(rows: list[dict[str, Any]], output: Path) -> None:
    layers = [row["layer"] for row in rows]
    figure, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, constrained_layout=True)
    axes[0].plot(
        layers,
        [row["bias_variance_explained_by_router_pc8"] for row in rows],
        label="top 8 router PCs",
    )
    axes[0].plot(
        layers,
        [row["bias_variance_explained_by_router_pc32"] for row in rows],
        label="top 32 router PCs",
    )
    axes[0].axhline(8 / 895, color="gray", linestyle="--", linewidth=1, label="8/895 null share")
    axes[0].axhline(32 / 895, color="gray", linestyle=":", linewidth=1, label="32/895 null share")
    axes[0].set_ylabel("bias variance fraction")
    axes[0].legend(ncol=2)
    axes[0].grid(alpha=0.2)
    axes[1].plot(
        layers,
        [row["bias_router_norm_spearman"] for row in rows],
        label="bias vs norm",
    )
    axes[1].plot(
        layers,
        [row["bias_neighbor_mean_spearman"] for row in rows],
        label="bias vs neighbor mean",
    )
    axes[1].plot(
        layers,
        [row["bias_pair_geometry_spearman"] for row in rows],
        label="cosine vs bias proximity",
    )
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_xlabel("routed layer")
    axes[1].set_ylabel("Spearman correlation")
    axes[1].legend(ncol=3)
    axes[1].grid(alpha=0.2)
    figure.savefig(output, dpi=160, metadata={"Software": "issue75 static analysis"})
    plt.close(figure)


def _plot_cluster_recurrence(matrix: np.ndarray, output: Path) -> None:
    figure, axis = plt.subplots(figsize=(9, 8), constrained_layout=True)
    image = axis.imshow(matrix, origin="lower", cmap="coolwarm", vmin=-0.1, vmax=1.0)
    axis.set_title("Cross-layer adjusted Rand index (k=16 router clusters)")
    axis.set_xlabel("routed layer")
    axis.set_ylabel("routed layer")
    figure.colorbar(image, ax=axis, label="adjusted Rand index")
    figure.savefig(output, dpi=160, metadata={"Software": "issue75 static analysis"})
    plt.close(figure)


def _plot_representative_pca(
    representatives: list[int],
    runtimes: dict[int, dict[str, np.ndarray]],
    output: Path,
) -> None:
    columns = 2
    rows = math.ceil(len(representatives) / columns)
    figure, axes = plt.subplots(
        rows, columns, figsize=(11, 4.5 * rows), constrained_layout=True, squeeze=False
    )
    for axis, layer in zip(axes.flat, representatives):
        runtime = runtimes[layer]
        coordinates = runtime["pca_coordinates"]
        points = axis.scatter(
            coordinates[:, 1],
            coordinates[:, 0],
            c=runtime["bias"],
            cmap="coolwarm",
            s=10,
            alpha=0.8,
        )
        axis.set_title(f"Layer {layer}: centered router PCA, colored by bias")
        axis.set_xlabel("PC1 score")
        axis.set_ylabel("PC2 score")
        figure.colorbar(points, ax=axis, label="selection correction")
    for axis in axes.flat[len(representatives) :]:
        axis.remove()
    figure.savefig(output, dpi=160, metadata={"Software": "issue75 static analysis"})
    plt.close(figure)


def main() -> int:
    args = parse_args()
    manifest = load_json(args.manifest.resolve())
    inventory = load_json(args.inventory.resolve())
    config = load_json(args.config.resolve())
    records = validate_inventory(inventory, config)
    payload_root = args.payload_root.resolve()
    payload_validation = validate_payload_tree(payload_root, records)
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise PackError(f"output directory must be absent or empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = output_dir / "figures"
    figures_dir.mkdir()

    by_key = {
        (int(record["layer"]), str(record["semantic_role"])): record
        for record in records
    }
    layers = [int(layer) for layer in manifest["router"]["routed_layers"]]
    summaries: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    outliers: list[dict[str, Any]] = []
    adjacent: list[dict[str, Any]] = []
    runtimes: dict[int, dict[str, np.ndarray]] = {}
    previous_layer = None
    previous_runtime = None
    for layer in layers:
        gate_record = by_key[(layer, "router_projection_weight")]
        bias_record = by_key[(layer, "selection_correction_bias")]
        gate, bias = read_layer_arrays(payload_root, gate_record, bias_record)
        summary, layer_details, runtime = analyze_layer(layer, gate, bias)
        summaries.append(summary)
        outliers.extend(layer_details.pop("robust_outliers"))
        details.append(layer_details)
        runtimes[layer] = runtime
        if previous_runtime is not None and previous_layer is not None:
            adjacent.append(adjacent_comparison(previous_layer, previous_runtime, layer, runtime))
        previous_layer = layer
        previous_runtime = runtime

    labels = [runtimes[layer]["cluster_labels"] for layer in layers]
    cluster_recurrence = np.eye(len(layers), dtype=np.float64)
    for left in range(len(layers)):
        for right in range(left + 1, len(layers)):
            value = adjusted_rand_index(labels[left], labels[right])
            cluster_recurrence[left, right] = value
            cluster_recurrence[right, left] = value

    top_favored = Counter()
    top_disfavored = Counter()
    for layer in layers:
        bias = runtimes[layer]["bias"]
        top_favored.update(int(value) for value in np.argsort(bias)[-16:])
        top_disfavored.update(int(value) for value in np.argsort(bias)[:16])

    metrics = (
        "router_norm_mean",
        "router_norm_cv",
        "pairwise_cosine_median",
        "nearest_cosine_median",
        "nearest_cosine_max",
        "direction_centroid_norm",
        "centered_spectral_effective_rank",
        "cluster_best_silhouette",
        "correction_bias_std",
        "correction_bias_min",
        "correction_bias_max",
        "bias_router_norm_spearman",
        "bias_neighbor_mean_spearman",
        "bias_pair_geometry_spearman",
        "bias_neighbor_difference_ratio",
        "bias_variance_explained_by_router_pc32",
    )
    off_diagonal = cluster_recurrence[np.triu_indices(len(layers), 1)]
    recurrence_for_max = cluster_recurrence.copy()
    np.fill_diagonal(recurrence_for_max, -np.inf)
    recurrence_max_indexes = np.unravel_index(
        int(np.argmax(recurrence_for_max)), recurrence_for_max.shape
    )
    global_top_pairs = sorted(
        (
            {"layer": item["layer"], **pair}
            for item in details
            for pair in item["top_cosine_pairs"]
        ),
        key=lambda item: item["cosine"],
        reverse=True,
    )[:25]
    representative_layers = []
    for candidate in (
        layers[0],
        int(max(summaries, key=lambda row: row["cluster_best_silhouette"])["layer"]),
        int(min(summaries, key=lambda row: row["nearest_cosine_median"])["layer"]),
        int(max(summaries, key=lambda row: row["bias_variance_explained_by_router_pc32"])["layer"]),
        layers[-1],
    ):
        if candidate not in representative_layers:
            representative_layers.append(candidate)

    adjacent_metric_means = {
        metric: finite_float(np.mean([row[metric] for row in adjacent]))
        for metric in (
            "cluster_ari_k16",
            "nearest_expert_repeat_fraction",
            "top5_neighbor_jaccard_mean",
            "same_expert_direction_cosine_mean",
            "router_norm_spearman",
            "correction_bias_spearman",
        )
    }
    outlier_counts = Counter(item["metric"] for item in outliers)

    summary_document = {
        "schema_version": "kimi-k3-static-router-analysis-v1",
        "analysis_scope": "static router function f(h); no hidden-state distribution or runtime traces",
        "input": {
            "pack_id": manifest["pack_id"],
            "model": manifest["model"],
            "source_artifact_identity_manifest_sha256": manifest["source_artifact"][
                "identity_manifest_sha256"
            ],
            "release_tag": manifest["release"]["tag"],
            "ordered_assets": [
                {
                    "filename": asset["filename"],
                    "compressed_bytes": asset["compressed_bytes"],
                    "sha256": asset["sha256"],
                }
                for asset in manifest["release"]["ordered_assets"]
            ],
            "payload_validation": payload_validation,
        },
        "method": {
            "pairwise_similarity": "exact all-pairs cosine over normalized 7168-element router vectors",
            "spectrum": "exact eigendecomposition of the centered 896x896 cosine Gram matrix",
            "clustering": "average-linkage agglomeration on exact cosine distance; exact silhouette for k=2,4,8,16,32",
            "bias_geometry": "rank correlations, exact pairwise bias-proximity association, top-PC variance projection, and five-nearest-neighbor smoothness",
            "outlier_rule": f"absolute median/MAD robust z-score >= {OUTLIER_THRESHOLD}",
            "cluster_recurrence": "adjusted Rand index over expert IDs for k=16 partitions",
            "limitations": [
                "router vectors are analyzed without samples from P(h)",
                "correction effects on selected experts depend on hidden-state logits",
                "cluster partitions are descriptive and do not establish functional specialization",
                "cluster ARI detects recurrence of identity-aligned expert memberships, not motifs preserved under an unknown expert permutation",
            ],
        },
        "software": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "matplotlib": matplotlib.__version__,
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
        },
        "layer_count": len(layers),
        "expert_count_per_layer": int(manifest["router"]["experts_per_layer"]),
        "cross_layer_metric_ranges": {
            metric: metric_range(summaries, metric) for metric in metrics
        },
        "cluster_recurrence_k16": {
            "all_pair_mean_ari": finite_float(np.mean(off_diagonal)),
            "all_pair_median_ari": finite_float(np.median(off_diagonal)),
            "all_pair_max_ari": finite_float(np.max(off_diagonal)),
            "adjacent_mean_ari": finite_float(
                np.mean([row["cluster_ari_k16"] for row in adjacent])
            ),
            "maximum_pair": {
                "layer_a": layers[recurrence_max_indexes[0]],
                "layer_b": layers[recurrence_max_indexes[1]],
                "ari": finite_float(recurrence_for_max[recurrence_max_indexes]),
            },
        },
        "adjacent_layer_metric_means": adjacent_metric_means,
        "random_id_recurrence_references": {
            "nearest_expert_repeat_fraction": 1.0
            / (int(manifest["router"]["experts_per_layer"]) - 1),
            "top5_neighbor_jaccard_approximation": (
                25.0 / (int(manifest["router"]["experts_per_layer"]) - 1)
            )
            / (
                10.0
                - 25.0 / (int(manifest["router"]["experts_per_layer"]) - 1)
            ),
        },
        "adjacent_layer_comparisons": adjacent,
        "redundancy_summary": {
            "total_expert_pairs": len(layers)
            * int(manifest["router"]["experts_per_layer"])
            * (int(manifest["router"]["experts_per_layer"]) - 1)
            // 2,
            "pair_counts_by_cosine_threshold": {
                str(threshold): int(
                    sum(
                        row[f"cosine_pairs_ge_{str(threshold).replace('.', 'p')}"]
                        for row in summaries
                    )
                )
                for threshold in REDUNDANCY_THRESHOLDS
            },
            "layers_with_any_pair_ge_0p7": [
                int(row["layer"])
                for row in summaries
                if row["cosine_pairs_ge_0p7"] > 0
            ],
        },
        "cluster_tendency_summary": {
            "layers_with_best_silhouette_ge_0p05": int(
                sum(row["cluster_best_silhouette"] >= 0.05 for row in summaries)
            ),
            "layers_with_best_silhouette_ge_0p10": int(
                sum(row["cluster_best_silhouette"] >= 0.10 for row in summaries)
            ),
        },
        "bias_geometry_summary": {
            "layers_with_positive_pair_geometry_association": int(
                sum(row["bias_pair_geometry_spearman"] > 0.0 for row in summaries)
            ),
            "layers_with_pc32_bias_fraction_ge_0p5": int(
                sum(
                    row["bias_variance_explained_by_router_pc32"] >= 0.5
                    for row in summaries
                )
            ),
        },
        "global_top_cosine_pairs": global_top_pairs,
        "robust_outlier_count": len(outliers),
        "robust_outlier_counts_by_metric": dict(sorted(outlier_counts.items())),
        "representative_layers": representative_layers,
        "recurring_correction_extremes": {
            "chance_expected_top16_layer_count_per_expert": len(layers)
            * 16
            / int(manifest["router"]["experts_per_layer"]),
            "top16_favored_most_common": [
                {"expert": expert, "layer_count": count}
                for expert, count in top_favored.most_common(20)
            ],
            "top16_disfavored_most_common": [
                {"expert": expert, "layer_count": count}
                for expert, count in top_disfavored.most_common(20)
            ],
        },
        "reproduction_command": (
            "python3 scripts/issue75/analyze_router_geometry.py "
            "--manifest results/2026-08-10/issue75-router-pack/manifest.json "
            "--inventory results/2026-08-10/issue75-router-pack/tensors.json "
            "--config results/2026-08-10/issue75-router-pack/extraction-config.json "
            "--payload-root <verified-extracted-pack> --output-dir <new-empty-output-dir>"
        ),
    }

    write_json(output_dir / "analysis-summary.json", summary_document)
    write_json(
        output_dir / "per-layer.json",
        {"schema_version": "kimi-k3-static-router-per-layer-v1", "layers": details},
    )
    write_csv(output_dir / "per-layer.csv", summaries)
    write_csv(output_dir / "adjacent-layers.csv", adjacent)
    if outliers:
        write_csv(output_dir / "expert-outliers.csv", outliers)
    _plot_overview(summaries, figures_dir / "router-geometry-overview.png")
    _plot_bias_relationships(summaries, figures_dir / "bias-geometry-relationships.png")
    _plot_cluster_recurrence(
        cluster_recurrence, figures_dir / "cluster-recurrence-k16.png"
    )
    _plot_representative_pca(
        representative_layers, runtimes, figures_dir / "representative-router-pca.png"
    )
    print(json.dumps(summary_document, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

#!/usr/bin/env python3
"""Shared deterministic helpers for Phase 9 decision-driving evidence."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable


GIB = 1024**3


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    return {"path": str(path), "size": path.stat().st_size, "sha256": sha256_file(path)}


def nearest_rank(values: Iterable[float | int], percentile: float) -> float | int | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(percentile*len(ordered)) - 1))
    return ordered[index]


def distribution(values: Iterable[float | int]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "count": len(ordered), "min": ordered[0] if ordered else None,
        "p50": nearest_rank(ordered, 0.50), "p95": nearest_rank(ordered, 0.95),
        "p99": nearest_rank(ordered, 0.99), "max": ordered[-1] if ordered else None,
    }


def legal_budget_grid(
        working_set: int, footprint: int, current: int, safe_ceiling: int) -> list[dict[str, Any]]:
    if working_set <= 0 or footprint <= 0 or safe_ceiling < 0:
        raise ValueError("invalid budget-grid inputs")
    candidates = [
        ("0.50W", working_set, 2, "down"), ("0.75W", 3*working_set, 4, "down"),
        ("W-one-expert", max(0, working_set - footprint), 1, "down"),
        ("W", working_set, 1, "down"), ("W+one-expert", working_set + footprint, 1, "up"),
        ("1.25W", 5*working_set, 4, "down"), ("1.50W", 3*working_set, 2, "down"),
        ("2.00W", 2*working_set, 1, "down"), ("current", current, 1, "down"),
        ("safe-ceiling", safe_ceiling, 1, "down"),
    ]
    by_bytes: dict[int, dict[str, Any]] = {}
    for label, numerator, denominator, direction in candidates:
        raw = numerator // denominator
        if direction == "up":
            effective = ((raw + footprint - 1)//footprint)*footprint
        else:
            effective = (raw//footprint)*footprint
        disposition = "available" if effective <= safe_ceiling else "unavailable-by-headroom"
        entry = by_bytes.setdefault(effective, {
            "effective_bytes": effective, "labels": [], "rounding": direction,
            "rounding_remainder_bytes": abs(raw - effective), "disposition": disposition,
        })
        entry["labels"].append(label)
        if disposition != "available": entry["disposition"] = disposition
    return [by_bytes[value] for value in sorted(by_bytes)]


def host_safe_ceiling(mem_total: int, mem_available: int, obligations: int, operator_ceiling: int | None) -> dict[str, int]:
    reserve = max(8*GIB, (mem_total + 7)//8)
    available_ceiling = max(0, mem_available - reserve - obligations)
    ceiling = min(available_ceiling, operator_ceiling) if operator_ceiling is not None else available_ceiling
    return {"physical_bytes": mem_total, "available_bytes": mem_available, "reserve_bytes": reserve,
            "declared_obligations_bytes": obligations, "operator_ceiling_bytes": operator_ceiling,
            "safe_ceiling_bytes": max(0, ceiling)}


def paired_interval(candidate: list[float], baseline: list[float]) -> dict[str, Any]:
    if len(candidate) != len(baseline) or len(candidate) < 2:
        raise ValueError("paired samples require equal lengths of at least two")
    differences = [left - right for left, right in zip(candidate, baseline)]
    mean = statistics.fmean(differences)
    standard_error = statistics.stdev(differences)/math.sqrt(len(differences))
    # Fixed two-sided 95% Student-t critical values for the mandatory n>=10 evidence.
    critical = {9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160,
                14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
                19: 2.093, 20: 2.086, 24: 2.064, 29: 2.045}.get(len(differences) - 1, 1.96)
    half = critical*standard_error
    return {"n": len(differences), "mean_difference": mean, "ci95_low": mean - half,
            "ci95_high": mean + half, "differences": differences, "student_t_critical": critical}

#!/usr/bin/env python3
"""Compare paired Phase 13.6 exact and cache-aware quality traces."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

import numpy as np


MAGIC = b"P13QTR1\n"
HEADER_SIZE = struct.Struct("<I")
RECORD_HEADER = struct.Struct("<B3xIiiIQ")
RECORD_TYPES = {1: "moe_output", 2: "hidden_state", 3: "logits"}


class QualityTraceError(ValueError):
    pass


@dataclass(frozen=True)
class Record:
    kind: str
    step: int
    layer: int
    target_token: int
    n_tokens: int
    values: np.ndarray

    @property
    def key(self) -> tuple[str, int, int, int, int, int]:
        return (self.kind, self.step, self.layer, self.target_token,
                self.n_tokens, int(self.values.size))


class TraceReader:
    def __init__(self, path: Path):
        self.path = path
        self.source: BinaryIO = path.open("rb")
        if self.source.read(len(MAGIC)) != MAGIC:
            raise QualityTraceError(f"{path}: invalid quality-trace magic")
        encoded_size = self.source.read(HEADER_SIZE.size)
        if len(encoded_size) != HEADER_SIZE.size:
            raise QualityTraceError(f"{path}: truncated quality-trace header size")
        header_size, = HEADER_SIZE.unpack(encoded_size)
        encoded_header = self.source.read(header_size)
        if len(encoded_header) != header_size:
            raise QualityTraceError(f"{path}: truncated quality-trace header")
        try:
            self.metadata = json.loads(encoded_header)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualityTraceError(f"{path}: invalid quality-trace metadata") from exc
        if self.metadata.get("schema_version") != "phase13-quality-trace-v1":
            raise QualityTraceError(f"{path}: unsupported quality-trace schema")

    def records(self) -> Iterator[Record]:
        while True:
            encoded_header = self.source.read(RECORD_HEADER.size)
            if not encoded_header:
                return
            if len(encoded_header) != RECORD_HEADER.size:
                raise QualityTraceError(f"{self.path}: truncated record header")
            record_type, step, layer, target, n_tokens, count = RECORD_HEADER.unpack(encoded_header)
            if record_type not in RECORD_TYPES or count == 0 or count > 2**32:
                raise QualityTraceError(f"{self.path}: invalid record header")
            encoded_values = self.source.read(count*4)
            if len(encoded_values) != count*4:
                raise QualityTraceError(f"{self.path}: truncated record payload")
            values = np.frombuffer(encoded_values, dtype="<f4")
            if not np.isfinite(values).all():
                raise QualityTraceError(f"{self.path}: non-finite trace value")
            yield Record(RECORD_TYPES[record_type], step, layer, target, n_tokens, values)

    def close(self) -> None:
        self.source.close()


def finite(value: float, label: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise QualityTraceError(f"non-finite {label}")
    return value


def vector_metrics(exact: np.ndarray, changed: np.ndarray) -> dict[str, float]:
    exact64 = exact.astype(np.float64)
    changed64 = changed.astype(np.float64)
    exact_norm = math.sqrt(float(np.dot(exact64, exact64)))
    changed_norm = math.sqrt(float(np.dot(changed64, changed64)))
    difference = changed64 - exact64
    difference_norm = math.sqrt(float(np.dot(difference, difference)))
    if exact_norm == 0.0:
        relative_l2 = 0.0 if difference_norm == 0.0 else math.inf
        norm_ratio = 1.0 if changed_norm == 0.0 else math.inf
    else:
        relative_l2 = difference_norm/exact_norm
        norm_ratio = changed_norm/exact_norm
    if exact_norm == 0.0 or changed_norm == 0.0:
        cosine = 1.0 if exact_norm == changed_norm else 0.0
    else:
        cosine = float(np.dot(exact64, changed64))/(exact_norm*changed_norm)
        cosine = min(1.0, max(-1.0, cosine))
    return {
        "relative_l2": finite(relative_l2, "relative L2"),
        "cosine_similarity": finite(cosine, "cosine similarity"),
        "norm_ratio": finite(norm_ratio, "norm ratio"),
    }


def predictive_metrics(exact: Record, changed: Record, top_k: int) -> dict[str, object]:
    exact_logits = exact.values.astype(np.float64)
    changed_logits = changed.values.astype(np.float64)
    if exact.target_token != changed.target_token or not 0 <= exact.target_token < exact_logits.size:
        raise QualityTraceError("paired logits do not use the same valid reference token")
    exact_max = float(np.max(exact_logits))
    changed_max = float(np.max(changed_logits))
    exact_log_z = exact_max + math.log(float(np.exp(exact_logits - exact_max).sum()))
    changed_log_z = changed_max + math.log(float(np.exp(changed_logits - changed_max).sum()))
    exact_log_p = exact_logits - exact_log_z
    changed_log_p = changed_logits - changed_log_z
    exact_p = np.exp(exact_log_p)
    changed_p = np.exp(changed_log_p)
    log_mixture = np.logaddexp(exact_log_p, changed_log_p) - math.log(2.0)
    kl = float(np.sum(exact_p*(exact_log_p - changed_log_p)))
    js = 0.5*float(np.sum(exact_p*(exact_log_p - log_mixture))) + \
        0.5*float(np.sum(changed_p*(changed_log_p - log_mixture)))
    exact_top1 = int(np.argmax(exact_logits))
    changed_top1 = int(np.argmax(changed_logits))
    effective_k = min(top_k, int(exact_logits.size))
    exact_topk = np.argpartition(exact_logits, -effective_k)[-effective_k:]
    changed_topk = np.argpartition(changed_logits, -effective_k)[-effective_k:]
    overlap = len(set(map(int, exact_topk)).intersection(map(int, changed_topk)))/effective_k
    target = exact.target_token
    exact_nll = -float(exact_log_p[target])
    changed_nll = -float(changed_log_p[target])
    return {
        "step": exact.step,
        "target_token": target,
        "kl_exact_to_changed": finite(kl, "KL"),
        "js_divergence": finite(js, "JS"),
        "exact_top1": exact_top1,
        "changed_top1": changed_top1,
        "top1_agreement": exact_top1 == changed_top1,
        "top_k": effective_k,
        "top_k_overlap": finite(overlap, "top-k overlap"),
        "exact_reference_nll": finite(exact_nll, "exact NLL"),
        "changed_reference_nll": finite(changed_nll, "changed NLL"),
        "delta_reference_nll": finite(changed_nll - exact_nll, "NLL delta"),
    }


def distribution(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "mean": finite(float(np.mean(array)), "mean"),
        "p50": finite(float(np.quantile(array, 0.50)), "p50"),
        "p95": finite(float(np.quantile(array, 0.95)), "p95"),
        "max": finite(float(np.max(array)), "max"),
    }


def summarize_internal(records: list[dict[str, object]], kind: str) -> dict[str, object]:
    selected = [record for record in records if record["kind"] == kind]
    return {
        "records": len(selected),
        "relative_l2": distribution([float(record["relative_l2"]) for record in selected]),
        "cosine_similarity": distribution([float(record["cosine_similarity"]) for record in selected]),
        "norm_ratio": distribution([float(record["norm_ratio"]) for record in selected]),
    }


def summarize_predictive(records: list[dict[str, object]]) -> dict[str, object]:
    return {
        "steps": len(records),
        "kl_exact_to_changed": distribution([float(record["kl_exact_to_changed"]) for record in records]),
        "js_divergence": distribution([float(record["js_divergence"]) for record in records]),
        "top1_agreement_fraction": finite(
            sum(bool(record["top1_agreement"]) for record in records)/len(records),
            "top1 agreement") if records else 0.0,
        "top_k_overlap": distribution([float(record["top_k_overlap"]) for record in records]),
        "exact_reference_nll": distribution([float(record["exact_reference_nll"]) for record in records]),
        "changed_reference_nll": distribution([float(record["changed_reference_nll"]) for record in records]),
        "delta_reference_nll": distribution([float(record["delta_reference_nll"]) for record in records]),
    }


def load_capture(path: Path) -> dict[str, object]:
    with path.open() as source:
        result = json.load(source)
    if result.get("schema_version") != "phase13-exact-topm-capture-v1" or not result.get("routes"):
        raise QualityTraceError(f"{path}: invalid route capture")
    return result


def compare_routes(exact_path: Path, changed_path: Path) -> tuple[dict[str, object], tuple[int, int] | None]:
    exact = load_capture(exact_path)
    changed = load_capture(changed_path)
    if exact.get("prompt_ids") != changed.get("prompt_ids") or \
            exact.get("generated_ids") != changed.get("generated_ids"):
        raise QualityTraceError("route captures do not use the same prompt/reference sequence")
    exact_routes = exact["routes"]
    changed_routes = changed["routes"]
    if len(exact_routes) != len(changed_routes):
        raise QualityTraceError("route captures have different record counts")
    decode_ubatches: dict[int, int] = {}
    for route in changed_routes:
        if route["phase"] == "DECODE" and route["ubatch_ordinal"] not in decode_ubatches:
            decode_ubatches[route["ubatch_ordinal"]] = len(decode_ubatches) + 1
    decisions = 0
    decode_decisions = 0
    decode_expert_slots = 0
    intentional_decisions = 0
    intentional_swaps = 0
    induced_decisions = 0
    final_divergent_decisions = 0
    cumulative_regret = 0.0
    first: tuple[int, int] | None = None
    for exact_route, changed_route in zip(exact_routes, changed_routes):
        identity = ("phase", "layer", "n_tokens", "n_expert_used", "n_candidates", "positions")
        if any(exact_route[field] != changed_route[field] for field in identity):
            raise QualityTraceError("route capture record identity mismatch")
        top_k = changed_route["n_expert_used"]
        candidate_count = changed_route["n_candidates"]
        for token in range(changed_route["n_tokens"]):
            decisions += 1
            if changed_route["phase"] == "DECODE":
                decode_decisions += 1
                decode_expert_slots += top_k
            exact_selected = exact_route["selected_experts"][token*top_k:(token + 1)*top_k]
            changed_selected = changed_route["selected_experts"][token*top_k:(token + 1)*top_k]
            exact_intrinsic = exact_route["candidate_experts"][token*candidate_count:token*candidate_count + top_k]
            changed_candidates = changed_route["candidate_experts"][token*candidate_count:(token + 1)*candidate_count]
            changed_scores = changed_route["candidate_selection_scores"][
                token*candidate_count:(token + 1)*candidate_count]
            changed_intrinsic = changed_candidates[:top_k]
            if exact_selected != exact_intrinsic:
                raise QualityTraceError("exact capture contains changed routing")
            intentional = changed_selected != changed_intrinsic
            induced = changed_intrinsic != exact_intrinsic
            final_divergent = changed_selected != exact_selected
            intentional_decisions += intentional
            induced_decisions += induced
            final_divergent_decisions += final_divergent
            if intentional:
                if changed_route["phase"] != "DECODE":
                    raise QualityTraceError("prefill rerouting is not permitted")
                if first is None:
                    first = (decode_ubatches[changed_route["ubatch_ordinal"]], changed_route["layer"])
                for rank, selected in enumerate(changed_selected):
                    if selected == changed_intrinsic[rank]:
                        continue
                    try:
                        candidate_rank = changed_candidates.index(selected)
                    except ValueError as exc:
                        raise QualityTraceError("changed expert is outside retained candidates") from exc
                    regret = changed_scores[rank] - changed_scores[candidate_rank]
                    if regret < 0 or not math.isfinite(regret):
                        raise QualityTraceError("invalid changed-route regret")
                    intentional_swaps += 1
                    cumulative_regret += regret
    return ({
        "decisions": decisions,
        "decode_decisions": decode_decisions,
        "intentional_changed_decisions": intentional_decisions,
        "intentional_changed_fraction": intentional_decisions/decisions,
        "intentional_swaps": intentional_swaps,
        "intentional_changed_expert_slots": intentional_swaps,
        "intentional_changed_expert_slot_fraction":
            intentional_swaps/decode_expert_slots if decode_expert_slots else 0.0,
        "mean_regret_per_swap": cumulative_regret/intentional_swaps if intentional_swaps else 0.0,
        "cumulative_regret": cumulative_regret,
        "induced_exact_topk_divergent_decisions": induced_decisions,
        "induced_exact_topk_divergent_fraction": induced_decisions/decisions,
        "final_route_divergent_decisions": final_divergent_decisions,
        "final_route_divergent_fraction": final_divergent_decisions/decisions,
        "first_intentional_swap": None if first is None else {"step": first[0], "layer": first[1]},
    }, first)


def compare_traces(exact_path: Path, changed_path: Path, top_k: int) -> tuple[dict[str, object], dict[tuple[int, int], dict[str, object]]]:
    exact = TraceReader(exact_path)
    changed = TraceReader(changed_path)
    internal: list[dict[str, object]] = []
    predictive: list[dict[str, object]] = []
    moe_by_key: dict[tuple[int, int], dict[str, object]] = {}
    try:
        if exact.metadata.get("prompt_ids") != changed.metadata.get("prompt_ids"):
            raise QualityTraceError("quality traces use different prompts")
        for exact_record, changed_record in itertools.zip_longest(exact.records(), changed.records()):
            if exact_record is None or changed_record is None or exact_record.key != changed_record.key:
                raise QualityTraceError("quality trace record sequence mismatch")
            if exact_record.kind == "logits":
                predictive.append(predictive_metrics(exact_record, changed_record, top_k))
                continue
            metrics: dict[str, object] = {
                "kind": exact_record.kind,
                "step": exact_record.step,
                "layer": exact_record.layer,
                "n_tokens": exact_record.n_tokens,
                "n_values": int(exact_record.values.size),
                **vector_metrics(exact_record.values, changed_record.values),
            }
            internal.append(metrics)
            if exact_record.kind == "moe_output":
                moe_by_key[(exact_record.step, exact_record.layer)] = metrics
    finally:
        exact.close()
        changed.close()
    if not predictive:
        raise QualityTraceError("quality traces contain no predictive records")
    return ({
        "exact_metadata": exact.metadata,
        "changed_metadata": changed.metadata,
        "moe_output": summarize_internal(internal, "moe_output"),
        "hidden_state": summarize_internal(internal, "hidden_state"),
        "predictive": summarize_predictive(predictive),
        "internal_records": internal,
        "predictive_records": predictive,
    }, moe_by_key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exact-trace", type=Path, required=True)
    parser.add_argument("--changed-trace", type=Path, required=True)
    parser.add_argument("--exact-routes", type=Path)
    parser.add_argument("--changed-routes", type=Path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.top_k <= 0 or (args.exact_routes is None) != (args.changed_routes is None):
        parser.error("top-k must be positive and route captures must be supplied as a pair")
    trace_comparison, moe_by_key = compare_traces(args.exact_trace, args.changed_trace, args.top_k)
    routing = None
    local_moe = None
    if args.exact_routes is not None:
        routing, first = compare_routes(args.exact_routes, args.changed_routes)
        if first is not None:
            local_moe = moe_by_key.get(first)
            if local_moe is None:
                raise QualityTraceError("first intentional swap has no paired MoE trace")
    result = {
        "schema_version": "phase13-quality-comparison-v1",
        "status": "pass",
        "exact_trace": str(args.exact_trace),
        "changed_trace": str(args.changed_trace),
        **trace_comparison,
        "routing": routing,
        "local_moe_at_first_intentional_swap": local_moe,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as destination:
        json.dump(result, destination, indent=2, sort_keys=True, allow_nan=False)
        destination.write("\n")
    print(f"PHASE13_QUALITY_ANALYSIS status=pass output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

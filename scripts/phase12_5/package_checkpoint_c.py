#!/usr/bin/env python3
"""Package the accepted Phase 12.5 Checkpoint C captures for independent review."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path


PROJECT = "f589f6782009480257abbcf844a3c8db3499a221"
NESTED = "d67525814502370a554ca851a0057bf4b8a735f8"
PROVIDER_PROJECT = "d4665e95f6bdb8ed2647c5228d7e311357411b0b"
PROVIDER_NESTED = "90347c79c8348ecf2199a419ee4112ea18238d66"
MODEL_REVISION = "85ce4196ab6e82852e25dfec2b7e2beaae56f5f1"
RELEASE_TAG = "phase12-5-issue54-v2"
PROVIDER_RELEASE_TAG = "phase12-5-issue54-v1"
RELEASE_ROOT = (
    "https://github.com/murillo128/k3-out-of-core/releases/download/"
    f"{RELEASE_TAG}"
)
TRACE_PROCESSOR = {
    "path": "trace_processor_shell",
    "size": 11313840,
    "sha256": "4b86aaf94d2ba9f517ddee817aaae7c12089d6ce078924e708d38f9cf0c0b517",
}
CONFIG = {
    "path": "full-stack-split-1g.pbtxt",
    "size": 2520,
    "sha256": "8c55bee5e8db49bbb33ab8fa901d051e23ff22750bbdb48118308086d0b66f65",
}

CASES = (
    {
        "directory": "provider-positional-selected-r12",
        "name": "provider-positional-selected",
        "role": "decision-driving",
        "raw_size": 537180776,
        "raw_sha256": "49b595ed3ca78eca8d3768d3a4381a91276abc41ea7115c45589e02a995d3b4b",
        "compressed_size": 35565917,
        "compressed_sha256": "4d4004c9e3a686a56e036d6a2baacb475b7cd7998c33c751de5dad4d06ef346b",
    },
    {
        "directory": "provider-positional-selected-r16",
        "name": "provider-positional-selected",
        "role": "decision-driving",
        "raw_size": 533867829,
        "raw_sha256": "aed3ee83321a38d9bc5c9727d39949ff2734f08420fa721932b9d95539cf217e",
        "compressed_size": 35156539,
        "compressed_sha256": "a00db556e4b6d8c9559bf4af056581d6c380b09428458965dd56e88763f68e14",
    },
    {
        "directory": "provider-buffered-io-uring-r12",
        "name": "provider-buffered-io-uring",
        "role": "decision-driving",
        "raw_size": 575019008,
        "raw_sha256": "50ae21910c3c28dd26f40fa4eff0b902ef2ad55f2f38ac0f1bff640d5f17a368",
        "compressed_size": 40196306,
        "compressed_sha256": "358cb5182f332022e15a1083f3bbb05dfb0893d2d6b8b01707beebdfb2bc95b4",
    },
    {
        "directory": "provider-cold64-positional-r2",
        "name": "provider-cold64-positional",
        "role": "decision-driving",
        "raw_size": 1609049500,
        "raw_sha256": "af69e5fc430c1074fbbad77d45dcc2c9a88c9ba3c113b37975e086b35a1234e1",
        "compressed_size": 86718318,
        "compressed_sha256": "e5c2de8b60d64e96304af4499c01754da60a9f5e6c46ac26c225e431b767abac",
    },
    {
        "directory": "fit-control-r12",
        "name": "fit-control",
        "role": "control",
        "raw_size": 1188116132,
        "raw_sha256": "1b6c40309ba922c980dc427263f26eb13eea5648482b98bbe476fa478bd12535",
        "compressed_size": 208111114,
        "compressed_sha256": "6df372d59c6ab005167aed45eb09e6401292da46571840c58b862f541ce75ab7",
    },
    {
        "directory": "cpu-moe-control-r3",
        "name": "cpu-moe-control",
        "role": "control",
        "raw_size": 1156440901,
        "raw_sha256": "fa412d563f293ec06fdf55592f83872a975c4084c19354799847aa7304d03ef8",
        "compressed_size": 203919035,
        "compressed_sha256": "90e55555776a1f588a0f884be1aabb76f4433f82d5edfbea38400b1ed317da1f",
    },
)

SPLITS = (
    ("DeepSeek-V4-Flash-UD-Q3_K_XL-00001-of-00004.gguf", 5256864,
     "951458825be77e285141adb8a71bcb72abf26ab33a39bbdead9eb7d73ef7b396"),
    ("DeepSeek-V4-Flash-UD-Q3_K_XL-00002-of-00004.gguf", 49350774208,
     "63c873e288a2ab222bf902cfda53105cdf37fd714f0aa939070f8106fdda3242"),
    ("DeepSeek-V4-Flash-UD-Q3_K_XL-00003-of-00004.gguf", 49189072672,
     "9c2c9878beb485d3553fe272edcc13f5959c31ec371f5df947fa0514b83cd4dc"),
    ("DeepSeek-V4-Flash-UD-Q3_K_XL-00004-of-00004.gguf", 30903139232,
     "2deb9faaa22707d4af983955f517f961d7e939e169a11ec129066186918a13ea"),
)


def read_json(path: Path) -> object:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_identity(path: Path, display_path: str | None = None) -> dict[str, object]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return {"path": display_path or path.name, "size": path.stat().st_size,
            "sha256": digest.hexdigest()}


def sanitize(value: object, case_directory: str) -> object:
    replacements = (
        (f"/dev/shm/k3-issue54-c/{case_directory}", f"<CAPTURE_ROOT>/{case_directory}"),
        ("/workspace/models/DeepSeek-V4-Flash-85ce4196-UD-Q3_K_XL", "<DEEPSEEK_ARTIFACT_ROOT>"),
        ("/workspace/builds/k3-issue54-tools", "<PERFETTO_TOOLS>"),
        ("/workspace/builds/k3-issue54-on", "<TRACE_BUILD>"),
        ("/workspace/k3-out-of-core", "<CHECKOUT>"),
    )
    if isinstance(value, str):
        for source, replacement in replacements:
            value = value.replace(source, replacement)
        return value
    if isinstance(value, list):
        return [sanitize(item, case_directory) for item in value]
    if isinstance(value, dict):
        return {key: sanitize(item, case_directory) for key, item in value.items()}
    return value


def percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)]


def critical_summary(query: dict[str, object]) -> dict[str, object]:
    rows = query["outputs"]["token_critical_path"]
    wall = sum(row["wall_ns"] for row in rows)
    names = ("storage", "transfer", "cuda_sync", "cuda_memcpy", "cuda_kernel",
             "scheduler", "cache", "provider_residual", "unattributed")
    shares = {name: sum(row[f"{name}_ns"] for row in rows) / wall for name in names}
    overlap = query["outputs"]["overlap_and_idle"]
    storage_rows = query["outputs"]["storage_queue_service"]
    result: dict[str, object] = {
        "rows": len(rows),
        "wall_sum_ns": wall,
        "p50_wall_ns": percentile([row["wall_ns"] for row in rows], 0.50),
        "p95_wall_ns": percentile([row["wall_ns"] for row in rows], 0.95),
        "p99_wall_ns": percentile([row["wall_ns"] for row in rows], 0.99),
        "nonoverlap_shares": shares,
        "gpu_busy_share": sum(row["gpu_busy_ns"] for row in overlap) / wall,
        "gpu_storage_overlap_share": sum(row["gpu_storage_overlap_ns"] for row in overlap) / wall,
        "gpu_storage_idle_share": sum(row["gpu_storage_idle_ns"] for row in overlap) / wall,
        "cpu_runnable_delay_ns_across_threads": sum(
            row["runnable_delay_ns"] for row in query["outputs"]["cpu_runqueue"]),
    }
    if storage_rows:
        result["storage_requests"] = len(storage_rows)
        result["p95_storage_queue_wait_ns"] = percentile(
            [row["queue_wait_ns"] for row in storage_rows], 0.95)
        result["p99_storage_queue_wait_ns"] = percentile(
            [row["queue_wait_ns"] for row in storage_rows], 0.99)
        result["p95_operation_service_wall_ns"] = percentile(
            [row["operation_service_wall_ns"] for row in storage_rows], 0.95)
        result["p99_operation_service_wall_ns"] = percentile(
            [row["operation_service_wall_ns"] for row in storage_rows], 0.99)
    return result


def duration_ns(capture: dict[str, object]) -> int:
    start = datetime.fromisoformat(capture["capture"]["started_utc"])
    end = datetime.fromisoformat(capture["capture"]["completed_utc"])
    return int((end - start).total_seconds() * 1_000_000_000)


def finalize_trace_index(results_root: Path, packet_root: Path, packet_archive: Path) -> None:
    selection = read_json(results_root / "capture-selection.json")
    grouped: dict[str, dict[str, object]] = {}
    drop_keys = ("required_source_loss", "cupti_dropped_records", "cupti_errors",
                 "cuda_packet_order_regressions", "cuda_raw_clock_mismatches",
                 "incomplete_application_slices", "incomplete_cuda_slices")
    for run in selection["cases"]:
        case = grouped.setdefault(run["name"], {
            "name": run["name"],
            "role": run["role"],
            "workload_identity": {
                "model_revision": run["model_revision"],
                "tokens": 24,
                "capture_target": run["capture_target"],
            },
            "untraced": [],
            "traced": [],
        })
        documents = run["packet_documents"]
        case["untraced"].append(documents["untraced-run.json"])
        case["traced"].append({
            "capture_command": run["capture_command"],
            "pid": run["pid"],
            "duration_ns": run["duration_ns"],
            "config": run["config"],
            "raw": run["raw"],
            "compressed": run["compressed"],
            "verification": documents["verification.json"],
            "query_output": documents["query-output.json"],
            "drop_counts": {key: run["verification_metrics"].get(key, 0) for key in drop_keys},
        })

    packet_index_path = packet_root / "packet-index.json"
    packet_index = file_identity(packet_index_path, "packet-index.json")
    packet = file_identity(packet_archive, packet_archive.name)
    trace_index = {
        "schema_version": "phase12-5-trace-index-v1",
        "status": "complete",
        "source_revisions": selection["capture_target"],
        "artifact": selection["artifact"],
        "host": selection["host"],
        "tools": selection["tools"],
        "cases": list(grouped.values()),
        "packet": {
            "url": f"{RELEASE_ROOT}/{packet_archive.name}",
            "size": packet["size"],
            "sha256": packet["sha256"],
            "index": packet_index,
        },
    }
    write_json(results_root / "trace-index.json", trace_index)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--finalize-packet", type=Path)
    args = parser.parse_args()

    if args.finalize_packet is not None:
        finalize_trace_index(args.results_root, args.packet_root, args.finalize_packet)
        print(json.dumps({"status": "finalized", "packet": str(args.finalize_packet),
                          "trace_index": str(args.results_root / "trace-index.json")}))
        return 0

    args.results_root.mkdir(parents=True, exist_ok=True)
    args.packet_root.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, dict[str, object]] = {}
    selection_cases: list[dict[str, object]] = []

    for case in CASES:
        is_provider = case["name"].startswith("provider-")
        case_project = PROVIDER_PROJECT if is_provider else PROJECT
        case_nested = PROVIDER_NESTED if is_provider else NESTED
        case_release_tag = PROVIDER_RELEASE_TAG if is_provider else RELEASE_TAG
        case_release_root = (
            "https://github.com/murillo128/k3-out-of-core/releases/download/"
            f"{case_release_tag}"
        )
        source = args.capture_root / case["directory"]
        packet_case = args.packet_root / "cases" / case["directory"]
        packet_case.mkdir(parents=True, exist_ok=True)
        documents = {}
        for filename in ("case.json", "capture.json", "verification.json", "query-output.json",
                         "comparison.json", "untraced-run.json"):
            value = sanitize(read_json(source / filename), case["directory"])
            target = packet_case / filename
            write_json(target, value)
            documents[filename] = file_identity(
                target, f"cases/{case['directory']}/{filename}")

        case_json = read_json(source / "case.json")
        capture = read_json(source / "capture.json")
        verification = read_json(source / "verification.json")
        query = read_json(source / "query-output.json")
        comparison = read_json(source / "comparison.json")
        if (case_json["project_revision"] != case_project or
                case_json["nested_revision"] != case_nested):
            raise RuntimeError(f"revision mismatch in {case['directory']}")
        if verification["status"] != "valid" or comparison["status"] != "pass":
            raise RuntimeError(f"invalid selected case {case['directory']}")
        if verification["trace"]["sha256"] != case["raw_sha256"]:
            raise RuntimeError(f"raw trace mismatch in {case['directory']}")

        summary = critical_summary(query)
        if case["name"].startswith("provider-"):
            summary.update({
                "traced_throughput_tps": comparison["traced"]["decode"]["throughput_tps"],
                "untraced_throughput_tps": comparison["untraced"]["decode"]["throughput_tps"],
                "traced_decode_p95_us": comparison["traced"]["decode"]["p95_us"],
                "untraced_decode_p95_us": comparison["untraced"]["decode"]["p95_us"],
                "peak_rss_kib": comparison["traced"]["peak_rss_kib"],
                "cold_misses": comparison["traced"]["mechanism"]["cold_misses"],
                "cold_hits": comparison["traced"]["mechanism"]["cold_hits"],
                "read_bytes": comparison["traced"]["storage"]["read_bytes"],
                "transport_actual": comparison["traced"]["transport_actual"],
                "io_uring_enabled": comparison["traced"]["async_io"]["io_uring_enabled"],
                "synchronous_fallback_operations": comparison["traced"]["async_io"][
                    "synchronous_fallback_operations"],
            })
        else:
            summary.update({
                "traced_generation_tps_displayed": comparison["traced"]["generation_tps_displayed"],
                "untraced_generation_tps_displayed": comparison["untraced"]["generation_tps_displayed"],
                "exact_text_match": comparison["exact_text_match"],
                "exact_generated_ids_match": comparison["exact_generated_ids_match"],
                "exact_logits_identity_match": comparison["exact_logits_identity_match"],
                "finite_logits": comparison["finite_logits"],
                "matches_provider_generated_ids": comparison["matches_provider_generated_ids"],
            })
        summaries[case["directory"]] = summary

        metrics = verification["metrics"]
        raw_name = f"{case['directory']}.pftrace"
        selection_cases.append({
            "directory": case["directory"],
            "name": case["name"],
            "role": case["role"],
            "capture_target": {"project": case_project, "nested": case_nested},
            "model_revision": MODEL_REVISION,
            "capture_command": sanitize(capture["capture"]["workload_command"], case["directory"]),
            "pid": capture["capture"]["workload_pid"],
            "duration_ns": duration_ns(capture),
            "config": CONFIG,
            "raw": {"url": f"{case_release_root}/{raw_name}", "size": case["raw_size"],
                    "sha256": case["raw_sha256"]},
            "compressed": {"url": f"{case_release_root}/{raw_name}.zst",
                           "size": case["compressed_size"],
                           "sha256": case["compressed_sha256"]},
            "verification_metrics": metrics,
            "packet_documents": documents,
            "analysis_summary": summary,
        })

    selection = {
        "schema_version": "phase12-5-capture-selection-v1",
        "status": "complete",
        "capture_target": {"project": PROJECT, "nested": NESTED},
        "release_tag": RELEASE_TAG,
        "release_url": f"https://github.com/murillo128/k3-out-of-core/releases/tag/{RELEASE_TAG}",
        "artifact": {
            "repository": "unsloth/DeepSeek-V4-Flash-GGUF",
            "revision": MODEL_REVISION,
            "variant": "UD-Q3_K_XL",
            "total_size": 129448242976,
            "splits": [{"path": path, "size": size, "sha256": sha} for path, size, sha in SPLITS],
        },
        "host": {
            "identity": "79466",
            "kernel": "6.8.0-59-generic",
            "gpu": "NVIDIA GeForce RTX 3090 24 GB",
            "storage_limitations": ["virtio/ext4 source storage", "warm OS page cache",
                                    "not Phase 12 physical-NVMe or cold-state evidence"],
        },
        "tools": {"perfetto_version": "v50.1", "trace_processor": TRACE_PROCESSOR,
                  "cuda_version": "12.8", "cupti_version": 26},
        "cases": selection_cases,
    }
    selection_path = args.results_root / "capture-selection.json"
    write_json(selection_path, selection)
    shutil.copy2(selection_path, args.packet_root / "capture-selection.json")
    selection_identity = file_identity(selection_path, "capture-selection.json")

    selected = [summaries["provider-positional-selected-r12"],
                summaries["provider-positional-selected-r16"]]
    mean_storage = sum(item["nonoverlap_shares"]["storage"] for item in selected) / 2
    mean_orchestration = sum(
        item["nonoverlap_shares"]["scheduler"] + item["nonoverlap_shares"]["provider_residual"]
        for item in selected) / 2
    buffered = summaries["provider-buffered-io-uring-r12"]
    buffered_service_scheduler = (buffered["nonoverlap_shares"]["storage"] +
                                  buffered["nonoverlap_shares"]["scheduler"])

    report = {
        "schema_version": "phase12-5-bottleneck-report-v1",
        "status": "SUPPORTED_BOTTLENECK_ATTRIBUTION",
        "source_revisions": {"project": PROJECT, "nested": NESTED},
        "trace_index": selection_identity,
        "method": {
            "accounting": "Sweep-line union with fixed mutually exclusive priority: storage, transfer, CUDA sync, CUDA copy, CUDA kernel, scheduler, cache, provider, residual. Shares within a trace sum to one; cumulative counters are not treated as wall time.",
            "percentile_definition": "Nearest-rank array index floor((N-1)*p) after ascending sort; token_critical_path has 24 provider rows and 26 control rows.",
            "observation_inference_rule": "OBSERVED values come from accepted trace intervals, verifier metrics, or adjacent run results. INFERENCE rankings require repeatability or a bounded perturbation/control contrast and state an alternative and falsifier.",
        },
        "cases": [{"name": name, "observations": value} for name, value in summaries.items()],
        "rankings": [
            {
                "rank": 1,
                "bottleneck": "Positional provider storage-request queue lifetime",
                "observation": [
                    "Selected repeats assign 63.218% and 63.448% of non-overlapping token wall time to storage intervals.",
                    "Storage queue-wait p95 is 957.116 ms and 952.287 ms, while operation-service-wall p95 is 3.668 ms and 3.623 ms.",
                    "Both repeats preserve 5,963 storage requests, 65,788,575,744 read bytes, exact output identity, and 0.1587-0.1595 token/s traced throughput.",
                ],
                "alternative_explanation": "The storage span may expose upstream single-flight dispatch serialization rather than physical media latency.",
                "falsifier": "A bounded later experiment that keeps media/cache state fixed but permits independent positional requests to overlap would falsify this ranking if queue-wait and token wall do not fall.",
                "confidence": "HIGH",
                "affected": [
                    {"case": "provider-positional-selected-r12", "request_id": 2, "token_index": 0,
                     "layer": None, "flight_id": None},
                    {"case": "provider-positional-selected-r16", "request_id": 2, "token_index": 0,
                     "layer": None, "flight_id": None},
                ],
                "critical_path_share": mean_storage,
                "accounting": "Mean of the storage-priority union share from the two independent selected traces; disjoint from all lower-priority categories.",
                "smallest_next_action": "In a new controlling issue, run one fixed-workload positional-read scheduling experiment that changes only bounded independent-request overlap and measures the same SQL shares.",
            },
            {
                "rank": 2,
                "bottleneck": "Provider scheduler and residual serialization outside storage",
                "observation": [
                    "Selected repeats assign 15.864-16.064% to scheduler intervals and 18.741-18.769% to provider residual, 34.716% combined on average.",
                    "GPU busy share is only 2.939-2.943%, and GPU/storage overlap is 1.886-1.903% of token wall.",
                    "The fit and CPU-MoE controls have p95 critical-path rows of 0.637 s and 0.888 s versus 7.237-7.248 s for the selected provider repeats.",
                    "Both adjacent control pairs preserve exact generated text, all 24 token IDs, and all 24 whole-logit identities with zero non-finite logits.",
                ],
                "alternative_explanation": "Uninstrumented CPU work nested below provider scopes could be labeled provider residual rather than true coordination wait.",
                "falsifier": "Add bounded sub-slices around the largest provider residual intervals or sample them in a later experiment; substantial CPU execution with no wait would lower confidence.",
                "confidence": "HIGH",
                "affected": [{"case": "provider-positional-selected-r12", "request_id": 1,
                              "token_index": 0, "layer": None, "flight_id": None}],
                "critical_path_share": mean_orchestration,
                "accounting": "Mean selected scheduler-priority plus provider-priority union; these categories are disjoint from storage and from each other.",
                "smallest_next_action": "In a later diagnostic issue, subdivide only the provider residual and scheduler wait/dispatch intervals for one selected repeat before changing runtime policy.",
            },
            {
                "rank": 3,
                "bottleneck": "Buffered io_uring service and scheduler coordination regression",
                "observation": [
                    "Native io_uring is enabled with zero synchronous fallback, yet traced throughput is 0.1007 token/s and p95 decode is 12.037 s.",
                    "Buffered storage service p95 is 821.139 ms while queue-wait p95 is 0.921 ms; scheduler plus storage consume 83.460% of non-overlapping token wall.",
                    "GPU/storage overlap falls to 0.0017%, so the transport does not hide the provider miss path.",
                ],
                "alternative_explanation": "The regression could be specific to the host kernel/filesystem io_uring behavior rather than the runtime completion path.",
                "falsifier": "Repeat the exact buffered case on the Phase 12 physical storage host; comparable service and scheduler shares would support a runtime cause, while their disappearance would support a host cause.",
                "confidence": "HIGH",
                "affected": [{"case": "provider-buffered-io-uring-r12", "request_id": 2,
                              "token_index": 0, "layer": None, "flight_id": None}],
                "critical_path_share": buffered_service_scheduler,
                "accounting": "Buffered-only sum of mutually exclusive storage-priority and scheduler-priority interval unions.",
                "smallest_next_action": "Do not select buffered io_uring from this host result; carry one unchanged reproduction into the physical-storage Phase 12 matrix before any implementation change.",
            },
            {
                "rank": 4,
                "bottleneck": "Cold-cache miss pressure is material but RAM-expensive",
                "observation": [
                    "The 64 GiB cold tier reduces misses and storage rows from 5,963 to 4,427 and read bytes from 65.789 GB to 48.812 GB.",
                    "Traced throughput rises from 0.1587-0.1595 to 0.1948 token/s, while peak RSS rises from about 17.62 million KiB to 67.91 million KiB.",
                    "Even at 64 GiB, storage remains 55.612% and scheduler/provider 41.566% of non-overlapping token wall.",
                ],
                "alternative_explanation": "Warm page-cache variation could contribute to the single cold64 contrast.",
                "falsifier": "A repeated fixed-cache-size physical cold-state comparison with unchanged routing would falsify the mechanism if miss/byte reductions do not track critical-path reductions.",
                "confidence": "MEDIUM",
                "affected": [{"case": "provider-cold64-positional-r2", "request_id": 2,
                              "token_index": 0, "layer": None, "flight_id": None}],
                "critical_path_share": 0,
                "accounting": "Mechanism ranking only; no extra wall share is assigned because its latency is already accounted under storage/scheduler/provider, preventing double counting.",
                "smallest_next_action": "Retain the original Phase 12 capacity and physical-storage gates; do not adopt a 64 GiB default from this one warm-cache host trace.",
            },
        ],
        "limitations": [
            "The host uses virtio/ext4 with a warm OS page cache; this is diagnostic evidence for #53 and not Phase 12 physical-NVMe, direct-I/O, or cold-state evidence.",
            "The matrix is one prompt, one request, 24 generated tokens, one RTX 3090, and one selected provider policy/configuration.",
            "The priority accounting intentionally assigns overlapping time to one category; changing priority would change category shares but not the recorded interval unions.",
            "Fit and CPU-MoE controls leave 92.3% and 94.5% unattributed by provider-focused categories, so their traces reject provider/GPU explanations but do not identify their complete CPU critical path.",
            "Selected repeats are valid and repeatable. Selected r13-r15 and fit r7-r8 were excluded by the fixed 1 ms clock gate; fit r10-r11 were superseded because their opt-in streaming identity envelopes were incomplete.",
            "The two streaming DISCARD warnings are configuration-only; required-source loss, CUPTI drops/errors, invalid intervals, and packet-order regressions are zero in every selected trace.",
        ],
        "conclusion": "INFERENCE: storage request queue lifetime is the dominant selected positional-provider bottleneck, with provider/scheduler serialization and insufficient storage/GPU overlap second. GPU kernels, copies, synchronization, CPU runqueue delay, and physical read service do not explain the observed slowdown on this host. Buffered io_uring is causally slower here, while 64 GiB cold caching helps but leaves the same bottleneck shape at a large RAM cost. The result is SUPPORTED_BOTTLENECK_ATTRIBUTION, not authorization to optimize and not a substitute for Phase 12 physical-storage evidence.",
    }
    report_path = args.results_root / "bottleneck-report.json"
    write_json(report_path, report)
    shutil.copy2(report_path, args.packet_root / "bottleneck-report.json")

    excluded = {
        "schema_version": "phase12-5-excluded-attempts-v1",
        "capture_target": {"project": PROVIDER_PROJECT, "nested": PROVIDER_NESTED},
        "attempts": [
            {"case": "provider-positional-selected-r13", "reason": "TRACE_INVALID",
             "errors": ["cuda_raw_clock_mismatches=746", "clock_anchor_residual_ns=2593949"]},
            {"case": "provider-positional-selected-r14", "reason": "TRACE_INVALID",
             "errors": ["clock_anchor_residual_ns=1567553"]},
            {"case": "provider-positional-selected-r15", "reason": "TRACE_INVALID",
             "errors": ["clock_anchor_residual_ns=1102356"]},
            {"case": "fit-control-r7", "reason": "TRACE_INVALID",
             "errors": ["clock_anchor_residual_ns=1440614"]},
            {"case": "fit-control-r8", "reason": "TRACE_INVALID",
             "errors": ["clock_anchor_residual_ns=1147775"]},
            {"case": "fit-control-r10", "reason": "IDENTITY_INCOMPLETE",
             "capture_target": {"project": "5a900559b53683dffad086f925e46a8870917169",
                                "nested": "32d7dbd4cf1c3c0392828449bc7c29163e8831e1"},
             "errors": ["streamed generated_ids=0", "streamed logits_fnv64=0"]},
            {"case": "fit-control-r11", "reason": "IDENTITY_INCOMPLETE",
             "capture_target": {"project": "5a900559b53683dffad086f925e46a8870917169",
                                "nested": "32d7dbd4cf1c3c0392828449bc7c29163e8831e1"},
             "errors": ["streamed generated_ids=0", "streamed logits_fnv64=0"]},
        ],
        "selection_rule": "Only zero-error traces with complete adjacent identities at their recorded immutable capture target are selected. Earlier diagnostics are excluded by revision identity or the stated fixed gate.",
    }
    write_json(args.results_root / "excluded-attempts.json", excluded)
    shutil.copy2(args.results_root / "excluded-attempts.json",
                 args.packet_root / "excluded-attempts.json")

    summary = f"""# Phase 12.5 Checkpoint C — causal bottleneck report

`SUPPORTED_BOTTLENECK_ATTRIBUTION` with provider captures at `{PROVIDER_PROJECT}` / `{PROVIDER_NESTED}` and corrected control captures at `{PROJECT}` / `{NESTED}`.

`OBSERVED`: both selected positional repeats are exact and pass the active-trace gate. Traced throughput is 0.1587 and 0.1595 token/s. Non-overlapping critical-path accounting assigns 63.218% and 63.448% to storage intervals, 15.864-16.064% to scheduler intervals, and 18.741-18.769% to provider residual. Storage queue-wait p95 is 952-957 ms while operation-service-wall p95 is 3.62-3.67 ms. GPU busy time is about 2.94% and storage/GPU overlap about 1.9%.

`OBSERVED`: the buffered native-io_uring case uses zero synchronous fallback but falls to 0.1007 token/s. Storage service p95 becomes 821 ms, scheduler plus storage consume 83.46% of token wall, and storage/GPU overlap is effectively zero. The 64 GiB cold case reduces misses from 5,963 to 4,427 and improves traced throughput to 0.1948 token/s, but peak RSS rises to 67,913,992 KiB and storage remains 55.61% of token wall. Fit and CPU-MoE controls have zero provider-storage rows and p95 critical-path rows of 0.637 s and 0.888 s. Both adjacent controls preserve exact generated text, all 24 token IDs, and all 24 whole-logit identities with zero non-finite logits.

`INFERENCE`: storage-request queue lifetime is the dominant selected-path bottleneck; provider/scheduler serialization and insufficient overlap are second. GPU execution, synchronization, CPU scheduling, and physical read-service time do not explain the provider slowdown on this host. Buffered io_uring is slower here. Larger cold capacity helps but does not remove the bottleneck shape and is not a justified default.

`BLOCKED`: none. All six selected raws are below 2 GiB, all compressed forms are below 1 GiB, total raw plus compressed size is 6,209,341,375 bytes, and every required loss/drop/error counter is zero. Five attempts rejected by the fixed 1 ms clock gate and two superseded incomplete-identity controls are listed in `excluded-attempts.json`.

This evidence comes from virtio/ext4 with a warm page cache and does not weaken or replace Phase 12 physical-NVMe, cold-state, direct-I/O, full-size, statistical, or storage-layout gates.
"""
    (args.results_root / "SUMMARY.md").write_text(summary, encoding="utf-8")
    shutil.copy2(args.results_root / "SUMMARY.md", args.packet_root / "SUMMARY.md")

    reproduce = f"""# Independent reproduction

1. Verify every URL, size, and SHA-256 in `capture-selection.json` and download all six raw `.pftrace` files. Each immutable release tag targets the per-case capture revision recorded in the index.
2. Acquire Perfetto v50.1 `trace_processor_shell` from the official v50.1 release and verify SHA-256 `{TRACE_PROCESSOR['sha256']}` and size `{TRACE_PROCESSOR['size']}`.
3. For each raw trace, run `<TRACE_PROCESSOR> --full-sort -q <SQL> <TRACE>` for the five SQL files in `sql/`, or run `python3 analyze_perfetto.py --trace-processor <TRACE_PROCESSOR> --trace <TRACE> --verification <CASE>/verification.json --output <OUTPUT> --case-name <CASE>`.
4. Compare the regenerated JSON with `cases/<case>/query-output.json`. Inspect selected-provider p95/p99 rows and compare them with both controls before accepting attribution.
5. Treat `bottleneck-report.json` observations as measured and its rankings as qualified inference. Do not treat this packet as Phase 12 physical-storage evidence.

The packet indexes immutable raw URLs instead of embedding 5.60 GB of raw traces. No access to the capture host or original checkout is required.
"""
    (args.packet_root / "REPRODUCE.md").write_text(reproduce, encoding="utf-8")

    repo_root = Path(__file__).resolve().parents[2]
    packet_scripts = args.packet_root / "scripts"
    packet_scripts.mkdir(parents=True, exist_ok=True)
    for relative in (
        "scripts/phase12_5/analyze_perfetto.py",
        "scripts/phase12_5/capture_perfetto.py",
        "scripts/phase12_5/common.py",
        "scripts/phase12_5/package_checkpoint_c.py",
        "scripts/phase12_5/run_checkpoint_c.py",
        "scripts/phase12_5/verify_perfetto.py",
        "scripts/phase12_5/EVENT_SCHEMA.md",
        "scripts/phase12_5/configs/full-stack-split-1g.pbtxt",
        "schemas/phase12_5/bottleneck-report-v1.schema.json",
        "schemas/phase12_5/trace-index-v1.schema.json",
        "llama.cpp/vendor/perfetto-v50.1/LICENSE",
        "llama.cpp/vendor/perfetto-v50.1/PROVENANCE.md",
    ):
        source = repo_root / relative
        target = args.packet_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for source in sorted((repo_root / "scripts/phase12_5/sql").glob("*.sql")):
        target = args.packet_root / "scripts/phase12_5/sql" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    packet_index = {
        "schema_version": "phase12-5-independent-packet-index-v1",
        "capture_target": {"project": PROJECT, "nested": NESTED},
        "release_tag": RELEASE_TAG,
        "contents": [
            file_identity(path, path.relative_to(args.packet_root).as_posix())
            for path in sorted(args.packet_root.rglob("*"))
            if path.is_file() and path.name != "packet-index.json"
        ],
        "index_note": "The index intentionally excludes packet-index.json itself; the external trace index records its identity.",
    }
    write_json(args.packet_root / "packet-index.json", packet_index)

    print(json.dumps({"status": "complete", "cases": len(CASES),
                      "results": str(args.results_root), "packet": str(args.packet_root)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import tarfile
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


SCHEMA_PROFILE = "expert-prefetch-profile-v1"
PROMPT_ORDER = ["prose-en-small", "code-en-small", "structured-en-small", "technical-en-large", "narrative-en-large", "narrative-es-large"]
POLICIES = {"OFF", "STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY", "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"}
READINESS = {"HOST_READY", "DEVICE_READY"}
FNV_OFFSET = 1469598103934665603
FNV_PRIME = 1099511628211
MASK64 = (1 << 64) - 1


class Phase10Error(ValueError):
    pass


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Phase10Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle, object_pairs_hook=_reject_pairs, parse_float=lambda value: (_ for _ in ()).throw(Phase10Error(f"float is forbidden: {value}")))
    if not isinstance(value, dict):
        raise Phase10Error("top-level JSON must be an object")
    return value


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode("utf-8")


def write_json(path: str | Path, value: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(canonical_bytes(value))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_identity(path: str | Path, name: str | None = None) -> dict[str, Any]:
    source = Path(path)
    return {"name": name or source.name, "size": source.stat().st_size, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def require_fields(value: Any, fields: Iterable[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Phase10Error(f"{name} fields do not match v1")


def require_uint(value: Any, name: str, *, positive: bool = False, maximum: int = (1 << 64) - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum or (positive and value == 0):
        raise Phase10Error(f"{name} must be a bounded unsigned integer")
    return value


def require_sha256(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(byte not in "0123456789abcdef" for byte in value):
        raise Phase10Error(f"{name} must be lowercase SHA-256")
    return value


def is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def fold_membership(index: int) -> dict[str, Any]:
    require_uint(index, "fold index", maximum=5)
    test = PROMPT_ORDER[index]
    validation = PROMPT_ORDER[(index + 1) % 6]
    training = [prompt for prompt in PROMPT_ORDER if prompt not in {test, validation}]
    return {"index": index, "training": training, "validation": validation, "test": test}


def break_even(record: dict[str, Any]) -> dict[str, int]:
    required = ["lead_ns", "demand_service_ns", "predictor_compute_ns", "speculative_service_ns", "scheduler_demand_delay_ns", "displacement_refill_ns"]
    values = {name: require_uint(record[name], name) for name in required}
    hidden = max(0, min(values["lead_ns"], values["demand_service_ns"]) - values["predictor_compute_ns"])
    waste = values["predictor_compute_ns"] + values["speculative_service_ns"] + values["scheduler_demand_delay_ns"] + values["displacement_refill_ns"]
    if hidden <= 0 or hidden + waste > (1 << 64) - 1 or waste > ((1 << 64) - 1) // 10000:
        raise Phase10Error("break-even envelope is ineligible")
    bps = (waste * 10000 + hidden + waste - 1) // (hidden + waste)
    if bps > 10000:
        raise Phase10Error("break-even threshold is unrepresentable")
    return {"hidden_benefit_ns": hidden, "waste_cost_ns": waste, "break_even_bps": bps}


def build_fingerprint(storage_map: dict[str, Any]) -> dict[str, Any]:
    require_fields(storage_map, {"schema_version", "model", "source_files", "routed_layers", "expert_count", "entries"}, "storage map")
    if storage_map["schema_version"] != "expert-storage-map-v1":
        raise Phase10Error("unsupported storage map")
    routed_layers = [require_uint(value, "routed layer", maximum=(1 << 31) - 1) for value in storage_map["routed_layers"]]
    experts = require_uint(storage_map["expert_count"], "expert count", positive=True, maximum=(1 << 31) - 1)
    entries = sorted(storage_map["entries"], key=lambda value: (value["layer"], value["expert_id"]))
    if len(entries) != len(routed_layers) * experts:
        raise Phase10Error("storage byte map is incomplete")
    expert_bytes = []
    for ordinal, entry in enumerate(entries):
        layer = routed_layers[ordinal // experts]
        expert = ordinal % experts
        if entry["layer"] != layer or entry["expert_id"] != expert:
            raise Phase10Error("storage byte map is not canonical")
        payload = require_uint(entry["atomic_bundle_bytes"], "atomic bundle bytes", positive=True)
        expert_bytes.append({"layer": layer, "expert": expert, "payload_bytes": payload, "physical_bytes": payload})
    files = []
    model = storage_map["model"]
    for ordinal, source in enumerate(storage_map["source_files"]):
        if source["index"] != ordinal:
            raise Phase10Error("source files are not ordered")
        files.append({"ordinal": ordinal, "name": Path(source["identity"]).name,
            "size": require_uint(source["size"], "source size", positive=True),
            "sha256": require_sha256(model["sha256"], "model sha256") if ordinal == 0 and len(storage_map["source_files"]) == 1 else require_sha256(source["sha256"], "source sha256")})
    layer_count = max(routed_layers) + 1
    top_k = 2
    layout_text = f"{layer_count}:{experts}:{top_k}\n" + "".join(f"{layer}," for layer in routed_layers) + "\n"
    layout_text += "".join(f"{entry['layer']}:{entry['expert']}:{entry['payload_bytes']}\n" for entry in expert_bytes)
    tensors: dict[str, dict[str, Any]] = {}
    for entry in entries:
        for projection in entry["projections"].values():
            name = projection["tensor_name"]
            metadata = {"name": name, "type": projection["ggml_type"]["id"],
                "source_file_index": projection["source_file_index"], "alignment": projection["gguf_alignment"],
                "byte_size": projection["tensor_byte_size"], "ne": projection["logical_shape"],
                "nb": projection["physical_strides"]}
            if name in tensors and tensors[name] != metadata:
                raise Phase10Error("inconsistent repeated tensor layout")
            tensors[name] = metadata
    for name in sorted(tensors):
        tensor = tensors[name]
        if len(tensor["ne"]) != 4 or len(tensor["nb"]) != 4:
            raise Phase10Error("tensor layout must have four axes")
        layout_text += (f"{len(name.encode('utf-8'))}:{name}:{tensor['type']}:{tensor['source_file_index']}:"
            f"{tensor['alignment']}:{tensor['byte_size']}\n")
        layout_text += "".join(f"{value}," for value in tensor["ne"]) + "\n"
        layout_text += "".join(f"{value}," for value in tensor["nb"]) + "\n"
    layout_sha = sha256_bytes(layout_text.encode("utf-8"))
    package_text = "".join(f"{entry['ordinal']}:{len(entry['name'].encode('utf-8'))}:{entry['name']}:{entry['size']}:{entry['sha256']}\n" for entry in files) + layout_sha + "\n"
    return {"package_sha256": sha256_bytes(package_text.encode("utf-8")), "files": files,
        "layer_count": layer_count, "routed_layers": routed_layers, "experts_per_layer": experts,
        "experts_per_token": top_k, "tensor_layout_sha256": layout_sha, "expert_bytes": expert_bytes}


def read_phase2_corpus(archive: str | Path, artifact: str) -> dict[str, Any]:
    if artifact not in {"f16", "mxfp4"}:
        raise Phase10Error("artifact must be f16 or mxfp4")
    archive_path = Path(archive)
    phase2_dir = Path(__file__).resolve().parents[1] / "phase2"
    sys.path.insert(0, str(phase2_dir))
    try:
        from route_trace import read_route_trace
    finally:
        sys.path.pop(0)
    traces: dict[str, Any] = {}
    identities: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="phase10-corpus-") as directory:
        root = Path(directory).resolve()
        with tarfile.open(archive_path, "r:gz") as handle:
            members = []
            prefix = "phase2-k3-route-corpus-v1/traces/"
            wanted = {f"{prefix}{artifact}-cpu-{prompt}.bin" for prompt in PROMPT_ORDER}
            for member in handle.getmembers():
                if member.name not in wanted:
                    continue
                destination = (root / member.name).resolve()
                if root not in destination.parents or not member.isfile():
                    raise Phase10Error("unsafe corpus member")
                members.append(member)
            if {member.name for member in members} != wanted:
                raise Phase10Error("Phase 2 corpus is incomplete")
            handle.extractall(root, members=members, filter="data")
        for prompt in PROMPT_ORDER:
            path = root / f"phase2-k3-route-corpus-v1/traces/{artifact}-cpu-{prompt}.bin"
            trace = read_route_trace(path)
            traces[prompt] = trace
            identities[prompt] = file_identity(path, f"{artifact}-cpu-{prompt}.bin")
    return {"archive": file_identity(archive_path), "traces": traces, "identities": identities}


def token_events(trace: dict[str, Any], decode_only: bool = True) -> list[dict[str, Any]]:
    records = trace["records"]
    groups: dict[tuple[str, int], dict[int, list[int]]] = defaultdict(dict)
    for record in records:
        if decode_only and record["phase"] != "DECODE":
            continue
        key = (record["phase"], record["position"])
        if record["layer"] in groups[key]:
            raise Phase10Error("duplicate route record")
        selected = sorted(set(record["selected_experts"]))
        if len(selected) != len(record["selected_experts"]):
            raise Phase10Error("duplicate selected expert")
        groups[key][record["layer"]] = selected
    events = []
    for ordinal, (_, layers) in enumerate(sorted(groups.items(), key=lambda item: (item[0][0] != "PREFILL", item[0][1]))):
        events.append({"token": ordinal, "layers": [{"layer": layer, "experts": layers[layer]} for layer in sorted(layers)]})
    return events


def training_tables(traces: dict[str, Any], prompts: list[str], routed_layers: list[int]) -> tuple[list[dict[str, int]], list[dict[str, int]], int]:
    static: Counter[tuple[int, int]] = Counter()
    transitions: Counter[tuple[int, int, int, int]] = Counter()
    rows = 0
    for prompt in prompts:
        for record in traces[prompt]["records"]:
            rows += 1
            for expert in set(record["selected_experts"]):
                static[(record["layer"], expert)] += 1
        for event in token_events(traces[prompt], decode_only=False):
            by_layer = {item["layer"]: item["experts"] for item in event["layers"]}
            for source_layer, target_layer in zip(routed_layers, routed_layers[1:]):
                if source_layer not in by_layer or target_layer not in by_layer:
                    continue
                for source in by_layer[source_layer]:
                    for target in by_layer[target_layer]:
                        transitions[(source_layer, source, target_layer, target)] += 1
    counts = [{"layer": layer, "expert": expert, "count": count} for (layer, expert), count in sorted(static.items())]
    edges = [{"source_layer": source_layer, "source_expert": source, "target_layer": target_layer,
        "target_expert": target, "count": count} for (source_layer, source, target_layer, target), count in sorted(transitions.items())]
    return counts, edges, rows


def validate_profile(profile: dict[str, Any]) -> None:
    require_fields(profile, {"schema_version", "profile_id", "tool", "source", "target", "static_counts", "transitions", "costs", "selection", "seed"}, "profile")
    if profile["schema_version"] != SCHEMA_PROFILE:
        raise Phase10Error("unsupported profile schema")
    require_fields(profile["tool"], {"name", "version"}, "tool")
    if not isinstance(profile["tool"]["name"], str) or not profile["tool"]["name"]:
        raise Phase10Error("tool name must be non-empty")
    require_uint(profile["tool"]["version"], "tool version", maximum=(1 << 32) - 1)
    source = profile["source"]
    require_fields(source, {"kind", "artifacts", "fold"}, "source")
    if source["kind"] not in {"route_trace", "imatrix_derived"}:
        raise Phase10Error("unsupported source kind")
    if not isinstance(source["artifacts"], list) or not source["artifacts"]:
        raise Phase10Error("source artifacts must be non-empty")
    for artifact in source["artifacts"]:
        require_fields(artifact, {"name", "size", "sha256"}, "source artifact")
        if not isinstance(artifact["name"], str) or not artifact["name"]:
            raise Phase10Error("source artifact name must be non-empty")
        require_uint(artifact["size"], "source artifact size", positive=True)
        require_sha256(artifact["sha256"], "source artifact sha256")
    fold = source["fold"]
    require_fields(fold, {"index", "training", "validation", "test", "training_rows", "validation_rows", "test_rows"}, "fold")
    if fold_membership(fold["index"]) != {key: fold[key] for key in ("index", "training", "validation", "test")}:
        raise Phase10Error("fold membership is not canonical")
    for name in ("training_rows", "validation_rows", "test_rows"):
        require_uint(fold[name], name, positive=True)
    target = profile["target"]
    require_fields(target, {"package_sha256", "files", "layer_count", "routed_layers", "experts_per_layer", "experts_per_token", "tensor_layout_sha256", "expert_bytes"}, "target")
    require_sha256(target["package_sha256"], "package_sha256")
    require_sha256(target["tensor_layout_sha256"], "tensor_layout_sha256")
    layer_count = require_uint(target["layer_count"], "layer_count", positive=True, maximum=(1 << 31) - 1)
    experts = require_uint(target["experts_per_layer"], "experts_per_layer", positive=True, maximum=(1 << 31) - 1)
    top_k = require_uint(target["experts_per_token"], "experts_per_token", positive=True, maximum=experts)
    del top_k
    if not isinstance(target["files"], list) or not target["files"]:
        raise Phase10Error("target files must be non-empty")
    for ordinal, file in enumerate(target["files"]):
        require_fields(file, {"ordinal", "name", "size", "sha256"}, "target file")
        if require_uint(file["ordinal"], "file ordinal", maximum=(1 << 32) - 1) != ordinal:
            raise Phase10Error("target files are not canonical")
        if not isinstance(file["name"], str) or not file["name"]:
            raise Phase10Error("target file name must be non-empty")
        require_uint(file["size"], "target file size", positive=True)
        require_sha256(file["sha256"], "target file sha256")
    package_text = "".join(f"{file['ordinal']}:{len(file['name'].encode('utf-8'))}:{file['name']}:{file['size']}:{file['sha256']}\n"
        for file in target["files"]) + target["tensor_layout_sha256"] + "\n"
    if sha256_bytes(package_text.encode("utf-8")) != target["package_sha256"]:
        raise Phase10Error("target package digest is inconsistent")
    if not isinstance(target["routed_layers"], list) or not target["routed_layers"]:
        raise Phase10Error("routed layers must be non-empty")
    routed_layers = [require_uint(layer, "routed layer", maximum=layer_count - 1) for layer in target["routed_layers"]]
    if routed_layers != sorted(set(routed_layers)):
        raise Phase10Error("routed layers are not canonical")
    if not isinstance(target["expert_bytes"], list) or len(target["expert_bytes"]) != len(routed_layers) * experts:
        raise Phase10Error("expert byte map is incomplete")
    byte_map: dict[tuple[int, int], tuple[int, int]] = {}
    for ordinal, record in enumerate(target["expert_bytes"]):
        require_fields(record, {"layer", "expert", "payload_bytes", "physical_bytes"}, "expert bytes")
        key = (routed_layers[ordinal // experts], ordinal % experts)
        if (record["layer"], record["expert"]) != key:
            raise Phase10Error("expert byte map is not canonical")
        payload = require_uint(record["payload_bytes"], "payload bytes", positive=True)
        physical = require_uint(record["physical_bytes"], "physical bytes", positive=True)
        if physical < payload:
            raise Phase10Error("physical bytes are smaller than payload")
        byte_map[key] = (payload, physical)
    seen = set()
    for record in profile["static_counts"]:
        require_fields(record, {"layer", "expert", "count"}, "static count")
        key = (require_uint(record["layer"], "layer"), require_uint(record["expert"], "expert", maximum=experts - 1))
        if key[0] not in routed_layers or key in seen:
            raise Phase10Error("duplicate or zero static count")
        require_uint(record["count"], "count", positive=True)
        seen.add(key)
    edges = set()
    for record in profile["transitions"]:
        require_fields(record, {"source_layer", "source_expert", "target_layer", "target_expert", "count"}, "transition")
        edge = tuple(record[name] for name in ("source_layer", "source_expert", "target_layer", "target_expert"))
        if any(isinstance(value, bool) or not isinstance(value, int) for value in edge):
            raise Phase10Error("transition key must be integral")
        source_index = routed_layers.index(edge[0]) if edge[0] in routed_layers else -1
        if source_index < 0 or source_index + 1 >= len(routed_layers) or routed_layers[source_index + 1] != edge[2] or \
                not 0 <= edge[1] < experts or not 0 <= edge[3] < experts or edge in edges:
            raise Phase10Error("duplicate or zero transition")
        require_uint(record["count"], "transition count", positive=True)
        edges.add(edge)
    cost_fields = {"transport", "readiness", "lead_ns", "demand_service_ns", "speculative_service_ns",
        "predictor_compute_ns", "scheduler_demand_delay_ns", "displacement_refill_ns", "storage_bytes", "h2d_bytes",
        "break_even_bps", "utility_window_predictions", "utility_min_observations", "utility_min_timely_successes"}
    if not isinstance(profile["costs"], list) or not profile["costs"]:
        raise Phase10Error("cost records must be non-empty")
    cost_keys = set()
    for cost in profile["costs"]:
        require_fields(cost, cost_fields, "cost")
        if cost["transport"] not in {"BUFFERED", "DIRECT_IO", "HOST_TO_DEVICE"} or cost["readiness"] not in READINESS:
            raise Phase10Error("unsupported cost envelope")
        cost_key = (cost["transport"], cost["readiness"])
        if cost_key in cost_keys:
            raise Phase10Error("duplicate cost envelope")
        cost_keys.add(cost_key)
        computed = break_even(cost)
        if computed["break_even_bps"] != cost["break_even_bps"]:
            raise Phase10Error("invalid break-even record")
        require_uint(cost["storage_bytes"], "storage bytes")
        require_uint(cost["h2d_bytes"], "h2d bytes")
        buffered = cost["transport"] in {"BUFFERED", "DIRECT_IO"}
        valid_path = ((buffered and cost["readiness"] == "HOST_READY" and cost["storage_bytes"] > 0 and cost["h2d_bytes"] == 0) or
            (buffered and cost["readiness"] == "DEVICE_READY" and cost["storage_bytes"] > 0 and cost["h2d_bytes"] > 0) or
            (cost["transport"] == "HOST_TO_DEVICE" and cost["readiness"] == "DEVICE_READY" and
                cost["storage_bytes"] == 0 and cost["h2d_bytes"] > 0))
        if not valid_path:
            raise Phase10Error("invalid transport/readiness byte envelope")
        window = require_uint(cost["utility_window_predictions"], "utility window", positive=True, maximum=(1 << 32) - 1)
        observations = require_uint(cost["utility_min_observations"], "utility observations", positive=True, maximum=window)
        del observations
        successes = require_uint(cost["utility_min_timely_successes"], "utility successes", maximum=window)
        expected_successes = (computed["break_even_bps"] * window + 9999) // 10000
        if not is_power_of_two(window) or successes != expected_successes:
            raise Phase10Error("invalid utility threshold")
    selection = profile["selection"]
    require_fields(selection, {"matrix_version", "tuning_digest", "fold_index", "policy", "candidates_per_target", "temporal_window_tokens", "transport", "readiness", "break_even_bps"}, "selection")
    require_sha256(selection["tuning_digest"], "tuning_digest")
    candidates = require_uint(selection["candidates_per_target"], "selected candidates", positive=True, maximum=experts)
    del candidates
    window = require_uint(selection["temporal_window_tokens"], "selected temporal window", maximum=64)
    if (selection["policy"] == "TEMPORAL_FREQUENCY" and
            (window < 2 or not is_power_of_two(window))) or \
            (selection["policy"] != "TEMPORAL_FREQUENCY" and window != 0):
        raise Phase10Error("invalid selected temporal window")
    require_uint(selection["break_even_bps"], "selected break even", maximum=10000)
    if selection["policy"] not in {"STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY",
            "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE", "BLOCKING_HOT"} or \
            selection["fold_index"] != fold["index"] or selection["readiness"] not in READINESS or \
            selection["transport"] not in {"BUFFERED", "DIRECT_IO", "HOST_TO_DEVICE"}:
        raise Phase10Error("invalid selection provenance")
    selected_cost = next((cost for cost in profile["costs"]
        if cost["transport"] == selection["transport"] and cost["readiness"] == selection["readiness"]), None)
    if selected_cost is None or selected_cost["break_even_bps"] != selection["break_even_bps"]:
        raise Phase10Error("selected cost envelope mismatch")
    if not isinstance(profile["seed"], list):
        raise Phase10Error("seed must be an array")
    seed_keys = set()
    expected_seed_keys = {(record["layer"], record["expert"]) for record in
        sorted(profile["static_counts"], key=lambda value: (-value["count"], value["layer"], value["expert"]))[:len(profile["seed"])]}
    previous_seed_order = None
    for seed in profile["seed"]:
        require_fields(seed, {"layer", "expert", "count", "payload_bytes", "physical_bytes"}, "seed")
        key = (require_uint(seed["layer"], "seed layer"), require_uint(seed["expert"], "seed expert", maximum=experts - 1))
        if key in seed_keys or key not in byte_map:
            raise Phase10Error("invalid seed key")
        seed_keys.add(key)
        count = require_uint(seed["count"], "seed count", positive=True)
        static_count = next((record["count"] for record in profile["static_counts"]
            if (record["layer"], record["expert"]) == key), None)
        if count != static_count:
            raise Phase10Error("seed count does not match static count")
        if byte_map[key] != (seed["payload_bytes"], seed["physical_bytes"]):
            raise Phase10Error("seed byte map mismatch")
        order = (count, key[0], key[1])
        if previous_seed_order is not None and order < previous_seed_order:
            raise Phase10Error("seed load order is not canonical")
        previous_seed_order = order
    if seed_keys != expected_seed_keys:
        raise Phase10Error("seed set is not the ranked complete prefix")


def fnv_append(state: int, value: int | str) -> int:
    if isinstance(value, str):
        state = fnv_append(state, len(value))
        data = value.encode("utf-8")
        for byte in data:
            state = ((state ^ byte) * FNV_PRIME) & MASK64
        return state
    for byte in struct.pack("<Q", value & MASK64):
        state = ((state ^ byte) * FNV_PRIME) & MASK64
    return state


def config_digest(config: dict[str, Any], profile_sha256: str) -> int:
    policy_id = {name: index for index, name in enumerate(["OFF", "STATIC_LAYER", "PREVIOUS_TOKEN", "TEMPORAL_FREQUENCY", "CROSS_LAYER_TRANSITION", "RANDOM_BASELINE"])}[config["policy"]]
    readiness_id = {"HOST_READY": 0, "DEVICE_READY": 1}[config["readiness"]]
    fields = [1, config["struct_size"], policy_id, readiness_id, 0, config["temporal_window_tokens"], config["candidates_per_target"],
        64 * 1024 * 1024, 32, 1 << 30, 1 << 30, 1 << 30, 1 << 30, 32, 32, 64, 32, 0, 0, 0, 0]
    state = FNV_OFFSET
    for value in fields:
        state = fnv_append(state, value)
    return fnv_append(state, require_sha256(profile_sha256, "profile sha256"))


def splitmix64(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & MASK64
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return state, (value ^ (value >> 31)) & MASK64


def predictor_candidates(profile: dict[str, Any], policy: str, history: list[list[list[int]]], target_layer: int,
        candidate_count: int, digest: int, request_ordinal: int, token: int) -> list[tuple[int, int]]:
    layers = profile["target"]["routed_layers"]
    layer_index = layers.index(target_layer)
    scores: list[tuple[int, int]] = []
    if policy == "STATIC_LAYER":
        scores = [(record["expert"], record["count"]) for record in profile["static_counts"] if record["layer"] == target_layer]
    elif policy == "PREVIOUS_TOKEN":
        scores = [(expert, 1) for expert in history[-1][layer_index]]
    elif policy == "TEMPORAL_FREQUENCY":
        counts: Counter[int] = Counter()
        recency: dict[int, int] = {}
        for ordinal, event in enumerate(history, 1):
            for expert in event[layer_index]:
                counts[expert] += 1
                recency[expert] = ordinal
        scores = [(expert, count * 128 + recency[expert]) for expert, count in counts.items()]
    elif policy == "RANDOM_BASELINE":
        eligible = list(range(profile["target"]["experts_per_layer"]))
        state = digest
        for value in (profile["source"]["fold"]["index"], request_ordinal, token, target_layer):
            state = fnv_append(state, value)
        for index in range(len(eligible)):
            state, value = splitmix64(state)
            selected = index + value % (len(eligible) - index)
            eligible[index], eligible[selected] = eligible[selected], eligible[index]
        scores = [(expert, 0) for expert in eligible]
    else:
        raise Phase10Error("policy has no token-end predictor")
    scores.sort(key=lambda value: (-value[1], value[0]))
    return scores[:candidate_count]


def cross_candidates(profile: dict[str, Any], source_layer: int, source_experts: list[int], target_layer: int,
        candidate_count: int) -> list[tuple[int, int]]:
    scores: Counter[int] = Counter()
    sources = sorted(set(source_experts))
    if len(sources) != len(source_experts):
        raise Phase10Error("duplicate source expert")
    for record in profile["transitions"]:
        if record["source_layer"] == source_layer and record["source_expert"] in sources and record["target_layer"] == target_layer:
            scores[record["target_expert"]] = min(MASK64, scores[record["target_expert"]] + record["count"])
    return sorted(scores.items(), key=lambda value: (-value[1], value[0]))[:candidate_count]


def state_digest(request_ordinal: int, history: list[list[list[int]]]) -> int:
    digest = fnv_append(FNV_OFFSET, request_ordinal)
    for token, layers in enumerate(history):
        digest = fnv_append(digest, token)
        for experts in layers:
            digest = fnv_append(digest, len(experts))
            for expert in experts:
                digest = fnv_append(digest, expert)
    return digest

cmake_minimum_required(VERSION 3.20)
project(issue102_cross_prompt_tools LANGUAGES CXX)

set(ISSUE102_LLAMA_SOURCE "/workspace/k3-out-of-core/llama.cpp" CACHE PATH "Frozen llama.cpp source")
set(ISSUE102_FROZEN_BUILD "/mnt/nvme1/issue77/build/cpu" CACHE PATH "Frozen CPU build")

function(import_shared name filename)
  add_library(${name} SHARED IMPORTED GLOBAL)
  set_target_properties(${name} PROPERTIES IMPORTED_LOCATION "${ISSUE102_FROZEN_BUILD}/bin/${filename}")
endfunction()

import_shared(issue102_llama_common "libllama-common.so.0.0.10345")
import_shared(issue102_llama "libllama.so.0.0.10345")
import_shared(issue102_ggml "libggml.so.0.17.0")
import_shared(issue102_ggml_cpu "libggml-cpu.so.0.17.0")
import_shared(issue102_ggml_base "libggml-base.so.0.17.0")
add_library(issue102_llama_common_base STATIC IMPORTED GLOBAL)
set_target_properties(issue102_llama_common_base PROPERTIES
  IMPORTED_LOCATION "${ISSUE102_FROZEN_BUILD}/common/libllama-common-base.a")

function(configure_issue102_target target)
  target_compile_features(${target} PRIVATE cxx_std_17)
  target_compile_definitions(${target} PRIVATE
    GGML_BACKEND_SHARED GGML_SHARED GGML_USE_CPU LLAMA_SHARED LLAMA_SUBPROCESS)
  target_compile_options(${target} PRIVATE
    -O3 -DNDEBUG -march=native -Wmissing-declarations -Wmissing-noreturn
    -Wall -Wextra -Wpedantic -Wcast-qual -Wno-unused-function -Wno-array-bounds -Wextra-semi)
  target_include_directories(${target} PRIVATE
    "${ISSUE102_LLAMA_SOURCE}/src"
    "${ISSUE102_LLAMA_SOURCE}/vendor"
    "${ISSUE102_LLAMA_SOURCE}/include"
    "${ISSUE102_LLAMA_SOURCE}/ggml/include"
    "${ISSUE102_LLAMA_SOURCE}/common")
  target_link_libraries(${target} PRIVATE
    issue102_llama_common issue102_llama issue102_ggml issue102_ggml_cpu
    issue102_ggml_base issue102_llama_common_base)
  set_target_properties(${target} PROPERTIES
    BUILD_RPATH "${ISSUE102_FROZEN_BUILD}/bin"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin")
endfunction()

add_executable(issue102-corpus-freezer freeze_corpus.cpp)
configure_issue102_target(issue102-corpus-freezer)

add_executable(issue102-stage-a-detokenizer detokenize_stage_a.cpp)
configure_issue102_target(issue102-stage-a-detokenizer)

# Keep the measurement-only delta auditable: derive it from the exact nested
# probe source pinned by issue #102, then require every source anchor to match.
set(original_source "${ISSUE102_LLAMA_SOURCE}/tests/phase13-mode-p-probe.cpp")
file(READ "${original_source}" probe_source)

macro(required_replace label needle replacement)
  set(before "${probe_source}")
  string(REPLACE "${needle}" "${replacement}" probe_source "${probe_source}")
  if(probe_source STREQUAL before)
    message(FATAL_ERROR "${label} source anchor did not match")
  endif()
endmacro()

required_replace("arguments"
  [=[    std::string point;
    std::string issue_mode = "BATCHED";]=]
  [=[    std::string point;
    std::string case_id;
    std::string protocol = "full-prompt";
    std::string issue_mode = "BATCHED";]=])

required_replace("argument parser"
  [=[        else if (option == "--point") args.point = value;
        else if (option == "--issue-mode") args.issue_mode = value;]=]
  [=[        else if (option == "--point") args.point = value;
        else if (option == "--case-id") args.case_id = value;
        else if (option == "--protocol") args.protocol = value;
        else if (option == "--issue-mode") args.issue_mode = value;]=])

required_replace("argument validation"
  [=[    return !args.model.empty() && (!args.prompt_corpus.empty() || args.prompt_token >= 0) && !args.output.empty() &&
        (args.point == "EXACT" || args.point == "KNEE" || args.point == "AGGRESSIVE") &&
        (args.issue_mode == "SERIAL" || args.issue_mode == "BATCHED") &&
        args.warmup_limit != 0 && args.decode_forwards != 0 &&
        args.threads != 0 && args.n_ctx >= args.warmup_limit + args.decode_forwards;]=]
  [=[    return !args.model.empty() && (!args.prompt_corpus.empty() || args.prompt_token >= 0) && !args.output.empty() &&
        (args.prompt_token >= 0 || !args.case_id.empty()) &&
        (args.point == "EXACT" || args.point == "KNEE" || args.point == "AGGRESSIVE" || args.point == "S2_P50") &&
        (args.protocol == "legacy-first-full" || args.protocol == "full-prompt") &&
        (args.issue_mode == "SERIAL" || args.issue_mode == "BATCHED") &&
        args.warmup_limit != 0 && args.decode_forwards != 0 && args.threads != 0;]=])

required_replace("prompt loader"
  [=[std::vector<llama_token> load_prompt(const std::string & path, const llama_vocab * vocab) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("unable to open prompt corpus");
    json corpus;
    input >> corpus;
    const auto & cases = corpus.at("cases");
    const auto selected = std::find_if(cases.begin(), cases.end(), [](const json & item) {
        return item.value("id", "") == "issue73-decision-prompt";
    });
    if (selected == cases.end()) throw std::runtime_error("decision prompt is absent");
    const std::string prompt = selected->at("prompt").get<std::string>();
    const int expected = selected->at("expected_prompt_tokens").get<int>();
    const int count = -llama_tokenize(vocab, prompt.data(), prompt.size(), nullptr, 0, true, true);
    if (count != expected || count != 100) throw std::runtime_error("decision prompt token identity mismatch");
    std::vector<llama_token> tokens((size_t(count)));
    if (llama_tokenize(vocab, prompt.data(), prompt.size(), tokens.data(), tokens.size(), true, true) != count) {
        throw std::runtime_error("decision prompt tokenization failed");
    }
    return tokens;
}]=]
  [=[struct prompt_case {
    std::string id;
    std::string family;
    int length_level = 0;
    std::string templated_prompt;
    std::vector<llama_token> tokens;
};

prompt_case load_prompt(
        const std::string & path,
        const std::string & case_id,
        const llama_model *,
        const llama_vocab * vocab,
        bool legacy) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("unable to open prompt corpus");
    json corpus;
    input >> corpus;
    json selected;
    for (const auto & item : corpus.at("cases")) {
        if (item.value("id", "") == case_id) selected = item;
    }
    if (selected.is_null() && corpus.contains("sentinel") && corpus.at("sentinel").value("id", "") == case_id) {
        selected = corpus.at("sentinel");
    }
    if (selected.is_null()) throw std::runtime_error("requested corpus case is absent");

    std::string rendered;
    if (selected.contains("raw_prompt")) {
        const std::string tick(1, char(96));
        const std::string quote(1, char(34));
        const std::string newline(1, char(10));
        const std::string prefix =
            "<|open|>message role=" + quote + "system" + quote +
            " type=" + quote + "thinking-effort" + quote + "<|sep|>" +
            tick + "thinking_effort" + tick +
            " guides on how much to think in your thinking channel "
            "(not including the response channel), supported values include " +
            tick + "low" + tick + ", " + tick + "medium" + tick + ", " +
            tick + "high" + tick + ", and " + tick + "max" + tick +
            "." + newline + "Now the system is invoked with " + tick + "thinking_effort=max" + tick +
            ".<|close|>message<|sep|><|end_of_msg|>"
            "<|open|>message role=" + quote + "user" + quote + "<|sep|>";
        const std::string suffix =
            "<|close|>message<|sep|><|end_of_msg|>"
            "<|open|>message role=" + quote + "assistant" + quote +
            "<|sep|><|open|>think<|sep|>";
        rendered = prefix + selected.at("raw_prompt").get<std::string>() + suffix;
        if (rendered != selected.at("templated_prompt").get<std::string>()) {
            throw std::runtime_error("frozen chat-template identity mismatch");
        }
    } else {
        rendered = selected.at("prompt").get<std::string>();
    }
    const int expected = selected.contains("observed_templated_prompt_tokens") ?
        selected.at("observed_templated_prompt_tokens").get<int>() :
        selected.at("expected_prompt_tokens").get<int>();
    const int count = -llama_tokenize(vocab, rendered.data(), rendered.size(), nullptr, 0, true, true);
    if (count != expected || (legacy && count != 100)) {
        throw std::runtime_error("prompt token identity mismatch");
    }
    prompt_case result;
    result.id = case_id;
    result.family = selected.value("semantic_family", legacy ? "legacy-sentinel" : "sentinel");
    result.length_level = selected.value("length_level", 0);
    result.templated_prompt = rendered;
    result.tokens.resize(size_t(count));
    if (llama_tokenize(vocab, rendered.data(), rendered.size(), result.tokens.data(), result.tokens.size(), true, true) != count) {
        throw std::runtime_error("prompt tokenization failed");
    }
    return result;
}]=])

required_replace("usage"
  [=[            "usage: %s --model GGUF --prompt-corpus JSON --output JSON --point EXACT|KNEE|AGGRESSIVE "]=]
  [=[            "usage: %s --model GGUF --prompt-corpus JSON --case-id ID --output JSON --point EXACT|KNEE|AGGRESSIVE|S2_P50 "]=])

required_replace("usage options"
  [=[            "[--issue-mode SERIAL|BATCHED --cold-cache-bytes N --warmup-limit N "]=]
  [=[            "[--protocol legacy-first-full|full-prompt --issue-mode SERIAL|BATCHED --cold-cache-bytes N --warmup-limit N "]=])

required_replace("prompt call"
  [=[        const auto prompt = args.prompt_token >= 0 ? std::vector<llama_token>{args.prompt_token} :
            load_prompt(args.prompt_corpus, vocab);
        if (std::any_of(prompt.begin(), prompt.end(), [n_vocab](llama_token token) {]=]
  [=[        prompt_case selected_case;
        if (args.prompt_token >= 0) {
            selected_case.id = "prompt-token";
            selected_case.tokens = { args.prompt_token };
        } else {
            selected_case = load_prompt(args.prompt_corpus, args.case_id, model.get(), vocab,
                args.protocol == "legacy-first-full");
        }
        const auto & prompt = selected_case.tokens;
        const uint64_t consumed_prompt_tokens = args.protocol == "full-prompt" ? prompt.size() : 0;
        if (args.protocol == "full-prompt" && prompt.size() + args.decode_forwards > args.n_ctx) {
            throw std::runtime_error("full prompt plus decode horizon exceeds context");
        }
        if (std::any_of(prompt.begin(), prompt.end(), [n_vocab](llama_token token) {]=])

required_replace("prefill loop"
  [=[        const auto fill_started = steady_clock::now();
        llm_expert_cold_scalar_snapshot fill_cold;
        llm_expert_storage_diagnostics fill_storage;
        llama_token next_token = prompt.front();
        uint32_t tokens_to_full = 0;
        bool seam_residency_visible = false;
        for (uint32_t index = 0; index < args.warmup_limit; ++index) {
            const llama_token input = index < prompt.size() ? prompt[index] : next_token;
            next_token = decode_one(input, false, 0);
            tokens_to_full = index + 1;
            fill_cold = provider->cold_cache_scalar_snapshot();
            fill_storage = storage->diagnostics();
            if (index == 0) {
                if (fill_cold.misses <= initial_cold.misses ||
                    fill_storage.read_requests <= initial_storage.read_requests ||
                    fill_storage.read_bytes <= initial_storage.read_bytes) {
                    throw std::runtime_error("first real miss did not increase backing reads");
                }
                std::vector<int32_t> experts(size_t(model->hparams.n_expert));
                std::vector<llama_route_service_tier> tiers(size_t(model->hparams.n_expert));
                for (int32_t expert = 0; expert < int32_t(experts.size()); ++expert) experts[expert] = expert;
                bool saw_routed_layer = false;
                for (size_t layer = 0; layer < model->layers.size(); ++layer) {
                    if (model->layers[layer].ffn_down_exps == nullptr) continue;
                    saw_routed_layer = true;
                    const auto tier_result = provider->route_service_tier_snapshot(
                        int32_t(layer), experts.data(), experts.size(), tiers.data());
                    if (!tier_result.is_ready() ||
                        std::find(tiers.begin(), tiers.end(), LLAMA_ROUTE_SERVICE_TIER_HOT) != tiers.end()) {
                        throw std::runtime_error("CPU service-tier seam returned invalid HOT/stale state");
                    }
                    seam_residency_visible = seam_residency_visible ||
                        std::find(tiers.begin(), tiers.end(), LLAMA_ROUTE_SERVICE_TIER_COLD) != tiers.end();
                }
                if (!saw_routed_layer || !seam_residency_visible) {
                    throw std::runtime_error("real cold residency is not visible to routing");
                }
            }
            if (fill_cold.occupancy == fill_cold.capacity) break;
        }
        const auto fill_completed = steady_clock::now();
        if (fill_cold.occupancy != fill_cold.capacity) {
            throw std::runtime_error("real cold cache did not fill within the bounded warmup: occupancy=" +
                std::to_string(fill_cold.occupancy) + " capacity=" + std::to_string(fill_cold.capacity));
        }
        const auto fill_async = model->expert_async_diagnostics();
        const auto fill_scheduler = model->expert_scheduler_diagnostics();]=]
  [=[        const auto fill_started = steady_clock::now();
        auto fill_completed = fill_started;
        llm_expert_cold_scalar_snapshot fill_cold;
        llm_expert_storage_diagnostics fill_storage;
        llm_expert_async_diagnostics fill_async;
        llm_expert_scheduler_diagnostics fill_scheduler;
        llama_token next_token = prompt.front();
        uint32_t tokens_to_full = 0;
        bool cache_became_full = false;
        bool seam_residency_visible = false;
        const uint32_t prefill_limit = args.protocol == "full-prompt" ? uint32_t(prompt.size()) : args.warmup_limit;
        for (uint32_t index = 0; index < prefill_limit; ++index) {
            const llama_token input = index < prompt.size() ? prompt[index] : next_token;
            next_token = decode_one(input, false, 0);
            const auto current_cold = provider->cold_cache_scalar_snapshot();
            const auto current_storage = storage->diagnostics();
            if (index == 0) {
                if (current_cold.misses <= initial_cold.misses ||
                    current_storage.read_requests <= initial_storage.read_requests ||
                    current_storage.read_bytes <= initial_storage.read_bytes) {
                    throw std::runtime_error("first real miss did not increase backing reads");
                }
                std::vector<int32_t> experts(size_t(model->hparams.n_expert));
                std::vector<llama_route_service_tier> tiers(size_t(model->hparams.n_expert));
                for (int32_t expert = 0; expert < int32_t(experts.size()); ++expert) experts[expert] = expert;
                bool saw_routed_layer = false;
                for (size_t layer = 0; layer < model->layers.size(); ++layer) {
                    if (model->layers[layer].ffn_down_exps == nullptr) continue;
                    saw_routed_layer = true;
                    const auto tier_result = provider->route_service_tier_snapshot(
                        int32_t(layer), experts.data(), experts.size(), tiers.data());
                    if (!tier_result.is_ready() ||
                        std::find(tiers.begin(), tiers.end(), LLAMA_ROUTE_SERVICE_TIER_HOT) != tiers.end()) {
                        throw std::runtime_error("CPU service-tier seam returned invalid HOT/stale state");
                    }
                    seam_residency_visible = seam_residency_visible ||
                        std::find(tiers.begin(), tiers.end(), LLAMA_ROUTE_SERVICE_TIER_COLD) != tiers.end();
                }
                if (!saw_routed_layer || !seam_residency_visible) {
                    throw std::runtime_error("real cold residency is not visible to routing");
                }
            }
            if (!cache_became_full && current_cold.occupancy == current_cold.capacity) {
                cache_became_full = true;
                tokens_to_full = index + 1;
                fill_completed = steady_clock::now();
                fill_cold = current_cold;
                fill_storage = current_storage;
                fill_async = model->expert_async_diagnostics();
                fill_scheduler = model->expert_scheduler_diagnostics();
                if (args.protocol == "legacy-first-full") break;
            }
        }
        const auto prefill_completed = steady_clock::now();
        if (!cache_became_full) {
            throw std::runtime_error("real cold cache did not fill within the bounded prefill");
        }]=])

required_replace("profile"
  [=[        } else if (args.point == "AGGRESSIVE") {
            max_swaps = 4;
            max_regret = 0.007303759455680847f;
        }]=]
  [=[        } else if (args.point == "AGGRESSIVE") {
            max_swaps = 4;
            max_regret = 0.007303759455680847f;
        } else if (args.point == "S2_P50") {
            max_swaps = 2;
            max_regret = 0.007303759455680847f;
        }]=])

required_replace("timing"
  [=[        const double fill_s = seconds(fill_completed - fill_started);
        const double post_s = seconds(post_completed - post_started);
        const double accounted_s = init_s + fill_s + measured_s + post_s;]=]
  [=[        const double fill_s = seconds(fill_completed - fill_started);
        const double prefill_s = seconds(prefill_completed - fill_started);
        const double post_s = seconds(post_completed - post_started);
        const double accounted_s = init_s + prefill_s + measured_s + post_s;]=])

required_replace("schema"
  [=[            {"schema_version", "phase13-6p-cpu-demand-v1"},]=]
  [=[            {"schema_version", "issue102-cross-prompt-cell-v1"},]=])

required_replace("result identity"
  [=[            {"point", args.point},
            {"pid", getpid()},]=]
  [=[            {"point", args.point},
            {"protocol", args.protocol},
            {"case", {
                {"id", selected_case.id}, {"semantic_family", selected_case.family},
                {"length_level", selected_case.length_level}, {"templated_prompt_tokens", prompt.size()},
                {"consumed_prompt_tokens", args.protocol == "full-prompt" ? prompt.size() : tokens_to_full},
            }},
            {"pid", getpid()},]=])

required_replace("fill result"
  [=[            {"fill", {
                {"tokens_to_full", tokens_to_full}, {"time_to_full_s", fill_s},]=]
  [=[            {"fill", {
                {"tokens_to_full", tokens_to_full}, {"time_to_full_s", fill_s},
                {"full_prompt_tokens", consumed_prompt_tokens}, {"prefill_s", prefill_s},]=])

required_replace("max regret availability"
  [=[                    {"failures", routing_stats.failures},]=]
  [=[                    {"failures", routing_stats.failures},
                    {"maximum_realized_regret", nullptr},
                    {"maximum_realized_regret_status", "unavailable_without_observer"},]=])

required_replace("wall fraction"
  [=[                {"cache_fill", accounted_s == 0 ? 0 : fill_s/accounted_s},]=]
  [=[                {"cache_fill_to_first_full", accounted_s == 0 ? 0 : fill_s/accounted_s},
                {"full_prompt_prefill", accounted_s == 0 ? 0 : prefill_s/accounted_s},]=])

set(generated_source "${CMAKE_CURRENT_BINARY_DIR}/issue102-cross-prompt-probe.cpp")
file(WRITE "${generated_source}" "${probe_source}")
add_executable(issue102-cross-prompt-probe "${generated_source}")
configure_issue102_target(issue102-cross-prompt-probe)

# Derive the post-Stage-A long-horizon diagnostic from the frozen Stage-A
# helper without changing that helper's source or binary. The only execution
# delta is six bounded, read-only scalar snapshots retained in memory.
set(horizon_source "${probe_source}")

macro(horizon_required_replace label needle replacement)
  set(before "${horizon_source}")
  string(REPLACE "${needle}" "${replacement}" horizon_source "${horizon_source}")
  if(horizon_source STREQUAL before)
    message(FATAL_ERROR "${label} horizon source anchor did not match")
  endif()
endmacro()

horizon_required_replace("fixed horizon validation"
  [=[        args.warmup_limit != 0 && args.decode_forwards != 0 && args.threads != 0;]=]
  [=[        args.warmup_limit != 0 && args.decode_forwards == 512 && args.threads != 0 &&
        args.protocol == "full-prompt";]=])

horizon_required_replace("snapshot storage"
  [=[        std::vector<double> forward_latency_s;
        forward_latency_s.reserve(args.decode_forwards);]=]
  [=[        std::vector<double> forward_latency_s;
        forward_latency_s.reserve(args.decode_forwards);
        json horizon_snapshots = json::array();
        int32_t first_eog_position = -1;]=])

horizon_required_replace("bounded snapshot loop"
  [=[            generated.push_back(next_token);
            forward_latency_s.push_back(seconds(forward_completed - forward_started));]=]
  [=[            generated.push_back(next_token);
            forward_latency_s.push_back(seconds(forward_completed - forward_started));
            const uint32_t generated_count = index + 1;
            if (first_eog_position < 0 && llama_vocab_is_eog(vocab, next_token)) {
                first_eog_position = int32_t(generated_count);
            }
            if (generated_count == 16 || generated_count == 32 || generated_count == 64 ||
                generated_count == 128 || generated_count == 256 || generated_count == 512) {
                const auto snapshot_cold = provider->cold_cache_scalar_snapshot();
                const auto snapshot_storage = storage->diagnostics();
                const auto snapshot_async = model->expert_async_diagnostics();
                const auto snapshot_scheduler = model->expert_scheduler_diagnostics();
                const auto snapshot_full = provider->hot_cache_diagnostics();
                const auto snapshot_routing = llama_cache_aware_routing_get_stats(context.get());
                struct rusage snapshot_usage {};
                if (getrusage(RUSAGE_SELF, &snapshot_usage) != 0) {
                    throw std::runtime_error("horizon snapshot getrusage failed");
                }
                horizon_snapshots.push_back({
                    {"generated_tokens", generated_count},
                    {"elapsed_s", seconds(forward_completed - measured_started)},
                    {"cold", cold_json(snapshot_cold)},
                    {"cold_delta", cold_delta_json(before_cold, snapshot_cold)},
                    {"storage_delta", storage_delta_json(before_storage, snapshot_storage)},
                    {"async_delta", async_delta_json(before_async, snapshot_async)},
                    {"scheduler_delta", scheduler_delta_json(before_scheduler, snapshot_scheduler)},
                    {"routing", {
                        {"ubatches", snapshot_routing.ubatches},
                        {"layers", snapshot_routing.layers},
                        {"decisions", snapshot_routing.decisions},
                        {"changed_decisions", snapshot_routing.changed_decisions},
                        {"swaps", snapshot_routing.swaps},
                        {"cumulative_score_regret", snapshot_routing.cumulative_score_regret},
                        {"explicit_synchronizations", snapshot_routing.explicit_synchronizations},
                        {"failures", snapshot_routing.failures},
                    }},
                    {"output_prefix", {
                        {"generated_token_count", generated.size()},
                        {"generated_token_hash", hex_u64(token_hash(generated))},
                        {"first_eog_position", first_eog_position < 0 ? json(nullptr) : json(first_eog_position)},
                    }},
                    {"resources", {
                        {"peak_rss_kib", snapshot_usage.ru_maxrss},
                        {"vm_swap_kib", vm_swap_kib()},
                        {"terminal_references", terminal_reference_json(snapshot_full)},
                        {"system_memory", system_memory_json(snapshot_full)},
                        {"scheduler_active_requests", snapshot_scheduler.active_requests},
                        {"scheduler_queued_requests", snapshot_scheduler.queued_requests},
                    }},
                });
            }]=])

horizon_required_replace("horizon schema"
  [=[            {"schema_version", "issue102-cross-prompt-cell-v1"},]=]
  [=[            {"schema_version", "issue102-long-horizon-cell-v1"},]=])

horizon_required_replace("horizon result"
  [=[            {"output", {
                {"generated_token_count", generated.size()},]=]
  [=[            {"horizon", {
                {"snapshot_boundaries", json::array({16, 32, 64, 128, 256, 512})},
                {"cumulative_snapshots", horizon_snapshots},
                {"first_eog_position", first_eog_position < 0 ? json(nullptr) : json(first_eog_position)},
            }},
            {"output", {
                {"generated_token_count", generated.size()},]=])

set(horizon_generated_source "${CMAKE_CURRENT_BINARY_DIR}/issue102-long-horizon-probe.cpp")
file(WRITE "${horizon_generated_source}" "${horizon_source}")
add_executable(issue102-long-horizon-probe "${horizon_generated_source}")
configure_issue102_target(issue102-long-horizon-probe)

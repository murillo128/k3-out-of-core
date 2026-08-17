#include "llama.h"
#include "llama-cpp.h"
#include "llama-context.h"
#include "llama-expert-async-io.h"
#include "llama-expert-scheduler.h"
#include "llama-expert-storage.h"
#include "llama-expert-weight-provider.h"
#include "llama-model.h"

#include "ggml-backend.h"
#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/resource.h>
#include <unistd.h>
#include <vector>

using json = nlohmann::ordered_json;
using steady_clock = std::chrono::steady_clock;

namespace {

constexpr uint32_t candidate_count = 32;
constexpr uint32_t selected_count = 16;
constexpr uint32_t routed_layer_count = 92;
constexpr uint64_t expert_bundle_bytes = 17547264;

struct arguments {
    std::string model;
    std::string prompt_corpus;
    std::string case_id;
    std::string output;
    std::string route_output;
    std::string quality_trace_output;
    std::string policy;
    std::string intervention;
    std::string reference_sequence;
    std::string issue_mode = "BATCHED";
    uint64_t cold_cache_bytes = 137728475136ULL;
    uint32_t horizon = 512;
    uint32_t threads = 32;
    uint32_t n_ctx = 1280;
};

bool parse_u32(const char * text, uint32_t & value) {
    char * end = nullptr;
    const unsigned long parsed = std::strtoul(text, &end, 10);
    if (end == text || *end != '\0' || parsed > UINT32_MAX) return false;
    value = uint32_t(parsed);
    return true;
}

bool parse_u64(const char * text, uint64_t & value) {
    char * end = nullptr;
    const unsigned long long parsed = std::strtoull(text, &end, 10);
    if (end == text || *end != '\0') return false;
    value = uint64_t(parsed);
    return true;
}

bool parse_arguments(int argc, char ** argv, arguments & args) {
    for (int index = 1; index < argc; ++index) {
        const std::string option = argv[index];
        if (index + 1 >= argc) return false;
        const char * value = argv[++index];
        if (option == "--model") args.model = value;
        else if (option == "--prompt-corpus") args.prompt_corpus = value;
        else if (option == "--case-id") args.case_id = value;
        else if (option == "--output") args.output = value;
        else if (option == "--route-output") args.route_output = value;
        else if (option == "--quality-trace-output") args.quality_trace_output = value;
        else if (option == "--policy") args.policy = value;
        else if (option == "--intervention") args.intervention = value;
        else if (option == "--reference-sequence") args.reference_sequence = value;
        else if (option == "--issue-mode") args.issue_mode = value;
        else if (option == "--cold-cache-bytes") {
            if (!parse_u64(value, args.cold_cache_bytes)) return false;
        } else if (option == "--horizon") {
            if (!parse_u32(value, args.horizon)) return false;
        } else if (option == "--threads") {
            if (!parse_u32(value, args.threads)) return false;
        } else if (option == "--n-ctx") {
            if (!parse_u32(value, args.n_ctx)) return false;
        } else {
            return false;
        }
    }
    const bool free_trajectory = args.intervention == "FREE_TRAJECTORY";
    const bool fixed_context = args.intervention == "DIRECT_FIXED_CONTEXT" ||
        args.intervention == "CAPACITY_FIXED_CONTEXT";
    return !args.model.empty() && !args.prompt_corpus.empty() && !args.case_id.empty() &&
        !args.output.empty() && !args.route_output.empty() && !args.quality_trace_output.empty() &&
        (args.policy == "EXACT" || args.policy == "KNEE" || args.policy == "S2_P50") &&
        (free_trajectory || fixed_context) &&
        (free_trajectory ? args.reference_sequence.empty() : !args.reference_sequence.empty()) &&
        (args.issue_mode == "SERIAL" || args.issue_mode == "BATCHED") &&
        args.cold_cache_bytes % expert_bundle_bytes == 0 &&
        args.horizon > 0 && args.horizon <= 1024 && args.threads == 32 && args.n_ctx == 1280;
}

double seconds(steady_clock::duration duration) {
    return std::chrono::duration<double>(duration).count();
}

double seconds(const timeval & value) {
    return double(value.tv_sec) + double(value.tv_usec)/1000000.0;
}

std::string hex_u64(uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << value;
    return out.str();
}

uint64_t token_hash(const std::vector<llama_token> & tokens) {
    uint64_t result = UINT64_C(1469598103934665603);
    for (llama_token token : tokens) {
        const uint32_t value = uint32_t(token);
        for (size_t byte = 0; byte < sizeof(value); ++byte) {
            result ^= (value >> (byte*8)) & 0xffU;
            result *= UINT64_C(1099511628211);
        }
    }
    return result;
}

int finite_argmax(const float * logits, int n_vocab) {
    int result = -1;
    float best = -std::numeric_limits<float>::infinity();
    for (int token = 0; token < n_vocab; ++token) {
        if (!std::isfinite(logits[token])) return -1;
        if (result < 0 || logits[token] > best) {
            result = token;
            best = logits[token];
        }
    }
    return result;
}

uint64_t vm_swap_kib() {
    std::ifstream status("/proc/self/status");
    std::string key;
    while (status >> key) {
        if (key == "VmSwap:") {
            uint64_t value = 0;
            status >> value;
            return value;
        }
        std::string remainder;
        std::getline(status, remainder);
    }
    return 0;
}

template<class T> T delta(T after, T before) {
    return after >= before ? after - before : 0;
}

json cold_json(const llm_expert_cold_scalar_snapshot & value) {
    return {
        {"requested_bytes", value.requested_bytes},
        {"actual_bytes", value.actual_bytes},
        {"capacity", value.capacity},
        {"occupancy", value.occupancy},
        {"requests", value.requests},
        {"hits", value.hits},
        {"misses", value.misses},
        {"admissions", value.admissions},
        {"evictions", value.evictions},
        {"residency_digest", hex_u64(value.residency_digest)},
    };
}

json cold_delta_json(
        const llm_expert_cold_scalar_snapshot & before,
        const llm_expert_cold_scalar_snapshot & after) {
    return {
        {"requests", delta(after.requests, before.requests)},
        {"hits", delta(after.hits, before.hits)},
        {"misses", delta(after.misses, before.misses)},
        {"admissions", delta(after.admissions, before.admissions)},
        {"evictions", delta(after.evictions, before.evictions)},
        {"occupancy_before", before.occupancy},
        {"occupancy_after", after.occupancy},
    };
}

json storage_json(const llm_expert_storage_diagnostics & value) {
    return {
        {"read_requests", value.read_requests},
        {"read_chunks", value.read_chunks},
        {"read_bytes", value.read_bytes},
        {"cancelled_reads", value.cancelled_reads},
        {"short_reads", value.short_reads},
        {"io_errors", value.io_errors},
        {"source_file_count", value.source_file_count},
        {"direct_source_count", value.direct_source_count},
        {"direct_unsupported_source_count", value.direct_unsupported_source_count},
        {"maximum_bundle_bytes", value.maximum_bundle_bytes},
    };
}

json storage_delta_json(
        const llm_expert_storage_diagnostics & before,
        const llm_expert_storage_diagnostics & after) {
    return {
        {"backing_loads", delta(after.read_requests, before.read_requests)},
        {"backing_chunks", delta(after.read_chunks, before.read_chunks)},
        {"backing_bytes", delta(after.read_bytes, before.read_bytes)},
        {"cancelled_reads", delta(after.cancelled_reads, before.cancelled_reads)},
        {"short_reads", delta(after.short_reads, before.short_reads)},
        {"io_errors", delta(after.io_errors, before.io_errors)},
    };
}

json async_delta_json(
        const llm_expert_async_diagnostics & before,
        const llm_expert_async_diagnostics & after) {
    return {
        {"read_requests_submitted", delta(after.read_requests_submitted, before.read_requests_submitted)},
        {"read_requests_completed", delta(after.read_requests_completed, before.read_requests_completed)},
        {"read_requests_cancelled", delta(after.read_requests_cancelled, before.read_requests_cancelled)},
        {"read_operations_completed", delta(after.read_operations_completed, before.read_operations_completed)},
        {"read_bytes_completed", delta(after.read_bytes_completed, before.read_bytes_completed)},
        {"queue_wait_samples", delta(after.read_queue_wait_samples, before.read_queue_wait_samples)},
        {"queue_wait_us", delta(after.read_queue_wait_us, before.read_queue_wait_us)},
        {"ring_submissions", delta(after.ring_submissions, before.ring_submissions)},
        {"ring_completions", delta(after.ring_completions, before.ring_completions)},
        {"direct_read_operations", delta(after.direct_read_operations, before.direct_read_operations)},
        {"direct_useful_bytes", delta(after.direct_useful_bytes, before.direct_useful_bytes)},
        {"direct_aligned_bytes", delta(after.direct_aligned_bytes, before.direct_aligned_bytes)},
        {"buffered_fallback_operations", delta(after.buffered_fallback_operations, before.buffered_fallback_operations)},
        {"synchronous_fallback_operations", delta(after.synchronous_fallback_operations, before.synchronous_fallback_operations)},
    };
}

json scheduler_delta_json(
        const llm_expert_scheduler_diagnostics & before,
        const llm_expert_scheduler_diagnostics & after) {
    return {
        {"flights_created", delta(after.flights_created, before.flights_created)},
        {"joins", delta(after.joins, before.joins)},
        {"terminal_complete", delta(after.terminal_complete, before.terminal_complete)},
        {"terminal_failed", delta(after.terminal_failed, before.terminal_failed)},
        {"terminal_cancelled", delta(after.terminal_cancelled, before.terminal_cancelled)},
        {"terminal_releases", delta(after.terminal_releases, before.terminal_releases)},
        {"stale_completions", delta(after.stale_completions, before.stale_completions)},
        {"active_requests", after.active_requests},
        {"queued_requests", after.queued_requests},
    };
}

json system_memory_json(const llm_hot_cache_diagnostics & value) {
    return {
        {"requested_pool_bytes", value.system_memory_requested_pool_bytes},
        {"selected_pool_bytes", value.system_memory_selected_pool_bytes},
        {"safe_pool_bytes", value.system_memory_safe_pool_bytes},
        {"admission_safe_pool_bytes", value.system_memory_admission_safe_pool_bytes},
        {"effective_limit_bytes", value.system_memory_effective_limit_bytes},
        {"available_headroom_bytes", value.system_memory_available_headroom_bytes},
        {"measured_non_pool_committed_bytes", value.system_memory_measured_non_pool_committed_bytes},
        {"model_file_virtual_bytes", value.system_memory_model_file_virtual_bytes},
        {"model_file_cache_resident_bytes", value.system_memory_model_file_cache_resident_bytes},
        {"other_process_resident_bytes", value.system_memory_other_process_resident_bytes},
        {"pressure_samples", value.system_memory_pressure_samples},
        {"pressure_rejections", value.system_memory_pressure_rejections},
        {"autofit", value.system_memory_autofit},
        {"budget_frozen", value.system_memory_budget_frozen},
        {"pressure_circuit_open", value.system_memory_pressure_circuit_open},
        {"pressure_rejection_reason", value.system_memory_pressure_rejection_reason},
        {"residency_unavailable_reason", value.system_memory_residency_unavailable_reason},
    };
}

json terminal_reference_json(const llm_hot_cache_diagnostics & value) {
    return {
        {"cold_hot_refs", value.cold_current_hot_refs},
        {"cold_transfer_refs", value.cold_current_transfer_refs},
        {"cold_request_refs", value.cold_current_request_refs},
        {"cold_cpu_execution_refs", value.cold_current_cpu_execution_refs},
        {"cold_batch_refs", value.cold_current_batch_refs},
        {"provider_pins", value.current_pins},
    };
}

struct prompt_case {
    std::string id;
    std::string family;
    int32_t length_level = 0;
    std::string templated_prompt;
    std::vector<llama_token> tokens;
};

prompt_case load_prompt(
        const std::string & path,
        const std::string & case_id,
        const llama_vocab * vocab) {
    std::ifstream input(path);
    if (!input) throw std::runtime_error("unable to open prompt corpus");
    json corpus;
    input >> corpus;
    json selected;
    for (const auto & item : corpus.at("cases")) {
        if (item.value("id", "") == case_id) selected = item;
    }
    if (selected.is_null() && corpus.contains("sentinel") &&
        corpus.at("sentinel").value("id", "") == case_id) {
        selected = corpus.at("sentinel");
    }
    if (selected.is_null()) throw std::runtime_error("requested corpus case is absent");
    const std::string rendered = selected.at("templated_prompt").get<std::string>();
    const int expected = selected.at("observed_templated_prompt_tokens").get<int>();
    const int count = -llama_tokenize(vocab, rendered.data(), rendered.size(), nullptr, 0, true, true);
    if (count != expected) throw std::runtime_error("prompt token identity mismatch");
    prompt_case result;
    result.id = case_id;
    result.family = selected.value("semantic_family", "sentinel");
    result.length_level = selected.value("length_level", 0);
    result.templated_prompt = rendered;
    result.tokens.resize(size_t(count));
    if (llama_tokenize(vocab, rendered.data(), rendered.size(), result.tokens.data(),
            result.tokens.size(), true, true) != count) {
        throw std::runtime_error("prompt tokenization failed");
    }
    return result;
}

std::vector<llama_token> tokenize_literal(const llama_vocab * vocab, const std::string & text) {
    const int count = -llama_tokenize(vocab, text.data(), text.size(), nullptr, 0, false, true);
    if (count <= 0) throw std::runtime_error("phase-boundary tokenization failed");
    std::vector<llama_token> result(static_cast<size_t>(count));
    if (llama_tokenize(vocab, text.data(), text.size(), result.data(), result.size(), false, true) != count) {
        throw std::runtime_error("phase-boundary tokenization changed");
    }
    return result;
}

std::string token_piece(const llama_vocab * vocab, llama_token token) {
    std::vector<char> buffer(32);
    int32_t count = llama_token_to_piece(vocab, token, buffer.data(), int32_t(buffer.size()), 0, true);
    if (count < 0) {
        buffer.resize(size_t(-count));
        count = llama_token_to_piece(vocab, token, buffer.data(), int32_t(buffer.size()), 0, true);
    }
    if (count < 0 || size_t(count) > buffer.size()) throw std::runtime_error("token-to-piece failed");
    return std::string(buffer.data(), size_t(count));
}

struct phase_accounting {
    std::vector<std::string> labels;
    int32_t transition_start = -1;
    int32_t final_start = -1;
    int32_t eog_position = -1;
};

phase_accounting classify_phases(
        const llama_vocab * vocab,
        const std::vector<llama_token> & tokens,
        const std::vector<llama_token> & boundary) {
    phase_accounting result;
    result.labels.assign(tokens.size(), "reasoning");
    for (size_t index = 0; index < tokens.size(); ++index) {
        if (llama_vocab_is_eog(vocab, tokens[index]) && result.eog_position < 0) {
            result.eog_position = int32_t(index);
            result.labels[index] = "eog_control";
        }
        if (result.final_start >= 0) {
            if (!llama_vocab_is_eog(vocab, tokens[index])) result.labels[index] = "final_answer";
            continue;
        }
        if (index + 1 < boundary.size()) continue;
        const size_t start = index + 1 - boundary.size();
        if (std::equal(boundary.begin(), boundary.end(), tokens.begin() + start)) {
            result.transition_start = int32_t(start);
            result.final_start = int32_t(index + 1);
            for (size_t item = start; item <= index; ++item) result.labels[item] = "transition_control";
        }
    }
    return result;
}

struct reference_sequence {
    llama_token seed_token = -1;
    std::vector<llama_token> target_ids;
    uint32_t horizon_limit = 0;
};

reference_sequence load_reference(const arguments & args) {
    std::ifstream input(args.reference_sequence);
    if (!input) throw std::runtime_error("unable to open reference sequence");
    json value;
    input >> value;
    if (value.value("schema_version", "") != "issue99-reference-sequence-v1" ||
        value.value("case_id", "") != args.case_id ||
        value.value("horizon_limit", 0U) != args.horizon) {
        throw std::runtime_error("reference-sequence identity mismatch");
    }
    reference_sequence result;
    result.seed_token = value.at("seed_token").get<llama_token>();
    result.target_ids = value.at("target_ids").get<std::vector<llama_token>>();
    result.horizon_limit = value.at("horizon_limit").get<uint32_t>();
    if (result.seed_token < 0 || result.target_ids.size() > args.horizon) {
        throw std::runtime_error("reference sequence is invalid or over horizon");
    }
    return result;
}

enum quality_record_type : uint8_t {
    QUALITY_RECORD_MOE_OUTPUT = 1,
    QUALITY_RECORD_HIDDEN_STATE = 2,
    QUALITY_RECORD_LOGITS = 3,
};

struct quality_trace {
    std::string path;
    std::string error;
    std::ofstream destination;
    std::vector<uint8_t> raw;
    std::vector<float> values;
    std::vector<float> block;
    uint32_t current_position = 0;
    bool capture_internal = false;
    bool finished = false;
    uint64_t records = 0;
    uint64_t moe_records = 0;
    uint64_t hidden_records = 0;
    uint64_t logits_records = 0;
    uint64_t payload_bytes = 0;
    uint64_t file_bytes = 0;

    template<typename T> bool write_value(const T & value) {
        destination.write(reinterpret_cast<const char *>(&value), sizeof(value));
        file_bytes += sizeof(value);
        return bool(destination);
    }

    bool write_bytes(const void * data, size_t size) {
        destination.write(static_cast<const char *>(data), size);
        file_bytes += size;
        return bool(destination);
    }

    bool initialize(const std::string & output_path, const json & metadata, size_t reserve_values) {
        const uint32_t endian_probe = 1;
        if (*reinterpret_cast<const uint8_t *>(&endian_probe) != 1 || output_path.empty() ||
            reserve_values > SIZE_MAX/sizeof(float)) return false;
        path = output_path;
        try {
            raw.reserve(reserve_values*sizeof(float));
            values.reserve(reserve_values);
            block.reserve(256);
        } catch (const std::bad_alloc &) {
            return false;
        }
        destination.open(path, std::ios::binary | std::ios::trunc);
        if (!destination) return false;
        const char magic[8] = {'P', '1', '3', 'Q', 'T', 'R', '1', '\n'};
        const std::string header = metadata.dump();
        if (header.size() > UINT32_MAX) return false;
        const uint32_t header_size = uint32_t(header.size());
        return write_bytes(magic, sizeof(magic)) && write_value(header_size) &&
            write_bytes(header.data(), header.size());
    }

    static bool parse_layer(std::string_view name, std::string_view prefix, int32_t & layer) {
        if (name.size() < prefix.size() || name.substr(0, prefix.size()) != prefix) return false;
        const std::string_view suffix = name.substr(prefix.size());
        const auto parsed = std::from_chars(suffix.data(), suffix.data() + suffix.size(), layer);
        return !suffix.empty() && parsed.ec == std::errc() &&
            parsed.ptr == suffix.data() + suffix.size() && layer >= 0;
    }

    bool wants(const ggml_tensor * tensor, quality_record_type & type, int32_t & layer) const {
        if (!capture_internal || tensor == nullptr) return false;
        const std::string_view name(tensor->name);
        if (parse_layer(name, "ffn_moe_out-", layer)) {
            if (layer < 1 || layer > int32_t(routed_layer_count)) return false;
            type = QUALITY_RECORD_MOE_OUTPUT;
            return true;
        }
        if (parse_layer(name, "l_out-", layer)) {
            // Kimi K3 layer 0 is dense. Pair hidden states only at the same
            // 92 routed layers for which a route/event identity exists.
            if (layer < 1 || layer > int32_t(routed_layer_count)) return false;
            type = QUALITY_RECORD_HIDDEN_STATE;
            return true;
        }
        return false;
    }

    bool tensor_to_float(const ggml_tensor * tensor) {
        const int64_t element_count = ggml_nelements(tensor);
        const size_t raw_bytes = ggml_nbytes(tensor);
        const size_t block_size = ggml_blck_size(tensor->type);
        const auto * traits = ggml_get_type_traits(tensor->type);
        if (element_count <= 0 || raw_bytes == 0 || block_size == 0 || traits == nullptr) return false;
        try {
            raw.resize(raw_bytes);
            values.clear();
            values.reserve(size_t(element_count));
            block.resize(block_size);
        } catch (const std::bad_alloc &) {
            return false;
        }
        ggml_backend_tensor_get(tensor, raw.data(), 0, raw.size());
        for (int64_t i3 = 0; i3 < tensor->ne[3]; ++i3) {
            for (int64_t i2 = 0; i2 < tensor->ne[2]; ++i2) {
                for (int64_t i1 = 0; i1 < tensor->ne[1]; ++i1) {
                    for (int64_t i0 = 0; i0 < tensor->ne[0]; i0 += block_size) {
                        const size_t offset = size_t(i3)*tensor->nb[3] + size_t(i2)*tensor->nb[2] +
                            size_t(i1)*tensor->nb[1] + size_t(i0/block_size)*tensor->nb[0];
                        if (offset >= raw.size()) return false;
                        if (tensor->type == GGML_TYPE_F32) {
                            float value = 0.0f;
                            if (offset + sizeof(value) > raw.size()) return false;
                            std::memcpy(&value, raw.data() + offset, sizeof(value));
                            values.push_back(value);
                        } else if (tensor->type == GGML_TYPE_F16) {
                            ggml_fp16_t value = 0;
                            if (offset + sizeof(value) > raw.size()) return false;
                            std::memcpy(&value, raw.data() + offset, sizeof(value));
                            values.push_back(ggml_fp16_to_fp32(value));
                        } else if (tensor->type == GGML_TYPE_BF16) {
                            ggml_bf16_t value {};
                            if (offset + sizeof(value) > raw.size()) return false;
                            std::memcpy(&value, raw.data() + offset, sizeof(value));
                            values.push_back(ggml_bf16_to_fp32(value));
                        } else if (traits->is_quantized && traits->to_float != nullptr) {
                            if (offset + traits->type_size > raw.size()) return false;
                            traits->to_float(raw.data() + offset, block.data(), block_size);
                            const size_t remaining = size_t(element_count) - values.size();
                            values.insert(values.end(), block.begin(), block.begin() + std::min(block_size, remaining));
                        } else {
                            return false;
                        }
                    }
                }
            }
        }
        return values.size() == size_t(element_count) &&
            std::all_of(values.begin(), values.end(), [](float value) { return std::isfinite(value); });
    }

    bool write_record(
            quality_record_type type,
            uint32_t position,
            int32_t layer,
            int32_t target_token,
            uint32_t n_tokens,
            const float * data,
            uint64_t count) {
        const uint8_t reserved[3] = {};
        if (data == nullptr || count == 0 || count > SIZE_MAX/sizeof(float) ||
            !write_value(uint8_t(type)) || !write_bytes(reserved, sizeof(reserved)) ||
            !write_value(position) || !write_value(layer) || !write_value(target_token) ||
            !write_value(n_tokens) || !write_value(count) ||
            !write_bytes(data, size_t(count)*sizeof(float))) {
            error = "quality trace write failed";
            return false;
        }
        records++;
        payload_bytes += count*sizeof(float);
        moe_records += type == QUALITY_RECORD_MOE_OUTPUT;
        hidden_records += type == QUALITY_RECORD_HIDDEN_STATE;
        logits_records += type == QUALITY_RECORD_LOGITS;
        return true;
    }

    bool capture_tensor(ggml_tensor * tensor, quality_record_type type, int32_t layer) {
        if (!tensor_to_float(tensor) || tensor->ne[1] != 1) {
            error = "quality tensor conversion failed";
            return false;
        }
        return write_record(type, current_position, layer, -1, 1, values.data(), values.size());
    }

    bool capture_logits(uint32_t position, llama_token target, const float * logits, uint32_t count) {
        if (logits == nullptr || count == 0 || target < 0 || uint32_t(target) >= count ||
            !std::all_of(logits, logits + count, [](float value) { return std::isfinite(value); })) {
            error = "quality logits are invalid";
            return false;
        }
        return write_record(QUALITY_RECORD_LOGITS, position, -1, target, 1, logits, count);
    }

    bool finish() {
        if (finished) return error.empty();
        finished = true;
        destination.flush();
        if (!destination) {
            error = "quality trace flush failed";
            return false;
        }
        destination.close();
        return bool(destination) && error.empty();
    }
};

bool capture_quality_tensor(ggml_tensor * tensor, bool ask, void * user_data) {
    auto & trace = *static_cast<quality_trace *>(user_data);
    quality_record_type type = QUALITY_RECORD_HIDDEN_STATE;
    int32_t layer = -1;
    if (!trace.wants(tensor, type, layer)) return false;
    return ask || trace.capture_tensor(tensor, type, layer);
}

struct route_writer {
    std::ofstream destination;
    std::string error;
    std::string policy;
    uint32_t max_swaps = 0;
    float max_regret = 0.0f;
    uint64_t records = 0;
    uint64_t swaps = 0;
    double corrected_regret = 0.0;
    double raw_regret = 0.0;
    bool finished = false;

    bool initialize(const std::string & path, const json & metadata) {
        destination.open(path, std::ios::trunc);
        if (!destination) return false;
        destination << json({{"record_type", "metadata"}, {"metadata", metadata}}).dump() << '\n';
        return bool(destination);
    }

    bool capture(const llama_route_observation * observation) {
        if (observation == nullptr || observation->n_tokens != 1 ||
            observation->n_expert_used != selected_count || observation->n_candidates != candidate_count ||
            observation->layer < 0 || observation->selected_experts == nullptr ||
            observation->weights == nullptr || observation->candidate_experts == nullptr ||
            observation->candidate_selection_scores == nullptr || observation->candidate_probabilities == nullptr) {
            error = "incomplete or structurally invalid route-observer payload";
            return false;
        }
        for (uint32_t rank = 0; rank < candidate_count; ++rank) {
            const int32_t candidate = observation->candidate_experts[rank];
            const float score = observation->candidate_selection_scores[rank];
            const float probability = observation->candidate_probabilities[rank];
            if (candidate < 0 || !std::isfinite(score) || !std::isfinite(probability) ||
                (rank > 0 && score > observation->candidate_selection_scores[rank - 1])) {
                error = "invalid ordered candidate payload";
                return false;
            }
            for (uint32_t prior = 0; prior < rank; ++prior) {
                if (candidate == observation->candidate_experts[prior]) {
                    error = "duplicate candidate expert";
                    return false;
                }
            }
        }
        uint32_t changed = 0;
        std::array<bool, selected_count> seen {};
        for (uint32_t rank = 0; rank < selected_count; ++rank) {
            const int32_t selected = observation->selected_experts[rank];
            if (!std::isfinite(observation->weights[rank])) {
                error = "invalid selected weight";
                return false;
            }
            for (uint32_t prior = 0; prior < rank; ++prior) {
                seen[rank] = seen[rank] || selected == observation->selected_experts[prior];
            }
            if (seen[rank]) {
                error = "duplicate selected expert";
                return false;
            }
            if (selected == observation->candidate_experts[rank]) continue;
            changed++;
            const int32_t * replacement = std::find(
                observation->candidate_experts + selected_count,
                observation->candidate_experts + candidate_count,
                selected);
            if (policy == "EXACT" || changed > max_swaps ||
                replacement == observation->candidate_experts + candidate_count) {
                error = "selected route is outside the frozen substitution policy";
                return false;
            }
            const size_t replacement_rank = size_t(replacement - observation->candidate_experts);
            const float regret = observation->candidate_selection_scores[rank] -
                observation->candidate_selection_scores[replacement_rank];
            if (!std::isfinite(regret) || regret < 0.0f || regret > max_regret) {
                error = "selected route exceeds the frozen regret bound";
                return false;
            }
            swaps++;
            corrected_regret += regret;
            raw_regret += observation->candidate_probabilities[rank] -
                observation->candidate_probabilities[replacement_rank];
        }
        json row = {
            {"record_type", "route"},
            {"sequence_position", observation->request_ordinal},
            {"layer", observation->layer},
            {"selected_experts", std::vector<int32_t>(
                observation->selected_experts, observation->selected_experts + selected_count)},
            {"selected_weights", std::vector<float>(
                observation->weights, observation->weights + selected_count)},
            {"candidate_experts", std::vector<int32_t>(
                observation->candidate_experts, observation->candidate_experts + candidate_count)},
            {"candidate_selection_scores", std::vector<float>(
                observation->candidate_selection_scores,
                observation->candidate_selection_scores + candidate_count)},
            {"candidate_probabilities", std::vector<float>(
                observation->candidate_probabilities,
                observation->candidate_probabilities + candidate_count)},
        };
        destination << row.dump() << '\n';
        if (!destination) {
            error = "route output write failed";
            return false;
        }
        records++;
        return true;
    }

    bool finish() {
        if (finished) return error.empty();
        finished = true;
        destination.flush();
        if (!destination) {
            error = "route output flush failed";
            return false;
        }
        destination.close();
        return bool(destination) && error.empty();
    }
};

bool capture_route(const llama_route_observation * observation, void * user_data) {
    return static_cast<route_writer *>(user_data)->capture(observation);
}

json routing_stats_json(const llama_cache_aware_routing_stats & value) {
    return {
        {"ubatches", value.ubatches},
        {"layers", value.layers},
        {"decisions", value.decisions},
        {"changed_decisions", value.changed_decisions},
        {"swaps", value.swaps},
        {"cumulative_score_regret", value.cumulative_score_regret},
        {"explicit_synchronizations", value.explicit_synchronizations},
        {"failures", value.failures},
    };
}

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::fprintf(stderr,
            "usage: %s --model GGUF --prompt-corpus JSON --case-id ID --output JSON "
            "--route-output JSONL --quality-trace-output BINARY --policy EXACT|KNEE|S2_P50 "
            "--intervention FREE_TRAJECTORY|DIRECT_FIXED_CONTEXT|CAPACITY_FIXED_CONTEXT "
            "[--reference-sequence JSON] --cold-cache-bytes N --horizon N "
            "[--issue-mode SERIAL|BATCHED --threads 32 --n-ctx 1280]\n",
            argv[0]);
        return 2;
    }

    try {
        const auto process_started = steady_clock::now();
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level == GGML_LOG_LEVEL_ERROR) std::fputs(text, stderr);
        }, nullptr);
        ggml_backend_load_all();
        uint32_t gpu_devices = 0;
        for (size_t index = 0; index < ggml_backend_dev_count(); ++index) {
            gpu_devices += ggml_backend_dev_type(ggml_backend_dev_get(index)) ==
                GGML_BACKEND_DEVICE_TYPE_GPU;
        }
        if (gpu_devices != 0) throw std::runtime_error("GPU backend/device present in CPU-only quality probe");

        auto model_params = llama_model_default_params();
        model_params.n_gpu_layers = 0;
        model_params.use_extra_bufts = false;
        model_params.load_mode = LLAMA_LOAD_MODE_DIRECT_IO;
        model_params.expert_weights_mode = LLAMA_EXPERT_WEIGHTS_MODE_COLD_CACHE;
        model_params.expert_runtime_mode = LLAMA_EXPERT_RUNTIME_MODE_PERFORMANCE;
        model_params.expert_hot_cache_capacity = 0;
        model_params.expert_cold_cache_bytes = args.cold_cache_bytes;
        model_params.expert_transfer_ring_bytes = 0;
        model_params.expert_miss_policy = LLAMA_EXPERT_MISS_POLICY_CPU_FALLBACK;
        model_params.expert_io_trace_capacity = 0;
        model_params.expert_background_promotion = false;
        model_params.expert_async_cold_fill = false;

        const auto model_load_started = steady_clock::now();
        llama_model_ptr model(llama_model_load_from_file(args.model.c_str(), model_params));
        if (!model) throw std::runtime_error("model load failed");
        const auto model_loaded = steady_clock::now();
        if (!model->uses_cpu_cold_cache()) throw std::runtime_error("CPU cold-only model topology not selected");
        auto * provider = model->expert_weight_provider();
        if (provider == nullptr ||
            !provider->debug_set_host_resident_serial_issue_for_testing(
                args.issue_mode == "SERIAL").is_ready()) {
            throw std::runtime_error("unable to configure the internal issue-mode evidence seam");
        }
        auto * storage = model->expert_storage();
        if (storage == nullptr) throw std::runtime_error("CPU expert storage unavailable");
        const llama_vocab * vocab = llama_model_get_vocab(model.get());
        const int n_vocab = llama_vocab_n_tokens(vocab);
        const prompt_case selected_case = load_prompt(args.prompt_corpus, args.case_id, vocab);
        const auto & prompt = selected_case.tokens;
        if (prompt.size() + args.horizon > args.n_ctx) {
            throw std::runtime_error("full prompt plus decode horizon exceeds context");
        }
        const std::string phase_boundary_text =
            "<|close|>think<|sep|><|open|>response<|sep|>";
        const std::vector<llama_token> phase_boundary = tokenize_literal(vocab, phase_boundary_text);
        reference_sequence reference;
        const bool fixed_context = args.intervention != "FREE_TRAJECTORY";
        if (fixed_context) reference = load_reference(args);
        if (fixed_context && std::any_of(reference.target_ids.begin(), reference.target_ids.end(),
                [n_vocab](llama_token token) { return token < 0 || token >= n_vocab; })) {
            throw std::runtime_error("reference token is outside the frozen vocabulary");
        }

        quality_trace trace;
        const int32_t n_embd = llama_model_n_embd(model.get());
        if (n_embd <= 0 || !trace.initialize(args.quality_trace_output, {
                {"schema_version", "phase13-quality-trace-v1"},
                {"issue99_trace_contract", "issue99-ephemeral-paired-tensor-trace-v1"},
                {"encoding", "little-endian-float32"},
                {"record_header", "<B3xIiiIQ"},
                {"record_types", {{"1", "ffn_moe_out"}, {"2", "l_out"}, {"3", "logits"}}},
                {"case_id", args.case_id},
                {"policy", args.policy},
                {"intervention", args.intervention},
                {"capacity_bytes", args.cold_cache_bytes},
                {"horizon_limit", args.horizon},
                {"capture_internal_phase", "DECODE"},
                {"raw_retention", "ephemeral_until_immediate_scalarization"},
            }, size_t(n_embd))) {
            throw std::runtime_error("quality trace initialization failed");
        }

        auto context_params = llama_context_default_params();
        context_params.n_ctx = args.n_ctx;
        context_params.n_batch = 1;
        context_params.n_ubatch = 1;
        context_params.n_threads = int32_t(args.threads);
        context_params.n_threads_batch = int32_t(args.threads);
        context_params.no_perf = true;
        context_params.cb_eval = capture_quality_tensor;
        context_params.cb_eval_user_data = &trace;
        const auto context_load_started = steady_clock::now();
        llama_context_ptr context(llama_init_from_model(model.get(), context_params));
        if (!context) throw std::runtime_error("context initialization failed");
        const auto context_loaded = steady_clock::now();

        const auto initial_cold = provider->cold_cache_scalar_snapshot();
        const auto initial_storage = storage->diagnostics();
        const auto initial_async = model->expert_async_diagnostics();
        const auto initial_scheduler = model->expert_scheduler_diagnostics();
        const auto initial_full = provider->hot_cache_diagnostics();
        const uint64_t allowed_async_fallback_mask =
            uint64_t(llm_expert_async_fallback_reason::buffer_registration);
        if (!initial_cold.available || initial_cold.occupancy != 0 || initial_cold.capacity == 0 ||
            initial_full.system_memory_requested_pool_bytes != args.cold_cache_bytes ||
            initial_full.system_memory_selected_pool_bytes != initial_cold.requested_bytes ||
            !initial_full.system_memory_budget_frozen ||
            initial_full.system_memory_autofit != (args.cold_cache_bytes == 0) ||
            !initial_full.cpu_cold_only || initial_full.requested_capacity != 0 ||
            initial_full.effective_capacity != 0 || initial_full.pool_bytes != 0 ||
            !initial_full.slots.empty() ||
            initial_storage.direct_source_count != initial_storage.source_file_count ||
            initial_storage.direct_unsupported_source_count != 0 || !initial_async.io_uring_enabled ||
            (initial_async.fallback_reason_mask & ~allowed_async_fallback_mask) != 0) {
            throw std::runtime_error("CPU production-path initial validation failed");
        }

        auto decode_plain = [&](llama_token token) -> llama_token {
            llama_batch batch = llama_batch_get_one(&token, 1);
            if (llama_decode(context.get(), batch) != 0) throw std::runtime_error("prefill decode failed");
            llama_synchronize(context.get());
            const float * logits = llama_get_logits_ith(context.get(), -1);
            const int next = logits == nullptr ? -1 : finite_argmax(logits, n_vocab);
            if (next < 0) throw std::runtime_error("non-finite prefill logits");
            return llama_token(next);
        };

        const auto prefill_started = steady_clock::now();
        llama_token seed_token = prompt.front();
        bool cache_became_full = false;
        uint32_t tokens_to_full = 0;
        for (uint32_t index = 0; index < prompt.size(); ++index) {
            seed_token = decode_plain(prompt[index]);
            const auto snapshot = provider->cold_cache_scalar_snapshot();
            if (!cache_became_full && snapshot.occupancy == snapshot.capacity) {
                cache_became_full = true;
                tokens_to_full = index + 1;
            }
        }
        const auto prefill_completed = steady_clock::now();
        if (!cache_became_full) throw std::runtime_error("real cold cache did not fill during full prompt");
        if (fixed_context && seed_token != reference.seed_token) {
            throw std::runtime_error("exact prefill seed differs from frozen reference");
        }

        uint32_t max_swaps = 0;
        float max_regret = 0.0f;
        if (args.policy == "KNEE") {
            max_swaps = 1;
            max_regret = 0.0030885785818099976f;
        } else if (args.policy == "S2_P50") {
            max_swaps = 2;
            max_regret = 0.007303759455680847f;
        }
        const bool changed_routing = args.policy != "EXACT";
        if (changed_routing) {
            const llama_cache_aware_routing_config routing = {
                true, candidate_count, max_swaps, max_regret, nullptr, nullptr, nullptr,
            };
            if (llama_set_cache_aware_routing(context.get(), &routing) !=
                LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                throw std::runtime_error("provider-backed routing configuration failed");
            }
        }
        route_writer routes;
        routes.policy = args.policy;
        routes.max_swaps = max_swaps;
        routes.max_regret = max_regret;
        if (!routes.initialize(args.route_output, {
                {"schema_version", "issue99-route-stream-v1"},
                {"case_id", args.case_id},
                {"policy", args.policy},
                {"intervention", args.intervention},
                {"capacity_bytes", args.cold_cache_bytes},
                {"candidate_count", candidate_count},
                {"selected_count", selected_count},
            }) || llama_set_route_observer_candidate_count(context.get(), candidate_count) !=
                LLAMA_ROUTE_OBSERVER_STATUS_OK ||
            llama_set_route_observer(context.get(), capture_route, &routes) !=
                LLAMA_ROUTE_OBSERVER_STATUS_OK) {
            throw std::runtime_error("route observer initialization failed");
        }
        context->sched_reserve();
        llama_cache_aware_routing_reset_stats(context.get());

        const auto before_cold = provider->cold_cache_scalar_snapshot();
        const auto before_storage = storage->diagnostics();
        const auto before_async = model->expert_async_diagnostics();
        const auto before_scheduler = model->expert_scheduler_diagnostics();
        std::vector<llama_token> accepted_ids;
        std::vector<llama_token> argmax_ids;
        std::vector<double> forward_latency_s;
        json token_telemetry = json::array();
        const size_t target_limit = fixed_context ? reference.target_ids.size() : args.horizon;
        accepted_ids.reserve(target_limit);
        argmax_ids.reserve(target_limit);
        forward_latency_s.reserve(target_limit);

        const bool first_miss_backing_read =
            before_storage.read_requests > initial_storage.read_requests &&
            before_storage.read_bytes > initial_storage.read_bytes;
        if (!first_miss_backing_read) {
            throw std::runtime_error("prefill did not prove a backing-file read from empty managed cache");
        }

        struct rusage measured_usage_before {};
        if (getrusage(RUSAGE_SELF, &measured_usage_before) != 0) {
            throw std::runtime_error("initial measured getrusage failed");
        }
        const auto measured_started = steady_clock::now();
        for (uint32_t index = 0; index < target_limit; ++index) {
            const uint32_t position = index + 1;
            llama_token input_token = index == 0 ? seed_token : accepted_ids.back();
            trace.current_position = position;
            trace.capture_internal = true;
            if (llama_route_observer_begin(context.get(), position, LLAMA_ROUTE_PHASE_DECODE) !=
                LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                throw std::runtime_error("route observer begin failed");
            }
            if (changed_routing && llama_cache_aware_routing_begin(
                    context.get(), position, LLAMA_ROUTE_PHASE_DECODE) !=
                    LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                throw std::runtime_error("cache-aware routing begin failed");
            }
            const auto forward_started = steady_clock::now();
            llama_batch batch = llama_batch_get_one(&input_token, 1);
            if (llama_decode(context.get(), batch) != 0) throw std::runtime_error("decode failed");
            llama_synchronize(context.get());
            const auto forward_completed = steady_clock::now();
            trace.capture_internal = false;
            if (!routes.error.empty() || !trace.error.empty()) {
                throw std::runtime_error("instrumentation failure: " + routes.error + trace.error);
            }
            const float * logits = llama_get_logits_ith(context.get(), -1);
            const int argmax = logits == nullptr ? -1 : finite_argmax(logits, n_vocab);
            if (argmax < 0) throw std::runtime_error("non-finite decode logits");
            const llama_token accepted = fixed_context ? reference.target_ids[index] : llama_token(argmax);
            if (!trace.capture_logits(position, accepted, logits, uint32_t(n_vocab))) {
                throw std::runtime_error(trace.error);
            }
            accepted_ids.push_back(accepted);
            argmax_ids.push_back(llama_token(argmax));
            forward_latency_s.push_back(seconds(forward_completed - forward_started));
            const auto current_cold = provider->cold_cache_scalar_snapshot();
            const auto current_storage = storage->diagnostics();
            const auto current_async = model->expert_async_diagnostics();
            const auto current_scheduler = model->expert_scheduler_diagnostics();
            token_telemetry.push_back({
                {"sequence_position", position},
                {"input_token", input_token},
                {"argmax_token", argmax},
                {"accepted_token", accepted},
                {"forward_s", forward_latency_s.back()},
                {"cold", cold_json(current_cold)},
                {"cold_delta", cold_delta_json(before_cold, current_cold)},
                {"storage_delta", storage_delta_json(before_storage, current_storage)},
                {"async_delta", async_delta_json(before_async, current_async)},
                {"scheduler_delta", scheduler_delta_json(before_scheduler, current_scheduler)},
                {"routing", routing_stats_json(llama_cache_aware_routing_get_stats(context.get()))},
            });
            if (llama_vocab_is_eog(vocab, accepted)) break;
        }
        const auto measured_completed = steady_clock::now();
        struct rusage measured_usage_after {};
        if (getrusage(RUSAGE_SELF, &measured_usage_after) != 0) {
            throw std::runtime_error("final measured getrusage failed");
        }
        trace.capture_internal = false;
        if (!trace.finish() || !routes.finish()) {
            throw std::runtime_error("instrumentation finalization failed: " + trace.error + routes.error);
        }

        const auto after_cold = provider->cold_cache_scalar_snapshot();
        const auto after_storage = storage->diagnostics();
        const auto after_async = model->expert_async_diagnostics();
        const auto after_scheduler = model->expert_scheduler_diagnostics();
        const auto after_full = provider->hot_cache_diagnostics();
        const auto routing_stats = llama_cache_aware_routing_get_stats(context.get());
        const auto observer_stats = llama_route_observer_get_stats(context.get());
        struct rusage usage {};
        if (getrusage(RUSAGE_SELF, &usage) != 0) throw std::runtime_error("getrusage failed");
        const uint64_t swap_kib = vm_swap_kib();
        const uint64_t expected_route_records = uint64_t(accepted_ids.size())*routed_layer_count;
        if (routes.records != expected_route_records || observer_stats.failures != 0 ||
            trace.moe_records != expected_route_records || trace.hidden_records != expected_route_records ||
            trace.logits_records != accepted_ids.size() || routing_stats.failures != 0 ||
            after_storage.cancelled_reads != 0 || after_storage.short_reads != 0 ||
            after_storage.io_errors != 0 || after_async.buffered_fallback_operations != 0 ||
            after_async.synchronous_fallback_operations != 0 ||
            (after_async.fallback_reason_mask & ~allowed_async_fallback_mask) != 0 ||
            after_scheduler.active_requests != 0 || after_scheduler.queued_requests != 0 ||
            after_scheduler.terminal_failed != 0 || after_scheduler.terminal_cancelled != 0 ||
            after_scheduler.stale_completions != 0 ||
            after_full.cold_current_hot_refs != 0 || after_full.cold_current_transfer_refs != 0 ||
            after_full.cold_current_request_refs != 0 ||
            after_full.cold_current_cpu_execution_refs != 0 ||
            after_full.cold_current_batch_refs != 0 || after_full.current_pins != 0 ||
            swap_kib != 0 ||
            (!changed_routing && (routing_stats.ubatches != 0 || routing_stats.layers != 0 ||
                                  routing_stats.decisions != 0 || routing_stats.swaps != 0))) {
            throw std::runtime_error("measured production-path or instrumentation invariant failed");
        }
        if (changed_routing && (routes.swaps != routing_stats.swaps ||
            std::abs(routes.corrected_regret - routing_stats.cumulative_score_regret) > 1e-3)) {
            throw std::runtime_error("observer regret does not reproduce runtime routing statistics");
        }

        std::vector<llama_token> phase_tokens;
        phase_tokens.reserve(accepted_ids.size() + 1);
        phase_tokens.push_back(seed_token);
        phase_tokens.insert(phase_tokens.end(), accepted_ids.begin(), accepted_ids.end());
        const phase_accounting phases = classify_phases(vocab, phase_tokens, phase_boundary);
        for (size_t index = 0; index < token_telemetry.size(); ++index) {
            token_telemetry[index]["input_generation_phase"] = phases.labels[index];
            token_telemetry[index]["target_generation_phase"] = phases.labels[index + 1];
        }
        json special_tokens = json::array();
        for (size_t index = 0; index < phase_tokens.size(); ++index) {
            const llama_token token = phase_tokens[index];
            const bool boundary_member = std::find(phase_boundary.begin(), phase_boundary.end(), token) !=
                phase_boundary.end();
            if (boundary_member || llama_vocab_is_control(vocab, token) || llama_vocab_is_eog(vocab, token)) {
                special_tokens.push_back({
                    {"sequence_position", int64_t(index)},
                    {"token_id", token},
                    {"piece", token_piece(vocab, token)},
                    {"is_control", llama_vocab_is_control(vocab, token)},
                    {"is_eog", llama_vocab_is_eog(vocab, token)},
                    {"generation_phase", phases.labels[index]},
                });
            }
        }

        const double measured_s = seconds(measured_completed - measured_started);
        const double measured_user_cpu_s =
            seconds(measured_usage_after.ru_utime) - seconds(measured_usage_before.ru_utime);
        const double measured_system_cpu_s =
            seconds(measured_usage_after.ru_stime) - seconds(measured_usage_before.ru_stime);
        json command = json::array();
        for (int index = 0; index < argc; ++index) command.push_back(argv[index]);
        const json result = {
            {"schema_version", "issue99-quality-cell-v1"},
            {"status", "pass"},
            {"exit_status", 0},
            {"case", {
                {"id", selected_case.id},
                {"semantic_family", selected_case.family},
                {"length_level", selected_case.length_level},
                {"templated_prompt_tokens", prompt.size()},
            }},
            {"policy", args.policy},
            {"intervention", args.intervention},
            {"command", command},
            {"model_path", args.model},
            {"prompt_corpus", args.prompt_corpus},
            {"execution", {
                {"backend", "CPU"}, {"n_gpu_layers", 0}, {"gpu_device_count", gpu_devices},
                {"cuda_dependency", "none"}, {"load_mode", "DIRECT_IO"},
                {"runtime_mode", "PERFORMANCE"}, {"n_ctx", args.n_ctx},
                {"n_batch", 1}, {"n_ubatch", 1}, {"threads", args.threads},
                {"current_layer_issue_mode", args.issue_mode},
                {"native_io_uring", initial_async.io_uring_enabled},
                {"registered_file_count", initial_async.registered_file_count},
                {"registered_buffer_count", initial_async.registered_buffer_count},
                {"buffer_registration_error", initial_async.buffer_registration_error},
                {"async_fallback_reason_mask", initial_async.fallback_reason_mask},
                {"capacity_request_mode", args.cold_cache_bytes == 0 ? "AUTO_QUALIFICATION" : "EXPLICIT"},
            }},
            {"routing", {
                {"enabled", changed_routing},
                {"candidate_count", changed_routing ? candidate_count : 0},
                {"max_swaps", max_swaps},
                {"max_score_regret", max_regret},
                {"tier_source", changed_routing ? "real-provider-cold-cache" : "disabled"},
                {"stats", routing_stats_json(routing_stats)},
                {"observer_recomputed", {
                    {"records", routes.records}, {"swaps", routes.swaps},
                    {"cumulative_corrected_regret", routes.corrected_regret},
                    {"cumulative_raw_probability_regret", routes.raw_regret},
                }},
            }},
            {"observer", {
                {"candidate_count", candidate_count}, {"selected_count", selected_count},
                {"routed_layers", routed_layer_count}, {"records", routes.records},
                {"stats", {
                    {"ubatches", observer_stats.ubatches}, {"layers", observer_stats.layers},
                    {"copy_bytes", observer_stats.copy_bytes},
                    {"explicit_synchronizations", observer_stats.explicit_synchronizations},
                    {"failures", observer_stats.failures},
                }},
                {"path", args.route_output},
            }},
            {"preflight", {
                {"pass", true}, {"process_start_occupancy", initial_cold.occupancy},
                {"cpu_cold_only", initial_full.cpu_cold_only},
                {"first_miss_backing_read", first_miss_backing_read},
                {"initial_cold", cold_json(initial_cold)},
                {"initial_storage", storage_json(initial_storage)},
                {"system_memory", system_memory_json(initial_full)},
            }},
            {"prefill", {
                {"tokens", prompt.size()}, {"tokens_to_full", tokens_to_full},
                {"elapsed_s", seconds(prefill_completed - prefill_started)},
                {"seed_token", seed_token},
            }},
            {"reference", {
                {"source", fixed_context ? args.reference_sequence : "generated-by-this-exact-arm"},
                {"teacher_forced", fixed_context},
                {"seed_token", seed_token},
                {"target_ids", accepted_ids},
                {"argmax_ids", argmax_ids},
                {"target_hash", hex_u64(token_hash(accepted_ids))},
                {"horizon_limit", args.horizon},
                {"achieved_horizon", accepted_ids.size()},
            }},
            {"generation_phase", {
                {"boundary_literal", phase_boundary_text},
                {"boundary_token_ids", phase_boundary},
                {"transition_start_position", phases.transition_start},
                {"final_answer_start_position", phases.final_start},
                {"eog_position", phases.eog_position},
                {"final_answer_reached", phases.final_start >= 0},
                {"special_token_observations", special_tokens},
            }},
            {"measured", {
                {"decode_forwards", accepted_ids.size()}, {"decode_s", measured_s},
                {"wall_time_context_only_not_performance_authority", true},
                {"cold_before", cold_json(before_cold)}, {"cold_after", cold_json(after_cold)},
                {"cold_delta", cold_delta_json(before_cold, after_cold)},
                {"storage_delta", storage_delta_json(before_storage, after_storage)},
                {"async_delta", async_delta_json(before_async, after_async)},
                {"scheduler_delta", scheduler_delta_json(before_scheduler, after_scheduler)},
                {"user_cpu_s", measured_user_cpu_s}, {"system_cpu_s", measured_system_cpu_s},
                {"token_telemetry", token_telemetry},
            }},
            {"quality_trace", {
                {"enabled", true}, {"path", trace.path}, {"records", trace.records},
                {"moe_records", trace.moe_records}, {"hidden_records", trace.hidden_records},
                {"logits_records", trace.logits_records}, {"payload_bytes", trace.payload_bytes},
                {"file_bytes", trace.file_bytes}, {"failures", trace.error.empty() ? 0 : 1},
                {"retention", "ephemeral-bounded-until-paired-scalarization"},
                {"maximum_live_trace_files", 2},
            }},
            {"resources", {
                {"peak_rss_kib", usage.ru_maxrss}, {"vm_swap_kib", swap_kib},
                {"terminal_references", terminal_reference_json(after_full)},
                {"system_memory", system_memory_json(after_full)},
                {"terminal_scheduler_active_requests", after_scheduler.active_requests},
                {"terminal_scheduler_queued_requests", after_scheduler.queued_requests},
            }},
            {"timing", {
                {"model_load_s", seconds(model_loaded - model_load_started)},
                {"context_init_s", seconds(context_loaded - context_load_started)},
                {"process_elapsed_s", seconds(steady_clock::now() - process_started)},
                {"forward_latency_s", forward_latency_s},
            }},
        };

        std::ofstream output(args.output, std::ios::trunc);
        if (!output) throw std::runtime_error("unable to open result output");
        output << result.dump(2) << '\n';
        output.flush();
        if (!output) throw std::runtime_error("unable to write result output");
        output.close();
        std::printf("ISSUE99_QUALITY_PROBE status=pass case=%s policy=%s intervention=%s horizon=%zu\n",
            args.case_id.c_str(), args.policy.c_str(), args.intervention.c_str(), accepted_ids.size());
        context.reset();
        model.reset();
        llama_backend_free();
        return 0;
    } catch (const std::exception & error) {
        std::fprintf(stderr, "issue99-quality-probe: %s\n", error.what());
        return 1;
    }
}

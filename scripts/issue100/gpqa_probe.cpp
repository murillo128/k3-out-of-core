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
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <limits>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/resource.h>
#include <unistd.h>
#include <vector>

using json = nlohmann::ordered_json;
using steady_clock = std::chrono::steady_clock;

namespace {

constexpr uint32_t candidate_count = 32;
constexpr uint32_t routed_layers = 92;
constexpr uint64_t expert_bundle_bytes = 17547264;
constexpr uint32_t capacity_floor_slots = 5874;
constexpr uint64_t auto_cache_request_bytes = 0;
constexpr uint32_t frozen_n_ctx = 7168;
constexpr uint32_t frozen_threads = 32;
constexpr uint32_t frozen_max_generated = 4096;
constexpr float frozen_top_p = 0.95f;
constexpr float frozen_temperature = 1.0f;
constexpr uint32_t frozen_max_swaps = 2;
constexpr float frozen_max_regret = 0.007303759455680847f;
constexpr uint64_t frozen_memlock_bytes = UINT64_C(512)*1024*1024;

struct arguments {
    std::string model;
    std::string input;
    std::string output;
    std::string progress;
    std::string arm;
    std::string issue_mode = "BATCHED";
    uint32_t seed = 0;
    uint32_t max_generated = frozen_max_generated;
    uint32_t n_ctx = frozen_n_ctx;
    uint32_t threads = frozen_threads;
};

bool parse_u32(const char * text, uint32_t & value) {
    char * end = nullptr;
    const unsigned long parsed = std::strtoul(text, &end, 10);
    if (end == text || *end != '\0' || parsed > UINT32_MAX) return false;
    value = uint32_t(parsed);
    return true;
}

bool parse_arguments(int argc, char ** argv, arguments & args) {
    for (int index = 1; index < argc; ++index) {
        const std::string option = argv[index];
        if (index + 1 >= argc) return false;
        const char * value = argv[++index];
        if (option == "--model") args.model = value;
        else if (option == "--input") args.input = value;
        else if (option == "--output") args.output = value;
        else if (option == "--progress") args.progress = value;
        else if (option == "--arm") args.arm = value;
        else if (option == "--issue-mode") args.issue_mode = value;
        else if (option == "--seed") {
            if (!parse_u32(value, args.seed)) return false;
        } else if (option == "--max-generated") {
            if (!parse_u32(value, args.max_generated)) return false;
        } else if (option == "--n-ctx") {
            if (!parse_u32(value, args.n_ctx)) return false;
        } else if (option == "--threads") {
            if (!parse_u32(value, args.threads)) return false;
        } else {
            return false;
        }
    }
    return !args.model.empty() && !args.input.empty() && !args.output.empty() &&
        !args.progress.empty() && (args.arm == "EXACT" || args.arm == "S2_P50") &&
        (args.issue_mode == "SERIAL" || args.issue_mode == "BATCHED") &&
        args.max_generated > 0 && args.max_generated <= frozen_max_generated &&
        args.n_ctx == frozen_n_ctx && args.threads == frozen_threads;
}

double seconds(steady_clock::duration duration) {
    return std::chrono::duration<double>(duration).count();
}

double seconds(const timeval & value) {
    return double(value.tv_sec) + double(value.tv_usec)/1000000.0;
}

template<class T> T delta(T after, T before) {
    return after >= before ? after - before : 0;
}

std::string bytes_hex(const std::string & value) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (const unsigned char byte : value) out << std::setw(2) << unsigned(byte);
    return out.str();
}

std::string hex_u64(uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setfill('0') << std::setw(16) << value;
    return out.str();
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

bool finite_logits(const float * logits, int32_t count) {
    return logits != nullptr && count > 0 &&
        std::all_of(logits, logits + count, [](float value) { return std::isfinite(value); });
}

struct process_resource_snapshot {
    uint64_t memlock_soft_bytes = 0;
    uint64_t memlock_hard_bytes = 0;
};

process_resource_snapshot process_resources() {
    rlimit limit{};
    if (getrlimit(RLIMIT_MEMLOCK, &limit) != 0) {
        throw std::runtime_error("memlock resource limit unavailable");
    }
    const auto bounded = [](rlim_t value) {
        return value == RLIM_INFINITY ? UINT64_MAX : uint64_t(value);
    };
    return {bounded(limit.rlim_cur), bounded(limit.rlim_max)};
}

json process_resource_json(const process_resource_snapshot & value) {
    return {
        {"rlimit_memlock_soft_bytes", value.memlock_soft_bytes},
        {"rlimit_memlock_hard_bytes", value.memlock_hard_bytes},
    };
}

std::string token_piece(const llama_vocab * vocab, llama_token token) {
    std::vector<char> buffer(64);
    int32_t count = llama_token_to_piece(
        vocab, token, buffer.data(), int32_t(buffer.size()), 0, true);
    if (count < 0) {
        buffer.resize(size_t(-count));
        count = llama_token_to_piece(
            vocab, token, buffer.data(), int32_t(buffer.size()), 0, true);
    }
    if (count < 0 || size_t(count) > buffer.size()) {
        throw std::runtime_error("token-to-piece failed");
    }
    return std::string(buffer.data(), size_t(count));
}

json cold_json(const llm_expert_cold_scalar_snapshot & value) {
    return {
        {"requested_bytes", value.requested_bytes}, {"actual_bytes", value.actual_bytes},
        {"capacity", value.capacity}, {"occupancy", value.occupancy},
        {"requests", value.requests}, {"hits", value.hits}, {"misses", value.misses},
        {"admissions", value.admissions}, {"evictions", value.evictions},
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
        {"occupancy_before", before.occupancy}, {"occupancy_after", after.occupancy},
    };
}

json storage_json(const llm_expert_storage_diagnostics & value) {
    return {
        {"read_requests", value.read_requests}, {"read_chunks", value.read_chunks},
        {"read_bytes", value.read_bytes}, {"cancelled_reads", value.cancelled_reads},
        {"short_reads", value.short_reads}, {"io_errors", value.io_errors},
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
        {"buffered_fallback_operations", delta(
            after.buffered_fallback_operations, before.buffered_fallback_operations)},
        {"synchronous_fallback_operations", delta(
            after.synchronous_fallback_operations, before.synchronous_fallback_operations)},
    };
}

json transport_json(const llm_expert_async_diagnostics & value) {
    return {
        {"native_io_uring", value.io_uring_enabled},
        {"staging_ceiling_bytes", value.staging_ceiling_bytes},
        {"direct_staging_lane_count", value.direct_staging_lane_count},
        {"registered_file_count", value.registered_file_count},
        {"registered_buffer_count", value.registered_buffer_count},
        {"registered_buffer_bytes", value.registered_buffer_bytes},
        {"file_registration_error", value.file_registration_error},
        {"buffer_registration_error", value.buffer_registration_error},
        {"direct_staging_error", value.direct_staging_error},
        {"io_uring_setup_error", value.io_uring_setup_error},
        {"io_uring_probe_error", value.io_uring_probe_error},
        {"io_uring_runtime_error", value.io_uring_runtime_error},
        {"async_fallback_reason_mask", value.fallback_reason_mask},
        {"buffered_fallback_operations", value.buffered_fallback_operations},
        {"synchronous_fallback_operations", value.synchronous_fallback_operations},
    };
}

json scheduler_json(const llm_expert_scheduler_diagnostics & value) {
    return {
        {"flights_created", value.flights_created}, {"joins", value.joins},
        {"terminal_complete", value.terminal_complete},
        {"terminal_failed", value.terminal_failed},
        {"terminal_cancelled", value.terminal_cancelled},
        {"terminal_releases", value.terminal_releases},
        {"stale_completions", value.stale_completions},
        {"active_requests", value.active_requests}, {"queued_requests", value.queued_requests},
    };
}

json system_memory_json(const llm_hot_cache_diagnostics & value) {
    return {
        {"requested_pool_bytes", value.system_memory_requested_pool_bytes},
        {"selected_pool_bytes", value.system_memory_selected_pool_bytes},
        {"safe_pool_bytes", value.system_memory_safe_pool_bytes},
        {"admission_safe_pool_bytes", value.system_memory_admission_safe_pool_bytes},
        {"effective_limit_bytes", value.system_memory_effective_limit_bytes},
        {"limit_headroom_bytes", value.system_memory_limit_headroom_bytes},
        {"available_headroom_bytes", value.system_memory_available_headroom_bytes},
        {"measured_non_pool_committed_bytes", value.system_memory_measured_non_pool_committed_bytes},
        {"runtime_obligation_bytes", value.system_memory_runtime_obligation_bytes},
        {"reported_runtime_obligation_bytes", value.system_memory_reported_runtime_obligation_bytes},
        {"observed_runtime_obligation_bytes", value.system_memory_observed_runtime_obligation_bytes},
        {"credited_runtime_obligation_bytes", value.system_memory_credited_runtime_obligation_bytes},
        {"remaining_runtime_reserve_bytes", value.system_memory_remaining_runtime_reserve_bytes},
        {"system_reserve_bytes", value.system_memory_system_reserve_bytes},
        {"runtime_reserve_bytes", value.system_memory_runtime_reserve_bytes},
        {"hysteresis_bytes", value.system_memory_hysteresis_bytes},
        {"model_file_virtual_bytes", value.system_memory_model_file_virtual_bytes},
        {"model_file_cache_resident_bytes", value.system_memory_model_file_cache_resident_bytes},
        {"other_process_resident_bytes", value.system_memory_other_process_resident_bytes},
        {"memory_current_bytes", value.system_memory_current_bytes},
        {"memory_available_bytes", value.system_memory_available_bytes},
        {"calculated_available_bytes", value.system_memory_calculated_available_bytes},
        {"incoming_bytes", value.system_memory_incoming_bytes},
        {"required_free_bytes", value.system_memory_required_free_bytes},
        {"selected_pool_slots", value.system_memory_selected_pool_slots},
        {"resolve_memory_current_bytes", value.system_memory_resolve_current_bytes},
        {"resolve_memory_available_bytes", value.system_memory_resolve_available_bytes},
        {"resolve_calculated_available_bytes", value.system_memory_resolve_calculated_available_bytes},
        {"resolve_required_free_bytes", value.system_memory_resolve_required_free_bytes},
        {"obligation_memory_current_bytes", value.system_memory_obligation_current_bytes},
        {"obligation_memory_available_bytes", value.system_memory_obligation_available_bytes},
        {"obligation_calculated_available_bytes", value.system_memory_obligation_calculated_available_bytes},
        {"obligation_required_free_bytes", value.system_memory_obligation_required_free_bytes},
        {"pressure_samples", value.system_memory_pressure_samples},
        {"pressure_rejections", value.system_memory_pressure_rejections},
        {"autofit", value.system_memory_autofit},
        {"budget_frozen", value.system_memory_budget_frozen},
        {"pressure_circuit_open", value.system_memory_pressure_circuit_open},
        {"stage", value.system_memory_stage},
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

json routing_stats_json(const llama_cache_aware_routing_stats & value) {
    return {
        {"ubatches", value.ubatches}, {"layers", value.layers},
        {"decisions", value.decisions}, {"changed_decisions", value.changed_decisions},
        {"swaps", value.swaps},
        {"cumulative_score_regret", value.cumulative_score_regret},
        {"explicit_synchronizations", value.explicit_synchronizations},
        {"failures", value.failures},
    };
}

struct input_case {
    std::string item_id;
    std::string prompt_sha256;
    std::string rendered_prompt;
    uint32_t expected_tokens = 0;
    bool scored = false;
    std::vector<llama_token> tokens;
};

input_case load_input(const arguments & args, const llama_vocab * vocab) {
    std::ifstream source(args.input);
    if (!source) throw std::runtime_error("unable to open input JSON");
    json value;
    source >> value;
    if (value.value("schema_version", "") != "issue100-probe-input-v1") {
        throw std::runtime_error("input schema mismatch");
    }
    input_case result;
    result.item_id = value.at("item_id").get<std::string>();
    result.prompt_sha256 = value.at("prompt_sha256").get<std::string>();
    result.rendered_prompt = value.at("rendered_prompt").get<std::string>();
    result.expected_tokens = value.at("prompt_tokens").get<uint32_t>();
    result.scored = value.at("scored").get<bool>();
    if (result.item_id.empty() || result.prompt_sha256.size() != 64 || result.expected_tokens == 0) {
        throw std::runtime_error("input identity is invalid");
    }
    const int count = -llama_tokenize(
        vocab, result.rendered_prompt.data(), result.rendered_prompt.size(), nullptr, 0, true, true);
    if (count <= 0 || uint32_t(count) != result.expected_tokens) {
        throw std::runtime_error("prompt token identity mismatch");
    }
    result.tokens.resize(size_t(count));
    if (llama_tokenize(vocab, result.rendered_prompt.data(), result.rendered_prompt.size(),
            result.tokens.data(), result.tokens.size(), true, true) != count) {
        throw std::runtime_error("prompt tokenization failed");
    }
    if (result.tokens.size() + args.max_generated > args.n_ctx) {
        throw std::runtime_error("prompt plus maximum generation exceeds context");
    }
    return result;
}

struct progress_writer {
    std::ofstream destination;

    explicit progress_writer(const std::string & path) : destination(path, std::ios::trunc) {
        if (!destination) throw std::runtime_error("unable to open progress stream");
    }

    void write(const json & value) {
        destination << value.dump() << '\n';
        destination.flush();
        if (!destination) throw std::runtime_error("progress stream write failed");
    }
};

void write_json(const std::string & path, const json & value) {
    std::ofstream destination(path, std::ios::trunc);
    if (!destination) throw std::runtime_error("unable to open result output");
    destination << value.dump(2) << '\n';
    destination.flush();
    if (!destination) throw std::runtime_error("result write failed");
    destination.close();
    if (!destination) throw std::runtime_error("result close failed");
}

void write_provider_failure(
        const arguments & args,
        const input_case & input,
        llm_expert_weight_provider * provider,
        const char * probe_stage,
        bool prompt_inference_started,
        const process_resource_snapshot & resources,
        const llm_expert_async_diagnostics & transport) {
    if (provider == nullptr) return;
    const auto cold = provider->cold_cache_scalar_snapshot();
    const auto memory = provider->hot_cache_diagnostics();
    write_json(args.output, {
        {"schema_version", "issue100-gpqa-probe-result-v2"},
        {"status", "provider-failure"},
        {"item", {
            {"id", input.item_id}, {"scored", input.scored},
            {"prompt_sha256", input.prompt_sha256},
            {"prompt_tokens", input.tokens.size()},
        }},
        {"arm", args.arm}, {"seed", args.seed},
        {"process_entry", process_resource_json(resources)},
        {"execution", {
            {"capacity_request_mode", "AUTO"},
            {"capacity_request_bytes", auto_cache_request_bytes},
            {"auto_resolved_slots", cold.capacity},
            {"auto_resolved_bytes", memory.system_memory_selected_pool_bytes},
        }},
        {"failure", {
            {"probe_stage", probe_stage},
            {"provider_stage", memory.system_memory_stage},
            {"pressure_rejection_reason", memory.system_memory_pressure_rejection_reason},
            {"prompt_inference_started", prompt_inference_started},
            {"transport", transport_json(transport)},
            {"system_memory", system_memory_json(memory)},
        }},
    });
}

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::fprintf(stderr,
            "usage: %s --model GGUF --input JSON --output JSON --progress JSONL "
            "--arm EXACT|S2_P50 --seed UINT32 "
            "[--max-generated 4096 --n-ctx 7168 --threads 32 --issue-mode BATCHED]\n",
            argv[0]);
        return 2;
    }

    try {
        const auto process_started = steady_clock::now();
        const auto entry_resources = process_resources();
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level == GGML_LOG_LEVEL_ERROR) std::fputs(text, stderr);
        }, nullptr);
        ggml_backend_load_all();
        uint32_t gpu_devices = 0;
        for (size_t index = 0; index < ggml_backend_dev_count(); ++index) {
            gpu_devices += ggml_backend_dev_type(ggml_backend_dev_get(index)) ==
                GGML_BACKEND_DEVICE_TYPE_GPU;
        }
        if (gpu_devices != 0) {
            throw std::runtime_error("GPU backend/device present in CPU-only GPQA probe");
        }

        auto model_params = llama_model_default_params();
        model_params.n_gpu_layers = 0;
        model_params.use_extra_bufts = false;
        model_params.load_mode = LLAMA_LOAD_MODE_DIRECT_IO;
        model_params.expert_weights_mode = LLAMA_EXPERT_WEIGHTS_MODE_COLD_CACHE;
        model_params.expert_runtime_mode = LLAMA_EXPERT_RUNTIME_MODE_PERFORMANCE;
        model_params.expert_hot_cache_capacity = 0;
        model_params.expert_cold_cache_bytes = auto_cache_request_bytes;
        model_params.expert_transfer_ring_bytes = 0;
        model_params.expert_miss_policy = LLAMA_EXPERT_MISS_POLICY_CPU_FALLBACK;
        model_params.expert_io_trace_capacity = 0;
        model_params.expert_background_promotion = false;
        model_params.expert_async_cold_fill = false;

        const auto model_load_started = steady_clock::now();
        llama_model_ptr model(llama_model_load_from_file(args.model.c_str(), model_params));
        if (!model) throw std::runtime_error("model load failed");
        const auto model_loaded = steady_clock::now();
        if (!model->uses_cpu_cold_cache()) {
            throw std::runtime_error("CPU cold-only model topology not selected");
        }
        auto * provider = model->expert_weight_provider();
        if (provider == nullptr ||
            !provider->debug_set_host_resident_serial_issue_for_testing(
                args.issue_mode == "SERIAL").is_ready()) {
            throw std::runtime_error("unable to configure internal issue mode");
        }
        auto * storage = model->expert_storage();
        if (storage == nullptr) throw std::runtime_error("CPU expert storage unavailable");
        const llama_vocab * vocab = llama_model_get_vocab(model.get());
        const int32_t n_vocab = llama_vocab_n_tokens(vocab);
        const input_case input = load_input(args, vocab);

        auto context_params = llama_context_default_params();
        context_params.n_ctx = args.n_ctx;
        context_params.n_batch = 1;
        context_params.n_ubatch = 1;
        context_params.n_threads = int32_t(args.threads);
        context_params.n_threads_batch = int32_t(args.threads);
        context_params.no_perf = true;
        const auto context_load_started = steady_clock::now();
        llama_context_ptr context(llama_init_from_model(model.get(), context_params));
        if (!context) {
            write_provider_failure(
                args, input, provider, "context_initialization", false,
                entry_resources, model->expert_async_diagnostics());
            throw std::runtime_error("context initialization failed");
        }
        const auto context_loaded = steady_clock::now();

        const auto initial_cold = provider->cold_cache_scalar_snapshot();
        const auto initial_storage = storage->diagnostics();
        const auto initial_async = model->expert_async_diagnostics();
        const auto initial_scheduler = model->expert_scheduler_diagnostics();
        const auto initial_full = provider->hot_cache_diagnostics();
        const uint64_t resolved_cache_bytes = initial_full.system_memory_selected_pool_bytes;
        const uint64_t resolved_cache_slots = initial_cold.capacity;
        if (entry_resources.memlock_soft_bytes != frozen_memlock_bytes ||
            entry_resources.memlock_hard_bytes != frozen_memlock_bytes ||
            !initial_cold.available || initial_cold.occupancy != 0 ||
            resolved_cache_bytes == 0 || resolved_cache_bytes % expert_bundle_bytes != 0 ||
            resolved_cache_slots != resolved_cache_bytes/expert_bundle_bytes ||
            initial_cold.requested_bytes != resolved_cache_bytes ||
            initial_cold.actual_bytes != resolved_cache_bytes ||
            initial_full.system_memory_requested_pool_bytes != auto_cache_request_bytes ||
            !initial_full.system_memory_budget_frozen || !initial_full.system_memory_autofit ||
            initial_full.system_memory_pressure_rejections != 0 ||
            initial_full.system_memory_pressure_circuit_open ||
            !initial_full.cpu_cold_only || initial_full.requested_capacity != 0 ||
            initial_full.effective_capacity != 0 || initial_full.pool_bytes != 0 ||
            !initial_full.slots.empty() ||
            initial_storage.cancelled_reads != 0 || initial_storage.short_reads != 0 ||
            initial_storage.io_errors != 0 ||
            initial_storage.direct_source_count != initial_storage.source_file_count ||
            initial_storage.direct_unsupported_source_count != 0 || !initial_async.io_uring_enabled ||
            initial_async.registered_buffer_count != 1 ||
            initial_async.registered_buffer_bytes == 0 ||
            initial_async.registered_buffer_bytes > initial_async.staging_ceiling_bytes ||
            initial_async.registered_buffer_bytes >= frozen_memlock_bytes ||
            initial_async.file_registration_error != 0 ||
            initial_async.buffer_registration_error != 0 ||
            initial_async.direct_staging_error != 0 || initial_async.io_uring_setup_error != 0 ||
            initial_async.io_uring_probe_error != 0 || initial_async.io_uring_runtime_error != 0 ||
            initial_async.fallback_reason_mask != 0 ||
            initial_async.buffered_fallback_operations != 0 ||
            initial_async.synchronous_fallback_operations != 0) {
            write_provider_failure(
                args, input, provider, "initial_production_path_validation", false,
                entry_resources, initial_async);
            throw std::runtime_error("CPU production-path initial validation failed");
        }
        if (resolved_cache_slots < capacity_floor_slots) {
            write_json(args.output, {
                {"schema_version", "issue100-gpqa-probe-result-v2"},
                {"status", "halted-below-capacity-floor"},
                {"item", {
                    {"id", input.item_id}, {"scored", input.scored},
                    {"prompt_sha256", input.prompt_sha256},
                    {"prompt_tokens", input.tokens.size()},
                }},
                {"arm", args.arm}, {"seed", args.seed},
                {"process_entry", process_resource_json(entry_resources)},
                {"execution", {
                    {"capacity_request_mode", "AUTO"},
                    {"capacity_request_bytes", auto_cache_request_bytes},
                    {"auto_resolved_slots", resolved_cache_slots},
                    {"auto_resolved_bytes", resolved_cache_bytes},
                }},
                {"preflight", {
                    {"pass", false}, {"prompt_inference_started", false},
                    {"reason", "AUTO_RESOLVED_SLOTS_BELOW_FLOOR"},
                    {"capacity_floor_slots", capacity_floor_slots},
                    {"capacity_floor_bytes", uint64_t(capacity_floor_slots)*expert_bundle_bytes},
                    {"initial_cold", cold_json(initial_cold)},
                    {"system_memory", system_memory_json(initial_full)},
                }},
            });
            throw std::runtime_error("AUTO resolved capacity below 5874-slot floor");
        }

        progress_writer progress(args.progress);
        progress.write({
            {"record_type", "metadata"},
            {"schema_version", "issue100-probe-progress-v1"},
            {"item_id", input.item_id}, {"arm", args.arm}, {"seed", args.seed},
            {"scored", input.scored}, {"prompt_sha256", input.prompt_sha256},
            {"prompt_tokens", input.tokens.size()}, {"max_generated", args.max_generated},
            {"capacity_request_mode", "AUTO"},
            {"auto_resolved_slots", resolved_cache_slots},
            {"auto_resolved_bytes", resolved_cache_bytes},
        });

        const auto prefill_started = steady_clock::now();
        bool cache_became_full = false;
        uint32_t tokens_to_full = 0;
        for (uint32_t index = 0; index < input.tokens.size(); ++index) {
            llama_token token = input.tokens[index];
            llama_batch batch = llama_batch_get_one(&token, 1);
            if (llama_decode(context.get(), batch) != 0) {
                write_provider_failure(
                    args, input, provider, "prefill_decode", true,
                    entry_resources, model->expert_async_diagnostics());
                throw std::runtime_error("EXACT prefill decode failed");
            }
            llama_synchronize(context.get());
            const float * logits = llama_get_logits_ith(context.get(), -1);
            if (!finite_logits(logits, n_vocab)) {
                throw std::runtime_error("non-finite prefill logits");
            }
            const auto snapshot = provider->cold_cache_scalar_snapshot();
            if (!cache_became_full && snapshot.occupancy == snapshot.capacity) {
                cache_became_full = true;
                tokens_to_full = index + 1;
            }
        }
        const auto prefill_completed = steady_clock::now();
        if (!cache_became_full) {
            throw std::runtime_error("managed expert cache did not fill during GPQA prefill");
        }

        const bool changed_routing = args.arm == "S2_P50";
        if (changed_routing) {
            const llama_cache_aware_routing_config routing = {
                true, candidate_count, frozen_max_swaps, frozen_max_regret,
                nullptr, nullptr, nullptr,
            };
            if (llama_set_cache_aware_routing(context.get(), &routing) !=
                    LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                throw std::runtime_error("provider-backed S2 routing configuration failed");
            }
        }
        context->sched_reserve();
        llama_cache_aware_routing_reset_stats(context.get());

        auto sampler_params = llama_sampler_chain_default_params();
        sampler_params.no_perf = true;
        llama_sampler_ptr sampler(llama_sampler_chain_init(sampler_params));
        if (!sampler) throw std::runtime_error("sampler chain allocation failed");
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_top_p(frozen_top_p, 1));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_temp(frozen_temperature));
        llama_sampler_chain_add(sampler.get(), llama_sampler_init_dist(args.seed));
        if (llama_sampler_get_seed(sampler.get()) != args.seed) {
            throw std::runtime_error("sampler seed identity mismatch");
        }

        const auto before_cold = provider->cold_cache_scalar_snapshot();
        const auto before_storage = storage->diagnostics();
        const auto before_async = model->expert_async_diagnostics();
        const auto before_scheduler = model->expert_scheduler_diagnostics();
        const bool first_miss_backing_read =
            before_storage.read_requests > initial_storage.read_requests &&
            before_storage.read_bytes > initial_storage.read_bytes;
        if (!first_miss_backing_read) {
            throw std::runtime_error("prefill did not prove backing-file reads from an empty cache");
        }

        std::vector<llama_token> generated_ids;
        std::vector<std::string> piece_hex;
        std::vector<double> sample_latency_s;
        std::vector<double> forward_latency_s;
        generated_ids.reserve(args.max_generated);
        piece_hex.reserve(args.max_generated);
        sample_latency_s.reserve(args.max_generated);
        forward_latency_s.reserve(args.max_generated > 0 ? args.max_generated - 1 : 0);
        bool stopped_eog = false;
        bool truncated = false;
        uint32_t decode_forward_tokens = 0;

        struct rusage measured_usage_before {};
        if (getrusage(RUSAGE_SELF, &measured_usage_before) != 0) {
            throw std::runtime_error("initial generation getrusage failed");
        }
        const auto generation_wall_started = steady_clock::now();
        for (uint32_t ordinal = 1; ordinal <= args.max_generated; ++ordinal) {
            const float * logits = llama_get_logits_ith(context.get(), -1);
            if (!finite_logits(logits, n_vocab)) {
                throw std::runtime_error("non-finite generation logits");
            }
            const auto sample_started = steady_clock::now();
            const llama_token sampled = llama_sampler_sample(sampler.get(), context.get(), -1);
            const auto sample_completed = steady_clock::now();
            if (sampled < 0 || sampled >= n_vocab) {
                throw std::runtime_error("sampler returned invalid token ID");
            }
            const bool eog = llama_vocab_is_eog(vocab, sampled);
            const std::string raw_piece = token_piece(vocab, sampled);
            generated_ids.push_back(sampled);
            piece_hex.push_back(bytes_hex(raw_piece));
            sample_latency_s.push_back(seconds(sample_completed - sample_started));
            progress.write({
                {"record_type", "token"}, {"ordinal", ordinal},
                {"token_id", sampled}, {"piece_hex", piece_hex.back()},
                {"eog", eog}, {"sample_s", sample_latency_s.back()},
            });
            if (eog) {
                stopped_eog = true;
                break;
            }
            if (ordinal == args.max_generated) {
                truncated = true;
                break;
            }

            if (changed_routing && llama_cache_aware_routing_begin(
                    context.get(), ordinal, LLAMA_ROUTE_PHASE_DECODE) !=
                    LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                throw std::runtime_error("S2 decode route transaction begin failed");
            }
            const auto forward_started = steady_clock::now();
            llama_token input_token = sampled;
            llama_batch batch = llama_batch_get_one(&input_token, 1);
            if (llama_decode(context.get(), batch) != 0) {
                write_provider_failure(
                    args, input, provider, "generated_decode", true,
                    entry_resources, model->expert_async_diagnostics());
                throw std::runtime_error("generated-token decode failed");
            }
            llama_synchronize(context.get());
            const auto forward_completed = steady_clock::now();
            forward_latency_s.push_back(seconds(forward_completed - forward_started));
            decode_forward_tokens++;
        }
        const auto generation_wall_completed = steady_clock::now();
        struct rusage measured_usage_after {};
        if (getrusage(RUSAGE_SELF, &measured_usage_after) != 0) {
            throw std::runtime_error("final generation getrusage failed");
        }
        if (stopped_eog == truncated || generated_ids.empty()) {
            throw std::runtime_error("generation stop identity is invalid");
        }

        const auto after_cold = provider->cold_cache_scalar_snapshot();
        const auto after_storage = storage->diagnostics();
        const auto after_async = model->expert_async_diagnostics();
        const auto after_scheduler = model->expert_scheduler_diagnostics();
        const auto after_full = provider->hot_cache_diagnostics();
        const auto routing_stats = llama_cache_aware_routing_get_stats(context.get());
        const uint64_t swap_kib = vm_swap_kib();
        if (routing_stats.failures != 0 || after_storage.cancelled_reads != 0 ||
            after_storage.short_reads != 0 || after_storage.io_errors != 0 ||
            after_async.buffered_fallback_operations != 0 ||
            after_async.synchronous_fallback_operations != 0 ||
            after_async.read_requests_cancelled != 0 ||
            after_async.registered_buffer_count != 1 ||
            after_async.registered_buffer_bytes != initial_async.registered_buffer_bytes ||
            after_async.registered_buffer_bytes > after_async.staging_ceiling_bytes ||
            after_async.registered_buffer_bytes >= frozen_memlock_bytes ||
            after_async.file_registration_error != 0 || after_async.buffer_registration_error != 0 ||
            after_async.direct_staging_error != 0 || after_async.io_uring_setup_error != 0 ||
            after_async.io_uring_probe_error != 0 || after_async.io_uring_runtime_error != 0 ||
            after_async.fallback_reason_mask != 0 ||
            after_scheduler.active_requests != 0 || after_scheduler.queued_requests != 0 ||
            after_scheduler.terminal_failed != 0 || after_scheduler.terminal_cancelled != 0 ||
            after_scheduler.stale_completions != 0 ||
            after_full.system_memory_pressure_rejections != 0 ||
            after_full.system_memory_pressure_circuit_open ||
            after_full.cold_current_hot_refs != 0 ||
            after_full.cold_current_transfer_refs != 0 ||
            after_full.cold_current_request_refs != 0 ||
            after_full.cold_current_cpu_execution_refs != 0 ||
            after_full.cold_current_batch_refs != 0 || after_full.current_pins != 0 ||
            swap_kib != 0) {
            throw std::runtime_error("production-path resource/safety invariant failed");
        }
        if (!changed_routing && (routing_stats.ubatches != 0 || routing_stats.layers != 0 ||
                routing_stats.decisions != 0 || routing_stats.changed_decisions != 0 ||
                routing_stats.swaps != 0 || routing_stats.cumulative_score_regret != 0.0)) {
            throw std::runtime_error("EXACT arm observed cache-aware routing activity");
        }
        if (changed_routing && (routing_stats.ubatches != decode_forward_tokens ||
                routing_stats.layers != uint64_t(decode_forward_tokens)*routed_layers ||
                routing_stats.decisions != uint64_t(decode_forward_tokens)*routed_layers)) {
            throw std::runtime_error("S2 routing coverage does not match generated decode forwards");
        }

        struct rusage usage {};
        if (getrusage(RUSAGE_SELF, &usage) != 0) throw std::runtime_error("getrusage failed");
        const double sample_s = std::accumulate(sample_latency_s.begin(), sample_latency_s.end(), 0.0);
        const double forward_s = std::accumulate(forward_latency_s.begin(), forward_latency_s.end(), 0.0);
        const double decode_inference_s = sample_s + forward_s;
        const double generation_wall_s = seconds(generation_wall_completed - generation_wall_started);
        json command = json::array();
        for (int index = 0; index < argc; ++index) command.push_back(argv[index]);
        const json result = {
            {"schema_version", "issue100-gpqa-probe-result-v2"},
            {"status", "pass"}, {"exit_status", 0}, {"command", command},
            {"process_entry", process_resource_json(entry_resources)},
            {"item", {
                {"id", input.item_id}, {"scored", input.scored},
                {"prompt_sha256", input.prompt_sha256},
                {"prompt_tokens", input.tokens.size()},
            }},
            {"arm", args.arm}, {"seed", args.seed},
            {"model_path", args.model}, {"input_path", args.input},
            {"execution", {
                {"backend", "CPU"}, {"gpu_device_count", gpu_devices},
                {"n_gpu_layers", 0}, {"cuda_dependency", "none"},
                {"load_mode", "DIRECT_IO"}, {"runtime_mode", "PERFORMANCE"},
                {"n_ctx", args.n_ctx}, {"n_batch", 1}, {"n_ubatch", 1},
                {"threads", args.threads}, {"issue_mode", args.issue_mode},
                {"native_io_uring", initial_async.io_uring_enabled},
                {"staging_ceiling_bytes", initial_async.staging_ceiling_bytes},
                {"direct_staging_lane_count", initial_async.direct_staging_lane_count},
                {"registered_file_count", initial_async.registered_file_count},
                {"registered_buffer_count", initial_async.registered_buffer_count},
                {"registered_buffer_bytes", initial_async.registered_buffer_bytes},
                {"file_registration_error", initial_async.file_registration_error},
                {"buffer_registration_error", initial_async.buffer_registration_error},
                {"direct_staging_error", initial_async.direct_staging_error},
                {"io_uring_setup_error", initial_async.io_uring_setup_error},
                {"io_uring_probe_error", initial_async.io_uring_probe_error},
                {"io_uring_runtime_error", initial_async.io_uring_runtime_error},
                {"async_fallback_reason_mask", initial_async.fallback_reason_mask},
                {"buffered_fallback_operations", initial_async.buffered_fallback_operations},
                {"synchronous_fallback_operations", initial_async.synchronous_fallback_operations},
                {"capacity_request_mode", "AUTO"},
                {"capacity_request_bytes", auto_cache_request_bytes},
                {"auto_resolved_slots", resolved_cache_slots},
                {"auto_resolved_bytes", resolved_cache_bytes},
            }},
            {"protocol", {
                {"prefill_routing", "EXACT"},
                {"s2_activation", "after-complete-prefill-before-first-generated-token-decode"},
                {"top_p", frozen_top_p}, {"top_p_min_keep", 1},
                {"temperature", frozen_temperature},
                {"sampler_seed", llama_sampler_get_seed(sampler.get())},
                {"max_generated", args.max_generated},
                {"stop", stopped_eog ? "EOG" : "TOKEN_CAP"},
            }},
            {"routing", {
                {"enabled", changed_routing},
                {"candidate_count", changed_routing ? candidate_count : 0},
                {"max_swaps", changed_routing ? frozen_max_swaps : 0},
                {"max_score_regret", changed_routing ? frozen_max_regret : 0.0f},
                {"tier_source", changed_routing ? "real-provider-cold-cache" : "disabled"},
                {"stats", routing_stats_json(routing_stats)},
            }},
            {"preflight", {
                {"pass", true}, {"process_start_occupancy", initial_cold.occupancy},
                {"cpu_cold_only", initial_full.cpu_cold_only},
                {"first_miss_backing_read", first_miss_backing_read},
                {"capacity_floor_slots", capacity_floor_slots},
                {"capacity_floor_bytes", uint64_t(capacity_floor_slots)*expert_bundle_bytes},
                {"initial_cold", cold_json(initial_cold)},
                {"initial_storage", storage_json(initial_storage)},
                {"system_memory", system_memory_json(initial_full)},
            }},
            {"prefill", {
                {"tokens", input.tokens.size()}, {"tokens_to_full", tokens_to_full},
                {"wall_s", seconds(prefill_completed - prefill_started)},
                {"cold", cold_delta_json(initial_cold, before_cold)},
                {"storage", storage_delta_json(initial_storage, before_storage)},
                {"async", async_delta_json(initial_async, before_async)},
            }},
            {"generation", {
                {"token_ids", generated_ids}, {"piece_hex", piece_hex},
                {"generated_tokens_including_eog", generated_ids.size()},
                {"decode_forward_tokens", decode_forward_tokens},
                {"stopped_eog", stopped_eog}, {"truncated", truncated},
                {"sample_inference_s", sample_s}, {"forward_inference_s", forward_s},
                {"decode_inference_s", decode_inference_s},
                {"generation_wall_s", generation_wall_s},
                {"decode_tok_s", decode_inference_s > 0.0 ? generated_ids.size()/decode_inference_s : 0.0},
                {"sample_latency_s", sample_latency_s},
                {"forward_latency_s", forward_latency_s},
            }},
            {"cache", {
                {"capacity_slots", resolved_cache_slots},
                {"capacity_bytes", resolved_cache_bytes},
                {"total", cold_delta_json(initial_cold, after_cold)},
                {"decode", cold_delta_json(before_cold, after_cold)},
                {"final", cold_json(after_cold)},
            }},
            {"io", {
                {"total", storage_delta_json(initial_storage, after_storage)},
                {"decode", storage_delta_json(before_storage, after_storage)},
                {"async_total", async_delta_json(initial_async, after_async)},
            }},
            {"safety", {
                {"status", "pass"}, {"vm_swap_kib", swap_kib},
                {"transport", transport_json(after_async)},
                {"system_memory", system_memory_json(after_full)},
                {"scheduler", scheduler_json(after_scheduler)},
                {"terminal_references", terminal_reference_json(after_full)},
            }},
            {"timing", {
                {"model_load_s", seconds(model_loaded - model_load_started)},
                {"context_load_s", seconds(context_loaded - context_load_started)},
                {"prefill_wall_s", seconds(prefill_completed - prefill_started)},
                {"decode_inference_s", decode_inference_s},
                {"generation_wall_s", generation_wall_s},
                {"process_wall_s", seconds(steady_clock::now() - process_started)},
                {"generation_user_cpu_s", seconds(measured_usage_after.ru_utime) -
                    seconds(measured_usage_before.ru_utime)},
                {"generation_system_cpu_s", seconds(measured_usage_after.ru_stime) -
                    seconds(measured_usage_before.ru_stime)},
                {"peak_rss_kib", usage.ru_maxrss},
            }},
        };
        write_json(args.output, result);
        progress.write({
            {"record_type", "terminal"}, {"status", "pass"},
            {"generated_tokens", generated_ids.size()},
            {"stop", stopped_eog ? "EOG" : "TOKEN_CAP"},
        });
        std::printf(
            "ISSUE100_PROBE item=%s arm=%s status=pass auto_slots=%llu generated=%zu stop=%s decode_tok_s=%.6f\n",
            input.item_id.c_str(), args.arm.c_str(),
            static_cast<unsigned long long>(resolved_cache_slots), generated_ids.size(),
            stopped_eog ? "EOG" : "TOKEN_CAP",
            decode_inference_s > 0.0 ? generated_ids.size()/decode_inference_s : 0.0);
        std::fflush(stdout);
        return 0;
    } catch (const std::exception & error) {
        std::fprintf(stderr, "issue100 GPQA probe: %s\n", error.what());
        return 1;
    }
}

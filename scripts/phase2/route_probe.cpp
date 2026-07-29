#include "ggml-backend.h"
#include "llama.h"
#include "route_trace.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <memory>
#include <cstdio>
#include <string>
#include <vector>

namespace {

using clock_type = std::chrono::steady_clock;

constexpr int kContext = 512;
constexpr int kGenerate = 32;
constexpr int kThreads = 8;
constexpr const char * kPrompt = "According to all known laws";

struct arguments {
    std::string model;
    std::string model_name;
    uint64_t model_size = 0;
    std::string model_sha256;
    std::string model_source_revision;
    std::string published_gguf_revision;
    std::string llama_cpp_revision;
    std::string run_id;
    std::string trace;
    std::string logits;
    std::string prompt_file;
    int gpu_layers = 0;
    int max_generate = kGenerate;
    int fail_after_observations = -1;
    size_t max_ubatch_payload = 0;
    bool trace_enabled = false;
    bool direct_readback = false;
    bool invalid_mixed_phase = false;
    bool missing_annotation = false;
    bool performance_sample = false;
    bool skip_logits_write = false;
};

bool parse_int(const char * text, int & value) {
    char * end = nullptr;
    errno = 0;
    const long parsed = std::strtol(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed < -1 || parsed > 100000000) {
        return false;
    }
    value = (int) parsed;
    return true;
}

bool parse_size(const char * text, size_t & value) {
    char * end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed == 0 || parsed > SIZE_MAX) {
        return false;
    }
    value = (size_t) parsed;
    return true;
}

bool parse_u64(const char * text, uint64_t & value) {
    char * end = nullptr;
    errno = 0;
    const unsigned long long parsed = std::strtoull(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed == 0) {
        return false;
    }
    value = (uint64_t) parsed;
    return true;
}

bool parse_arguments(int argc, char ** argv, arguments & result) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--model") == 0 && i + 1 < argc) {
            result.model = argv[++i];
        } else if (std::strcmp(argv[i], "--model-name") == 0 && i + 1 < argc) {
            result.model_name = argv[++i];
        } else if (std::strcmp(argv[i], "--model-size") == 0 && i + 1 < argc) {
            if (!parse_u64(argv[++i], result.model_size)) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--model-sha256") == 0 && i + 1 < argc) {
            result.model_sha256 = argv[++i];
        } else if (std::strcmp(argv[i], "--model-source-revision") == 0 && i + 1 < argc) {
            result.model_source_revision = argv[++i];
        } else if (std::strcmp(argv[i], "--published-gguf-revision") == 0 && i + 1 < argc) {
            result.published_gguf_revision = argv[++i];
        } else if (std::strcmp(argv[i], "--llama-cpp-revision") == 0 && i + 1 < argc) {
            result.llama_cpp_revision = argv[++i];
        } else if (std::strcmp(argv[i], "--run-id") == 0 && i + 1 < argc) {
            result.run_id = argv[++i];
        } else if (std::strcmp(argv[i], "--trace") == 0 && i + 1 < argc) {
            result.trace = argv[++i];
            result.trace_enabled = true;
        } else if (std::strcmp(argv[i], "--logits") == 0 && i + 1 < argc) {
            result.logits = argv[++i];
        } else if (std::strcmp(argv[i], "--prompt-file") == 0 && i + 1 < argc) {
            result.prompt_file = argv[++i];
        } else if (std::strcmp(argv[i], "--max-generate") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.max_generate) ||
                result.max_generate < 1 || result.max_generate > 128) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--gpu-layers") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.gpu_layers)) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--max-ubatch-payload") == 0 && i + 1 < argc) {
            if (!parse_size(argv[++i], result.max_ubatch_payload)) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--fail-after-observations") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.fail_after_observations)) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--direct-readback") == 0) {
            result.direct_readback = true;
        } else if (std::strcmp(argv[i], "--invalid-mixed-phase") == 0) {
            result.invalid_mixed_phase = true;
        } else if (std::strcmp(argv[i], "--missing-annotation") == 0) {
            result.missing_annotation = true;
        } else if (std::strcmp(argv[i], "--performance-sample") == 0) {
            result.performance_sample = true;
            result.skip_logits_write = true;
        } else if (std::strcmp(argv[i], "--skip-logits-write") == 0) {
            result.skip_logits_write = true;
        } else {
            return false;
        }
    }

    if (result.model.empty() || result.logits.empty()) {
        return false;
    }
    if (result.performance_sample && !result.trace_enabled) {
        return false;
    }
    if (result.trace_enabled) {
        return !result.model_name.empty() && result.model_size > 0 && !result.model_sha256.empty() &&
            !result.model_source_revision.empty() &&
            !result.published_gguf_revision.empty() &&
            !result.llama_cpp_revision.empty() && !result.run_id.empty() && result.max_ubatch_payload > 0;
    }
    return true;
}

void print_ids(const std::vector<llama_token> & ids) {
    for (size_t i = 0; i < ids.size(); ++i) {
        if (i != 0) {
            std::cout << ',';
        }
        std::cout << ids[i];
    }
}

int finite_argmax(const float * logits, int n_vocab) {
    int best = 0;
    for (int token = 0; token < n_vocab; ++token) {
        if (!std::isfinite(logits[token])) {
            return -1;
        }
        if (logits[token] > logits[best]) {
            best = token;
        }
    }
    return best;
}

double elapsed(clock_type::time_point begin, clock_type::time_point end) {
    return std::chrono::duration<double>(end - begin).count();
}

struct observer_state {
    route_trace_writer * writer;
    int fail_after_observations;
    int observations = 0;
    struct direct_state * direct = nullptr;
};

struct direct_layer {
    std::vector<int32_t> selected_experts;
    std::vector<float> weights;
};

struct direct_state {
    std::vector<direct_layer> layers = std::vector<direct_layer>(8);
    bool failed = false;
};

bool tensor_layer(const char * name, const char * format, int & layer) {
    int consumed = 0;
    return std::sscanf(name, format, &layer, &consumed) == 1 && consumed > 0 && name[consumed] == '\0';
}

bool direct_readback_callback(ggml_tensor * tensor, bool ask, void * user_data) {
    direct_state * state = (direct_state *) user_data;
    int layer = -1;
    const bool selected = tensor_layer(tensor->name, "ffn_moe_topk-%d%n", layer);
    const bool weights = tensor_layer(tensor->name, "ffn_moe_weights_norm-%d%n", layer);
    const bool needed = (selected || weights) && layer >= 0 && layer < (int) state->layers.size();
    if (ask) {
        return needed;
    }
    if (!needed) {
        state->failed = true;
        return false;
    }

    const size_t count = (size_t) ggml_nelements(tensor);
    if (selected) {
        if (tensor->type != GGML_TYPE_I32) {
            state->failed = true;
            return false;
        }
        state->layers[layer].selected_experts.resize(count);
        ggml_backend_tensor_get_2d(
            tensor,
            state->layers[layer].selected_experts.data(),
            0,
            (size_t) tensor->ne[0]*sizeof(int32_t),
            (size_t) tensor->ne[1],
            tensor->nb[1],
            (size_t) tensor->ne[0]*sizeof(int32_t));
    } else {
        if (tensor->type != GGML_TYPE_F32) {
            state->failed = true;
            return false;
        }
        state->layers[layer].weights.resize(count);
        ggml_backend_tensor_get_2d(
            tensor,
            state->layers[layer].weights.data(),
            0,
            (size_t) tensor->ne[0]*sizeof(float),
            (size_t) tensor->ne[1],
            tensor->nb[1],
            (size_t) tensor->ne[0]*sizeof(float));
    }
    return true;
}

bool observe_route(const llama_route_observation * observation, void * user_data) {
    observer_state * state = (observer_state *) user_data;
    if (state->fail_after_observations >= 0 && state->observations >= state->fail_after_observations) {
        state->writer->abort();
        return false;
    }

    if (observation->n_expert_used != 2 || observation->layer < 1 || observation->layer > 7) {
        state->writer->abort();
        return false;
    }
    for (uint32_t row = 0; row < observation->n_tokens; ++row) {
        const size_t offset = (size_t) row*observation->n_expert_used;
        const float sum = observation->weights[offset] + observation->weights[offset + 1];
        if (observation->selected_experts[offset] == observation->selected_experts[offset + 1] ||
            observation->weights[offset] < 0.0f || observation->weights[offset + 1] < 0.0f ||
            std::fabs(sum - 1.0f) > 1e-5f) {
            state->writer->abort();
            return false;
        }
    }
    if (state->direct != nullptr) {
        const direct_layer & direct = state->direct->layers[observation->layer];
        const size_t count = (size_t) observation->n_tokens*observation->n_expert_used;
        const bool ids_equal = direct.selected_experts.size() == count &&
            std::equal(direct.selected_experts.begin(), direct.selected_experts.end(), observation->selected_experts);
        const bool weights_equal = direct.weights.size() == count &&
            std::equal(direct.weights.begin(), direct.weights.end(), observation->weights);
        if (state->direct->failed || !ids_equal || !weights_equal) {
            std::cerr << "ROUTE_ERROR: direct readback mismatch at layer " << observation->layer
                      << " expected_count=" << count
                      << " ids_count=" << direct.selected_experts.size()
                      << " weights_count=" << direct.weights.size()
                      << " ids_equal=" << ids_equal
                      << " weights_equal=" << weights_equal
                      << " callback_failed=" << state->direct->failed;
            if (!direct.weights.empty() && count != 0) {
                std::cerr << " direct_weight0=" << direct.weights[0]
                          << " observed_weight0=" << observation->weights[0];
            }
            if (!ids_equal) {
                std::cerr << " direct_ids=";
                for (int32_t id : direct.selected_experts) {
                    std::cerr << id << ',';
                }
                std::cerr << " observed_ids=";
                for (size_t i = 0; i < count; ++i) {
                    std::cerr << observation->selected_experts[i] << ',';
                }
            }
            std::cerr << '\n';
            state->writer->abort();
            return false;
        }
    }
    state->observations++;
    return state->writer->write(*observation);
}

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::cerr << "usage: route-probe --model PATH --logits PATH --gpu-layers N [--prompt-file PATH] [--max-generate N] [--trace PATH --model-name NAME --model-size BYTES --model-sha256 HEX --model-source-revision SHA --published-gguf-revision SHA --llama-cpp-revision SHA --run-id ID --max-ubatch-payload BYTES] [--direct-readback] [--performance-sample] [--skip-logits-write] [--fail-after-observations N] [--invalid-mixed-phase] [--missing-annotation]\n";
        return 2;
    }

    std::string prompt_text = kPrompt;
    if (!args.prompt_file.empty()) {
        std::ifstream prompt_file(args.prompt_file, std::ios::binary);
        if (!prompt_file) {
            std::cerr << "ROUTE_ERROR: prompt input failed\n";
            return 2;
        }
        prompt_text.assign(
            std::istreambuf_iterator<char>(prompt_file),
            std::istreambuf_iterator<char>());
        if (prompt_text.empty()) {
            std::cerr << "ROUTE_ERROR: prompt input is empty\n";
            return 2;
        }
    }

    ggml_backend_load_all();
    llama_model_params model_params = llama_model_default_params();
    model_params.n_gpu_layers = args.gpu_layers;
    llama_model * model = llama_model_load_from_file(args.model.c_str(), model_params);
    if (model == nullptr) {
        std::cerr << "ROUTE_ERROR: model load failed\n";
        return 3;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model);
    const int n_vocab = llama_vocab_n_tokens(vocab);
    const int n_prompt = -llama_tokenize(
        vocab, prompt_text.data(), prompt_text.size(), nullptr, 0, true, true);
    if (n_prompt <= 0 || n_prompt + args.max_generate - 1 > kContext) {
        std::cerr << "ROUTE_ERROR: prompt tokenization failed\n";
        llama_model_free(model);
        return 4;
    }
    std::vector<llama_token> prompt(n_prompt);
    if (llama_tokenize(
            vocab,
            prompt_text.data(),
            prompt_text.size(),
            prompt.data(),
            prompt.size(),
            true,
            true) != n_prompt) {
        std::cerr << "ROUTE_ERROR: prompt tokenization failed\n";
        llama_model_free(model);
        return 4;
    }

    llama_context_params context_params = llama_context_default_params();
    context_params.n_ctx = kContext;
    context_params.n_batch = kContext;
    context_params.n_ubatch = kContext;
    context_params.no_perf = false;
    direct_state direct;
    if (args.direct_readback) {
        context_params.cb_eval = direct_readback_callback;
        context_params.cb_eval_user_data = &direct;
    }
    llama_context * context = llama_init_from_model(model, context_params);
    if (context == nullptr) {
        std::cerr << "ROUTE_ERROR: context creation failed\n";
        llama_model_free(model);
        return 5;
    }
    llama_set_n_threads(context, kThreads, kThreads);

    std::unique_ptr<route_trace_writer> writer;
    observer_state observer = {};
    if (args.trace_enabled) {
        const route_trace_metadata metadata = {
            /*.model_name            =*/ args.model_name,
            /*.model_size            =*/ args.model_size,
            /*.model_sha256          =*/ args.model_sha256,
            /*.model_source_revision =*/ args.model_source_revision,
            /*.published_gguf_revision =*/ args.published_gguf_revision,
            /*.llama_cpp_revision    =*/ args.llama_cpp_revision,
            /*.run_id                =*/ args.run_id,
            /*.expert_count          =*/ 8,
            /*.top_k                 =*/ 2,
            /*.routed_layer_count    =*/ 7,
            /*.max_ubatch_payload    =*/ args.max_ubatch_payload,
        };
        writer.reset(new route_trace_writer(args.trace, metadata));
        observer.writer = writer.get();
        observer.fail_after_observations = args.fail_after_observations;
        observer.direct = args.direct_readback ? &direct : nullptr;
        if (!writer->good() || llama_set_route_observer(context, observe_route, &observer) != 0) {
            std::cerr << "ROUTE_ERROR: observer initialization failed\n";
            llama_free(context);
            llama_model_free(model);
            return 6;
        }
    }

    std::ofstream logits_file(args.logits, std::ios::binary | std::ios::trunc);
    if (!logits_file) {
        std::cerr << "ROUTE_ERROR: logits output failed\n";
        llama_free(context);
        llama_model_free(model);
        return 7;
    }

    std::vector<llama_token> generated;
    llama_batch batch = llama_batch_get_one(prompt.data(), prompt.size());
    const auto prompt_begin = clock_type::now();
    clock_type::time_point prompt_end;
    clock_type::time_point decode_begin;
    bool stopped_on_eog = false;
    for (int step = 0; step < args.max_generate; ++step) {
        if (args.trace_enabled) {
            const llama_route_phase phase = args.invalid_mixed_phase && step == 0
                ? LLAMA_ROUTE_PHASE_MIXED
                : (step == 0 ? LLAMA_ROUTE_PHASE_PREFILL : LLAMA_ROUTE_PHASE_DECODE);
            const int32_t annotation_status = args.missing_annotation && step == 0
                ? LLAMA_ROUTE_OBSERVER_STATUS_OK
                : llama_route_observer_begin(context, 0, phase);
            if (annotation_status != LLAMA_ROUTE_OBSERVER_STATUS_OK) {
                std::cerr << "ROUTE_ERROR: observer annotation failed with status " << annotation_status << '\n';
                writer->abort();
                llama_free(context);
                llama_model_free(model);
                return 8;
            }
        }
        const int status = llama_decode(context, batch);
        if (status != 0) {
            std::cerr << "ROUTE_ERROR: decode failed with status " << status << '\n';
            if (writer) {
                writer->abort();
            }
            if (status == -4 && args.fail_after_observations >= 0) {
                const int32_t retry = llama_route_observer_begin(context, 0, LLAMA_ROUTE_PHASE_DECODE);
                if (retry != LLAMA_ROUTE_OBSERVER_ERROR_STATE) {
                    std::cerr << "ROUTE_ERROR: observer failure was not latched\n";
                    llama_free(context);
                    llama_model_free(model);
                    return 15;
                }
                std::cerr << "LATCHED_FAILURE_REJECTED\tstatus=" << retry << '\n';
            }
            llama_free(context);
            llama_model_free(model);
            return status == -4 ? 14 : 9;
        }
        float * logits = llama_get_logits_ith(context, -1);
        if (logits == nullptr) {
            std::cerr << "ROUTE_ERROR: logits unavailable\n";
            llama_free(context);
            llama_model_free(model);
            return 10;
        }
        if (!args.skip_logits_write) {
            logits_file.write((const char *) logits, (size_t) n_vocab*sizeof(float));
        }
        const int next = finite_argmax(logits, n_vocab);
        if (!logits_file || next < 0) {
            std::cerr << "ROUTE_ERROR: invalid logits\n";
            llama_free(context);
            llama_model_free(model);
            return 11;
        }
        if (step == 0) {
            prompt_end = clock_type::now();
        }
        generated.push_back(next);
        if (step == 0) {
            decode_begin = clock_type::now();
        }
        if (llama_vocab_is_eog(vocab, next)) {
            stopped_on_eog = true;
            break;
        }
        batch = llama_batch_get_one(&generated.back(), 1);
    }
    const auto decode_end = clock_type::now();

    const auto finalize_begin = clock_type::now();
    if (writer && !writer->finalize()) {
        std::cerr << "ROUTE_ERROR: trace finalization failed\n";
        llama_free(context);
        llama_model_free(model);
        return 12;
    }
    const auto finalize_end = clock_type::now();

    if (args.performance_sample) {
        const int decode_tokens = (int) generated.size() - 1;
        const double ttft = elapsed(prompt_begin, prompt_end);
        const double decode_seconds = elapsed(decode_begin, decode_end);
        if (decode_tokens <= 0 || ttft <= 0.0 || decode_seconds <= 0.0) {
            std::cerr << "ROUTE_ERROR: timing sample invalid\n";
            llama_free(context);
            llama_model_free(model);
            return 13;
        }
        std::cout << std::setprecision(17)
                  << "TRACE_PERF"
                  << "\tprompt_tokens=" << prompt.size()
                  << "\tgenerated_tokens=" << generated.size()
                  << "\tttft_seconds=" << ttft
                  << "\tprompt_tokens_per_second=" << prompt.size()/ttft
                  << "\tdecode_tokens_per_second=" << decode_tokens/decode_seconds
                  << "\tfinalize_seconds=" << elapsed(finalize_begin, finalize_end)
                  << '\n';
    }

    std::cout << "PROMPT_IDS\t";
    print_ids(prompt);
    std::cout << "\nGENERATED_IDS\t";
    print_ids(generated);
    std::cout << "\nSTOP_REASON\t" << (stopped_on_eog ? "eog" : "cap") << '\n';
    if (writer) {
        const llama_route_observer_stats stats = llama_route_observer_get_stats(context);
        const llama_perf_context_data perf = llama_perf_context(context);
        std::cout << "ROUTE_STATS\tubatches=" << stats.ubatches
                  << "\tlayers=" << stats.layers
                  << "\tcopy_bytes=" << stats.copy_bytes
                  << "\tsynchronizations=" << stats.explicit_synchronizations
                  << "\tfailures=" << stats.failures
                  << "\trecords=" << writer->record_count()
                  << "\ttrace_bytes=" << writer->bytes_written()
                  << "\tflushes=" << writer->flush_count()
                  << "\tgraphs_reused=" << perf.n_reused << '\n';
    } else {
        const llama_route_observer_stats stats = llama_route_observer_get_stats(context);
        const llama_perf_context_data perf = llama_perf_context(context);
        std::cout << "DISABLED_ROUTE_STATS\tubatches=" << stats.ubatches
                  << "\tlayers=" << stats.layers
                  << "\tcopy_bytes=" << stats.copy_bytes
                  << "\tsynchronizations=" << stats.explicit_synchronizations
                  << "\tfailures=" << stats.failures
                  << "\tgraphs_reused=" << perf.n_reused << '\n';
    }
    std::cout << "RESULT\texit=0\n";

    llama_free(context);
    llama_model_free(model);
    return 0;
}

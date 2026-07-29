#include "ggml-backend.h"
#include "llama.h"

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <clocale>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <string>
#include <sys/resource.h>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

constexpr int kContext = 512;
constexpr int kGenerate = 128;
constexpr int kSeed = 1;
constexpr int kThreads = 8;
constexpr int kMeasuredRuns = 5;
constexpr const char * kPrompt = "According to all known laws";

struct Arguments {
    std::string model;
    int gpu_layers = 0;
};

struct DeviceMemory {
    ggml_backend_dev_t device = nullptr;
    long long used_bytes = -1;
};

double seconds_between(Clock::time_point start, Clock::time_point end) {
    return std::chrono::duration<double>(end - start).count();
}

long peak_rss_kib() {
    rusage usage{};
    if (getrusage(RUSAGE_SELF, &usage) != 0) {
        return -1;
    }
    return usage.ru_maxrss;
}

long long device_used_bytes(ggml_backend_dev_t device) {
    if (device == nullptr) {
        return -1;
    }
    size_t free_memory = 0;
    size_t total_memory = 0;
    ggml_backend_dev_memory(device, &free_memory, &total_memory);
    if (free_memory > total_memory) {
        return -1;
    }
    return static_cast<long long>(total_memory - free_memory);
}

std::string clean_field(const char * text) {
    std::string result = text == nullptr ? "" : text;
    std::replace(result.begin(), result.end(), '\t', ' ');
    std::replace(result.begin(), result.end(), '\n', ' ');
    return result;
}

bool parse_int(const char * text, int & value) {
    char * end = nullptr;
    errno = 0;
    const long parsed = std::strtol(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed < 0 || parsed > 1000000) {
        return false;
    }
    value = static_cast<int>(parsed);
    return true;
}

bool parse_arguments(int argc, char ** argv, Arguments & arguments) {
    for (int index = 1; index < argc; ++index) {
        if (std::strcmp(argv[index], "--model") == 0 && index + 1 < argc) {
            arguments.model = argv[++index];
        } else if (std::strcmp(argv[index], "--gpu-layers") == 0 && index + 1 < argc) {
            if (!parse_int(argv[++index], arguments.gpu_layers)) {
                return false;
            }
        } else {
            return false;
        }
    }
    return !arguments.model.empty();
}

int finite_argmax(const float * logits, int vocabulary_size) {
    int best = 0;
    for (int token = 0; token < vocabulary_size; ++token) {
        if (!std::isfinite(logits[token])) {
            return -1;
        }
        if (logits[token] > logits[best]) {
            best = token;
        }
    }
    return best;
}

void print_ids(const std::vector<llama_token> & ids) {
    for (size_t index = 0; index < ids.size(); ++index) {
        if (index) {
            std::cout << ',';
        }
        std::cout << ids[index];
    }
}

bool run_inference(
    llama_model * model,
    const llama_vocab * vocabulary,
    const std::vector<llama_token> & prompt_tokens,
    const char * kind,
    int run_index,
    ggml_backend_dev_t gpu_device) {
    llama_context_params context_parameters = llama_context_default_params();
    context_parameters.n_ctx = kContext;
    context_parameters.n_batch = kContext;
    context_parameters.n_ubatch = kContext;
    context_parameters.no_perf = false;
    llama_context * context = llama_init_from_model(model, context_parameters);
    if (context == nullptr) {
        std::cerr << "BENCH_ERROR: could not create context\n";
        return false;
    }
    llama_set_n_threads(context, kThreads, kThreads);

    const int vocabulary_size = llama_vocab_n_tokens(vocabulary);
    std::vector<llama_token> generated;
    generated.reserve(kGenerate);
    std::vector<double> decode_latencies;
    decode_latencies.reserve(kGenerate - 1);
    long long gpu_peak = device_used_bytes(gpu_device);

    llama_batch batch = llama_batch_get_one(
        const_cast<llama_token *>(prompt_tokens.data()), prompt_tokens.size());
    const auto prompt_start = Clock::now();
    if (llama_decode(context, batch) != 0) {
        std::cerr << "BENCH_ERROR: prompt decode failed\n";
        llama_free(context);
        return false;
    }
    float * logits = llama_get_logits_ith(context, -1);
    const int first = logits == nullptr ? -1 : finite_argmax(logits, vocabulary_size);
    const auto prompt_end = Clock::now();
    if (first < 0) {
        std::cerr << "BENCH_ERROR: invalid or non-finite first token\n";
        llama_free(context);
        return false;
    }
    generated.push_back(first);
    gpu_peak = std::max(gpu_peak, device_used_bytes(gpu_device));
    bool terminal_eog = llama_vocab_is_eog(vocabulary, first);

    for (int index = 1; index < kGenerate && !terminal_eog; ++index) {
        batch = llama_batch_get_one(&generated.back(), 1);
        const auto token_start = Clock::now();
        if (llama_decode(context, batch) != 0) {
            std::cerr << "BENCH_ERROR: token decode failed at index " << index << '\n';
            llama_free(context);
            return false;
        }
        logits = llama_get_logits_ith(context, -1);
        const int next = logits == nullptr ? -1 : finite_argmax(logits, vocabulary_size);
        const auto token_end = Clock::now();
        if (next < 0) {
            std::cerr << "BENCH_ERROR: invalid or non-finite token at index " << index << '\n';
            llama_free(context);
            return false;
        }
        decode_latencies.push_back(seconds_between(token_start, token_end));
        generated.push_back(next);
        gpu_peak = std::max(gpu_peak, device_used_bytes(gpu_device));
        terminal_eog = llama_vocab_is_eog(vocabulary, next);
    }

    const double ttft = seconds_between(prompt_start, prompt_end);
    const double decode_seconds = std::accumulate(
        decode_latencies.begin(), decode_latencies.end(), 0.0);
    const double prompt_throughput = prompt_tokens.size() / ttft;
    const double decode_throughput = decode_latencies.size() / decode_seconds;

    std::cout << std::setprecision(17);
    std::cout << "RUN\t" << kind << '\t' << run_index
              << "\tprompt_tokens=" << prompt_tokens.size()
              << "\tgenerated_tokens=" << generated.size()
              << "\tterminal_eog=" << (terminal_eog ? 1 : 0)
              << "\tttft_seconds=" << ttft
              << "\tprompt_tokens_per_second=" << prompt_throughput
              << "\tdecode_tokens_per_second=" << decode_throughput
              << "\tpeak_rss_kib=" << peak_rss_kib()
              << "\tgpu_used_peak_bytes=" << gpu_peak << '\n';
    std::cout << "LATENCIES\t" << kind << '\t' << run_index << '\t';
    for (size_t index = 0; index < decode_latencies.size(); ++index) {
        if (index) {
            std::cout << ',';
        }
        std::cout << decode_latencies[index];
    }
    std::cout << '\n';
    std::cout << "IDS\t" << kind << '\t' << run_index << '\t';
    print_ids(generated);
    std::cout << '\n';

    llama_perf_context_print(context);
    llama_free(context);
    return true;
}

}  // namespace

int main(int argc, char ** argv) {
    std::setlocale(LC_NUMERIC, "C");
    Arguments arguments;
    if (!parse_arguments(argc, argv, arguments)) {
        std::cerr << "usage: " << argv[0] << " --model PATH --gpu-layers N\n";
        return 2;
    }

    ggml_backend_load_all();
    ggml_backend_dev_t gpu_device = nullptr;
    std::cout << "CONFIG\tprompt=" << kPrompt << "\tseed=" << kSeed
              << "\ttemperature=0\tcontext=" << kContext
              << "\tgenerate=" << kGenerate << "\tthreads=" << kThreads
              << "\tgpu_layers=" << arguments.gpu_layers << '\n';
    for (size_t index = 0; index < ggml_backend_dev_count(); ++index) {
        ggml_backend_dev_t device = ggml_backend_dev_get(index);
        size_t free_memory = 0;
        size_t total_memory = 0;
        ggml_backend_dev_memory(device, &free_memory, &total_memory);
        if (gpu_device == nullptr
            && ggml_backend_dev_type(device) == GGML_BACKEND_DEVICE_TYPE_GPU) {
            gpu_device = device;
        }
        std::cout << "DEVICE\t" << index << '\t' << clean_field(ggml_backend_dev_name(device))
                  << '\t' << clean_field(ggml_backend_dev_description(device))
                  << '\t' << static_cast<int>(ggml_backend_dev_type(device))
                  << '\t' << free_memory << '\t' << total_memory << '\n';
    }

    const long long gpu_baseline = device_used_bytes(gpu_device);
    llama_model_params model_parameters = llama_model_default_params();
    model_parameters.n_gpu_layers = arguments.gpu_layers;
    const auto load_start = Clock::now();
    llama_model * model = llama_model_load_from_file(arguments.model.c_str(), model_parameters);
    const auto load_end = Clock::now();
    if (model == nullptr) {
        std::cerr << "BENCH_ERROR: could not load model\n";
        return 3;
    }
    std::cout << std::setprecision(17);
    std::cout << "LOAD\tseconds=" << seconds_between(load_start, load_end)
              << "\tpeak_rss_kib=" << peak_rss_kib()
              << "\tgpu_baseline_used_bytes=" << gpu_baseline
              << "\tgpu_used_after_load_bytes=" << device_used_bytes(gpu_device) << '\n';

    const llama_vocab * vocabulary = llama_model_get_vocab(model);
    const int token_count = -llama_tokenize(
        vocabulary, kPrompt, std::strlen(kPrompt), nullptr, 0, true, true);
    if (token_count <= 0) {
        std::cerr << "BENCH_ERROR: could not size prompt tokenization\n";
        llama_model_free(model);
        return 4;
    }
    std::vector<llama_token> prompt_tokens(token_count);
    if (llama_tokenize(
            vocabulary,
            kPrompt,
            std::strlen(kPrompt),
            prompt_tokens.data(),
            prompt_tokens.size(),
            true,
            true) != token_count) {
        std::cerr << "BENCH_ERROR: could not tokenize prompt\n";
        llama_model_free(model);
        return 5;
    }
    std::cout << "PROMPT_IDS\t";
    print_ids(prompt_tokens);
    std::cout << '\n';

    if (!run_inference(model, vocabulary, prompt_tokens, "warmup", 0, gpu_device)) {
        llama_model_free(model);
        return 6;
    }
    for (int run = 0; run < kMeasuredRuns; ++run) {
        if (!run_inference(model, vocabulary, prompt_tokens, "measured", run, gpu_device)) {
            llama_model_free(model);
            return 7;
        }
    }

    std::cout << "RESULT\tload_calls=1\twarmups=1\tmeasured=" << kMeasuredRuns
              << "\texit=0\n";
    llama_model_free(model);
    llama_backend_free();
    return 0;
}

#include "ggml-backend.h"
#include "llama.h"

#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <string>
#include <vector>

namespace {

using clock_type = std::chrono::steady_clock;

constexpr int kContext = 512;
constexpr int kGenerate = 128;
constexpr int kThreads = 8;
constexpr const char * kPrompt = "According to all known laws";

struct arguments {
    std::string model;
    int gpu_layers = 0;
};

bool parse_int(const char * text, int & value) {
    char * end = nullptr;
    errno = 0;
    const long parsed = std::strtol(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed < 0 || parsed > 1000000) {
        return false;
    }
    value = (int) parsed;
    return true;
}

bool parse_arguments(int argc, char ** argv, arguments & result) {
    for (int i = 1; i < argc; ++i) {
        if (std::strcmp(argv[i], "--model") == 0 && i + 1 < argc) {
            result.model = argv[++i];
        } else if (std::strcmp(argv[i], "--gpu-layers") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.gpu_layers)) {
                return false;
            }
        } else {
            return false;
        }
    }
    return !result.model.empty();
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

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::cerr << "usage: overhead-probe --model PATH --gpu-layers N\n";
        return 2;
    }

    ggml_backend_load_all();
    llama_model_params model_params = llama_model_default_params();
    model_params.n_gpu_layers = args.gpu_layers;
    llama_model * model = llama_model_load_from_file(args.model.c_str(), model_params);
    if (model == nullptr) {
        std::cerr << "OVERHEAD_ERROR: model load failed\n";
        return 3;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model);
    const int n_vocab = llama_vocab_n_tokens(vocab);
    const int n_prompt = -llama_tokenize(vocab, kPrompt, std::strlen(kPrompt), nullptr, 0, true, true);
    if (n_prompt <= 0) {
        std::cerr << "OVERHEAD_ERROR: prompt tokenization failed\n";
        llama_model_free(model);
        return 4;
    }
    std::vector<llama_token> prompt((size_t) n_prompt);
    if (llama_tokenize(vocab, kPrompt, std::strlen(kPrompt), prompt.data(), prompt.size(), true, true) != n_prompt) {
        std::cerr << "OVERHEAD_ERROR: prompt tokenization failed\n";
        llama_model_free(model);
        return 4;
    }

    llama_context_params context_params = llama_context_default_params();
    context_params.n_ctx = kContext;
    context_params.n_batch = kContext;
    context_params.n_ubatch = kContext;
    context_params.no_perf = false;
    llama_context * context = llama_init_from_model(model, context_params);
    if (context == nullptr) {
        std::cerr << "OVERHEAD_ERROR: context creation failed\n";
        llama_model_free(model);
        return 5;
    }
    llama_set_n_threads(context, kThreads, kThreads);

    std::vector<llama_token> generated;
    generated.reserve(kGenerate);
    llama_batch batch = llama_batch_get_one(prompt.data(), prompt.size());
    const auto prompt_begin = clock_type::now();
    if (llama_decode(context, batch) != 0) {
        std::cerr << "OVERHEAD_ERROR: prompt decode failed\n";
        llama_free(context);
        llama_model_free(model);
        return 6;
    }
    float * logits = llama_get_logits_ith(context, -1);
    int next = logits == nullptr ? -1 : finite_argmax(logits, n_vocab);
    const auto prompt_end = clock_type::now();
    if (next < 0) {
        std::cerr << "OVERHEAD_ERROR: prompt logits invalid\n";
        llama_free(context);
        llama_model_free(model);
        return 7;
    }
    generated.push_back(next);

    const auto decode_begin = clock_type::now();
    while ((int) generated.size() < kGenerate && !llama_vocab_is_eog(vocab, generated.back())) {
        batch = llama_batch_get_one(&generated.back(), 1);
        if (llama_decode(context, batch) != 0) {
            std::cerr << "OVERHEAD_ERROR: token decode failed\n";
            llama_free(context);
            llama_model_free(model);
            return 8;
        }
        logits = llama_get_logits_ith(context, -1);
        next = logits == nullptr ? -1 : finite_argmax(logits, n_vocab);
        if (next < 0) {
            std::cerr << "OVERHEAD_ERROR: token logits invalid\n";
            llama_free(context);
            llama_model_free(model);
            return 9;
        }
        generated.push_back(next);
    }
    const auto decode_end = clock_type::now();

    const int decode_tokens = (int) generated.size() - 1;
    const double ttft = elapsed(prompt_begin, prompt_end);
    const double decode_seconds = elapsed(decode_begin, decode_end);
    if (decode_tokens <= 0 || ttft <= 0.0 || decode_seconds <= 0.0) {
        std::cerr << "OVERHEAD_ERROR: timing sample invalid\n";
        llama_free(context);
        llama_model_free(model);
        return 10;
    }

    std::cout << std::setprecision(17)
              << "METRIC"
              << "\tprompt_tokens=" << prompt.size()
              << "\tgenerated_tokens=" << generated.size()
              << "\tttft_seconds=" << ttft
              << "\tprompt_tokens_per_second=" << prompt.size()/ttft
              << "\tdecode_tokens_per_second=" << decode_tokens/decode_seconds
              << "\tgraphs_reused=" << llama_perf_context(context).n_reused
              << "\nRESULT\texit=0\n";

    llama_free(context);
    llama_model_free(model);
    llama_backend_free();
    return 0;
}

#include "ggml-backend.h"
#include "llama.h"

#include <algorithm>
#include <cerrno>
#include <clocale>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <string>
#include <vector>

namespace {

constexpr int kContext = 512;
constexpr int kGenerate = 32;
constexpr int kSeed = 1;
constexpr int kThreads = 8;
constexpr int kTop = 10;
constexpr const char * kPrompt = "According to all known laws";

struct Arguments {
    std::string model;
    std::string raw_logits;
    int gpu_layers = 0;
};

void usage(const char * executable) {
    std::cerr << "usage: " << executable
              << " --model PATH --raw-logits PATH --gpu-layers N\n";
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
        } else if (std::strcmp(argv[index], "--raw-logits") == 0 && index + 1 < argc) {
            arguments.raw_logits = argv[++index];
        } else if (std::strcmp(argv[index], "--gpu-layers") == 0 && index + 1 < argc) {
            if (!parse_int(argv[++index], arguments.gpu_layers)) {
                return false;
            }
        } else {
            return false;
        }
    }
    return !arguments.model.empty() && !arguments.raw_logits.empty();
}

std::string clean_field(const char * text) {
    std::string result = text == nullptr ? "" : text;
    std::replace(result.begin(), result.end(), '\t', ' ');
    std::replace(result.begin(), result.end(), '\n', ' ');
    return result;
}

void print_ids(const std::vector<llama_token> & ids) {
    for (size_t index = 0; index < ids.size(); ++index) {
        if (index) {
            std::cout << ',';
        }
        std::cout << ids[index];
    }
}

std::vector<int> top_ids(const float * logits, int vocabulary_size) {
    std::vector<int> ids(vocabulary_size);
    std::iota(ids.begin(), ids.end(), 0);
    std::partial_sort(
        ids.begin(),
        ids.begin() + kTop,
        ids.end(),
        [logits](int left, int right) {
            if (logits[left] == logits[right]) {
                return left < right;
            }
            return logits[left] > logits[right];
        });
    ids.resize(kTop);
    return ids;
}

}  // namespace

int main(int argc, char ** argv) {
    std::setlocale(LC_NUMERIC, "C");
    Arguments arguments;
    if (!parse_arguments(argc, argv, arguments)) {
        usage(argv[0]);
        return 2;
    }

    ggml_backend_load_all();
    std::cout << "CONFIG\tprompt=" << kPrompt << "\tseed=" << kSeed
              << "\ttemperature=0\tcontext=" << kContext
              << "\tgenerate=" << kGenerate << "\tthreads=" << kThreads
              << "\tgpu_layers=" << arguments.gpu_layers << '\n';
    for (size_t index = 0; index < ggml_backend_dev_count(); ++index) {
        ggml_backend_dev_t device = ggml_backend_dev_get(index);
        size_t free_memory = 0;
        size_t total_memory = 0;
        ggml_backend_dev_memory(device, &free_memory, &total_memory);
        std::cout << "DEVICE\t" << index << '\t' << clean_field(ggml_backend_dev_name(device))
                  << '\t' << clean_field(ggml_backend_dev_description(device))
                  << '\t' << static_cast<int>(ggml_backend_dev_type(device))
                  << '\t' << free_memory << '\t' << total_memory << '\n';
    }

    llama_model_params model_parameters = llama_model_default_params();
    model_parameters.n_gpu_layers = arguments.gpu_layers;
    llama_model * model = llama_model_load_from_file(arguments.model.c_str(), model_parameters);
    if (model == nullptr) {
        std::cerr << "PROBE_ERROR: could not load model\n";
        return 3;
    }

    const llama_vocab * vocabulary = llama_model_get_vocab(model);
    const int vocabulary_size = llama_vocab_n_tokens(vocabulary);
    std::cout << "MODEL\tvocabulary=" << vocabulary_size
              << "\tlayers=" << llama_model_n_layer(model)
              << "\tbytes=" << llama_model_size(model)
              << "\tparameters=" << llama_model_n_params(model) << '\n';

    const int token_count = -llama_tokenize(
        vocabulary, kPrompt, std::strlen(kPrompt), nullptr, 0, true, true);
    if (token_count <= 0) {
        std::cerr << "PROBE_ERROR: could not size prompt tokenization\n";
        llama_model_free(model);
        return 4;
    }
    std::vector<llama_token> prompt_tokens(token_count);
    if (llama_tokenize(
            vocabulary,
            kPrompt,
            std::strlen(kPrompt),
            prompt_tokens.data(),
            static_cast<int32_t>(prompt_tokens.size()),
            true,
            true) != token_count) {
        std::cerr << "PROBE_ERROR: could not tokenize prompt\n";
        llama_model_free(model);
        return 5;
    }
    std::cout << "PROMPT_IDS\t";
    print_ids(prompt_tokens);
    std::cout << '\n';

    llama_context_params context_parameters = llama_context_default_params();
    context_parameters.n_ctx = kContext;
    context_parameters.n_batch = kContext;
    context_parameters.n_ubatch = kContext;
    context_parameters.no_perf = false;
    llama_context * context = llama_init_from_model(model, context_parameters);
    if (context == nullptr) {
        std::cerr << "PROBE_ERROR: could not create context\n";
        llama_model_free(model);
        return 6;
    }
    llama_set_n_threads(context, kThreads, kThreads);

    std::ofstream raw(arguments.raw_logits, std::ios::binary | std::ios::trunc);
    if (!raw) {
        std::cerr << "PROBE_ERROR: could not open raw logits output\n";
        llama_free(context);
        llama_model_free(model);
        return 7;
    }

    std::vector<llama_token> generated;
    llama_batch batch = llama_batch_get_one(prompt_tokens.data(), prompt_tokens.size());
    for (int step = 0; step < kGenerate; ++step) {
        const int decode_status = llama_decode(context, batch);
        if (decode_status != 0) {
            std::cerr << "PROBE_ERROR: decode failed with status " << decode_status << '\n';
            llama_free(context);
            llama_model_free(model);
            return 8;
        }
        float * logits = llama_get_logits_ith(context, -1);
        if (logits == nullptr) {
            std::cerr << "PROBE_ERROR: logits unavailable\n";
            llama_free(context);
            llama_model_free(model);
            return 9;
        }
        raw.write(reinterpret_cast<const char *>(logits), vocabulary_size * sizeof(float));
        if (!raw) {
            std::cerr << "PROBE_ERROR: raw logits write failed\n";
            llama_free(context);
            llama_model_free(model);
            return 10;
        }

        const std::vector<int> selected = top_ids(logits, vocabulary_size);
        const llama_token next = selected.front();
        generated.push_back(next);
        const bool is_eog = llama_vocab_is_eog(vocabulary, next);
        std::cout << "STEP\t" << step << "\t" << next << "\t" << (is_eog ? 1 : 0) << "\t";
        for (size_t index = 0; index < selected.size(); ++index) {
            if (index) {
                std::cout << ',';
            }
            std::cout << selected[index] << ':' << std::setprecision(9) << logits[selected[index]];
        }
        std::cout << '\n';
        if (is_eog) {
            break;
        }
        batch = llama_batch_get_one(&generated.back(), 1);
    }
    raw.close();

    std::cout << "GENERATED_IDS\t";
    print_ids(generated);
    std::cout << '\n';
    std::cout << "RESULT\tsteps=" << generated.size() << "\texit=0\n";

    llama_perf_context_print(context);
    llama_free(context);
    llama_model_free(model);
    return 0;
}

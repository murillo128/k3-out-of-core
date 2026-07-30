#include "ggml-backend.h"
#include "llama.h"
#include "llama-context.h"
#include "llama-model.h"

#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <vector>

namespace {

constexpr const char * kPrompt = "According to all known laws";

struct context_deleter {
    void operator()(llama_context * context) const {
        llama_free(context);
    }
};

using context_ptr = std::unique_ptr<llama_context, context_deleter>;

context_ptr create_context(llama_model * model) {
    llama_context_params params = llama_context_default_params();
    params.n_ctx = 512;
    params.n_batch = 512;
    params.n_ubatch = 512;
    params.no_perf = true;
    return context_ptr(llama_init_from_model(model, params));
}

bool decode_prompt(llama_context * context, std::vector<llama_token> & prompt) {
    if (llama_decode(context, llama_batch_get_one(prompt.data(), prompt.size())) != 0) {
        return false;
    }
    llama_synchronize(context);
    return true;
}

} // namespace

int main(int argc, char ** argv) {
    if (argc != 2) {
        std::cerr << "usage: provider-admin-probe MODEL\n";
        return 2;
    }

    ggml_backend_load_all();
    llama_model_params model_params = llama_model_default_params();
    model_params.n_gpu_layers = 0;
    model_params.expert_weights_mode = LLAMA_EXPERT_WEIGHTS_MODE_RESIDENT;
    std::unique_ptr<llama_model, decltype(&llama_model_free)> model(
        llama_model_load_from_file(argv[1], model_params), llama_model_free);
    if (!model) {
        std::cerr << "ADMIN_ERROR: model load failed\n";
        return 3;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model.get());
    const int token_count = -llama_tokenize(vocab, kPrompt, std::strlen(kPrompt), nullptr, 0, true, true);
    if (token_count <= 0) {
        std::cerr << "ADMIN_ERROR: tokenization failed\n";
        return 4;
    }
    std::vector<llama_token> prompt((size_t) token_count);
    if (llama_tokenize(vocab, kPrompt, std::strlen(kPrompt), prompt.data(), prompt.size(), true, true) != token_count) {
        std::cerr << "ADMIN_ERROR: tokenization failed\n";
        return 4;
    }

    context_ptr first = create_context(model.get());
    context_ptr second = create_context(model.get());
    if (!first || !second) {
        std::cerr << "ADMIN_ERROR: context creation failed\n";
        return 5;
    }
    if (!decode_prompt(first.get(), prompt) || !decode_prompt(second.get(), prompt)) {
        std::cerr << "ADMIN_ERROR: decode failed\n";
        return 6;
    }

    const llm_expert_provider_stats stats = model->expert_weight_provider_stats();
    const llm_expert_graph_diagnostics first_graph = first->expert_graph_diagnostics();
    const llm_expert_graph_diagnostics second_graph = second->expert_graph_diagnostics();
    std::cout << "ADMIN"
              << "\tcontexts=2"
              << "\tprompt_tokens=" << prompt.size()
              << "\tbind_calls=" << stats.bind_calls
              << "\tprepare_calls=" << stats.prepare_calls
              << "\thandles_acquired=" << stats.handles_acquired
              << "\thandles_released=" << stats.handles_released
              << "\tfirst_bindings=" << first_graph.binding_count
              << "\tsecond_bindings=" << second_graph.binding_count;
#ifdef K3_FAST_PATH_DIAGNOSTICS
    std::cout << "\tbundle_registrations=" << stats.bundle_registrations
              << "\tbundle_full_validations=" << stats.bundle_full_validations
              << "\tbundle_fast_path_hits=" << stats.bundle_fast_path_hits
              << "\tfirst_binding_capacity=" << first_graph.binding_capacity
              << "\tsecond_binding_capacity=" << second_graph.binding_capacity;
#endif
    std::cout << "\nRESULT\texit=0\n";
    return 0;
}

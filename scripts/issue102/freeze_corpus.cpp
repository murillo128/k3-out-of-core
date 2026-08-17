#include "llama.h"
#include "llama-cpp.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

using json = nlohmann::ordered_json;

namespace {

struct arguments {
    std::string model;
    std::string input;
    std::string output;
};

bool parse_arguments(int argc, char ** argv, arguments & args) {
    for (int index = 1; index < argc; ++index) {
        if (index + 1 >= argc) return false;
        const std::string option = argv[index];
        const std::string value = argv[++index];
        if (option == "--model") args.model = value;
        else if (option == "--input") args.input = value;
        else if (option == "--output") args.output = value;
        else return false;
    }
    return !args.model.empty() && !args.input.empty() && !args.output.empty();
}

std::string render_prompt(
        const llama_model *,
        const std::string & raw_prompt) {
    const std::string tick(1, char(96));
    const std::string prefix =
        "<|open|>message role=\"system\" type=\"thinking-effort\"<|sep|>" +
        tick + "thinking_effort" + tick +
        " guides on how much to think in your thinking channel "
        "(not including the response channel), supported values include " +
        tick + "low" + tick + ", " + tick + "medium" + tick + ", " +
        tick + "high" + tick + ", and " + tick + "max" + tick +
        ".\nNow the system is invoked with " + tick + "thinking_effort=max" + tick +
        ".<|close|>message<|sep|><|end_of_msg|>"
        "<|open|>message role=\"user\"<|sep|>";
    static const std::string suffix =
        "<|close|>message<|sep|><|end_of_msg|>"
        "<|open|>message role=\"assistant\"<|sep|><|open|>think<|sep|>";
    return prefix + raw_prompt + suffix;
}

int token_count(const llama_vocab * vocab, const std::string & prompt) {
    const int count = -llama_tokenize(vocab, prompt.data(), prompt.size(), nullptr, 0, true, true);
    if (count <= 0) throw std::runtime_error("tokenization failed");
    return count;
}

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::cerr << "usage: " << argv[0] << " --model GGUF --input DRAFT.json --output FROZEN.json\n";
        return 2;
    }

    try {
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level == GGML_LOG_LEVEL_ERROR) std::fputs(text, stderr);
        }, nullptr);
        auto model_params = llama_model_default_params();
        model_params.n_gpu_layers = 0;
        model_params.vocab_only = true;
        llama_model_ptr model(llama_model_load_from_file(args.model.c_str(), model_params));
        if (!model) throw std::runtime_error("vocabulary-only model load failed");
        const llama_vocab * vocab = llama_model_get_vocab(model.get());

        std::ifstream input(args.input);
        if (!input) throw std::runtime_error("unable to open draft corpus");
        json draft;
        input >> draft;
        if (draft.at("version") != "owner-preregistered-candidate-v2") {
            throw std::runtime_error("unexpected owner candidate version");
        }
        if (draft.at("cases").size() != 128) throw std::runtime_error("draft must contain exactly 128 primary cases");

        std::set<std::string> ids;
        std::set<std::string> raw_prompts;
        std::set<std::string> families;
        const std::vector<std::string> semantic_families = {
            "mathematical reasoning",
            "formal logic / proof-style reasoning",
            "physics / scientific reasoning",
            "factual / explanatory knowledge",
            "code generation",
            "debugging / code review",
            "algorithms / data-structure reasoning",
            "summarization / synthesis",
            "structured extraction / transformation",
            "planning / constraint satisfaction",
            "multi-step instruction following / structured response",
            "analytical comparison / argumentation",
            "creative / language generation",
            "conversational / direct QA",
            "Spanish-language reasoning/explanation",
            "multilingual / translation / cross-language transformation",
        };
        json frozen_cases = json::array();
        json order = json::array();
        std::vector<int> prior_family_counts(16, 0);
        size_t index = 0;
        for (const auto & item : draft.at("cases")) {
            const int length_level = item.at("band").get<int>();
            const int family_index = int(index / 8) + 1;
            const std::string id = item.at("id").get<std::string>();
            const std::string family = item.at("family").get<std::string>();
            const std::string raw = item.at("prompt").get<std::string>();
            if (length_level < 1 || length_level > 8 || family_index < 1 || family_index > 16) {
                throw std::runtime_error("invalid family or length-level index");
            }
            const int expected_level = int(index % 8) + 1;
            if (length_level != expected_level) {
                throw std::runtime_error("owner cases are not in frozen family/length-level order");
            }
            const std::string expected_id = std::string(family_index < 10 ? "0" : "") +
                std::to_string(family_index) + "-" + family + "-b" + std::to_string(length_level);
            if (id != expected_id || !ids.insert(id).second || !raw_prompts.insert(raw).second) {
                throw std::runtime_error("case ID or prompt uniqueness failure");
            }
            families.insert(family);
            const std::string rendered = render_prompt(model.get(), raw);
            const int count = token_count(vocab, rendered);
            if (length_level > 1 && count <= prior_family_counts[size_t(family_index - 1)]) {
                throw std::runtime_error("templated token counts are not strictly increasing within family: " + id);
            }
            prior_family_counts[size_t(family_index - 1)] = count;
            if (count + 64 > 768) {
                throw std::runtime_error("prompt plus decode horizon exceeds n_ctx=768: " + id);
            }
            json frozen = {
                {"id", id},
                {"family_index", family_index},
                {"semantic_family", semantic_families[size_t(family_index - 1)]},
                {"owner_family", family},
                {"length_level", length_level},
                {"round", length_level},
                {"position", family_index},
                {"raw_prompt", raw},
            };
            frozen["templated_prompt"] = rendered;
            frozen["observed_templated_prompt_tokens"] = count;
            frozen_cases.push_back(std::move(frozen));
            ++index;
        }
        if (families.size() != 16) throw std::runtime_error("corpus does not contain exactly 16 semantic families");

        for (int length_level = 1; length_level <= 8; ++length_level) {
            for (int family_index = 1; family_index <= 16; ++family_index) {
                const auto & item = frozen_cases[size_t((family_index - 1)*8 + (length_level - 1))];
                order.push_back(item.at("id"));
            }
            order.push_back("issue102-sentinel");
        }

        json sentinel = {
            {"id", "issue102-sentinel"},
            {"semantic_family", "sentinel"},
            {"length_level", 0},
            {"raw_prompt", "Explain why a careful measurement should distinguish observed facts from assumptions."},
        };
        sentinel["templated_prompt"] = render_prompt(model.get(), sentinel.at("raw_prompt").get<std::string>());
        sentinel["observed_templated_prompt_tokens"] = token_count(vocab, sentinel.at("templated_prompt").get<std::string>());
        if (sentinel.at("observed_templated_prompt_tokens").get<int>() != 100) {
            throw std::runtime_error("full-prompt sentinel does not preserve the 100-token prompt identity");
        }

        const json output = {
            {"schema_version", "issue102-cross-prompt-corpus-v1"},
            {"status", "frozen-before-performance"},
            {"owner_candidate", {
                {"version", "owner-preregistered-candidate-v2"},
                {"canonical_sha256", "3535638264d920b025e8c99caedf2197a73f3a7d4a274d865bd7f4defbdf3ef6"},
                {"canonicalization", draft.at("canonicalization")},
            }},
            {"template", {
                {"model_embedded_template", false},
                {"frozen_path", "issue73 Kimi-K3 max-thinking chat wrapper"},
                {"wrapper_placeholder_sha256", "2fba93a789cd8e3656306446e174f8ec8478f7ea6092727836c357421a335e86"},
                {"sentinel_templated_prompt_sha256", "619f2ebc9be1e36147b9f5c24095b3daa16b04d67ef4e9f354d0607a3f4517fa"},
                {"tokenizer_model_manifest_sha256", "58b14d13a602944e1134fc753b2cc819a84a31290aee9c1479264a66dbb5efe2"},
                {"enable_thinking", true}, {"thinking_effort", "max"},
                {"tokenize_add_special", true}, {"tokenize_parse_special", true},
            }},
            {"design", {
                {"semantic_families", 16}, {"length_levels", 8},
                {"length_semantics", "ordered within-family factor; actual templated token count is quantitative"},
                {"primary_cases", 128}, {"decode_horizon", 64}, {"n_ctx", 768},
                {"ordering", "length-level-outer/family-inner/sentinel-after-each-level"},
            }},
            {"cases", frozen_cases},
            {"sentinel", sentinel},
            {"execution_order", order},
        };
        std::ofstream out(args.output, std::ios::trunc);
        if (!out) throw std::runtime_error("unable to open frozen corpus output");
        out << output.dump(2) << '\n';
        if (!out) throw std::runtime_error("unable to write frozen corpus output");
        std::cout << output.dump() << '\n';
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "issue102-corpus-freezer: " << error.what() << '\n';
        return 1;
    }
}

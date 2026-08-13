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
        if (draft.at("cases").size() != 128) throw std::runtime_error("draft must contain exactly 128 primary cases");

        const std::vector<std::pair<int, int>> bands = {
            {64, 95}, {96, 127}, {128, 159}, {160, 191},
            {192, 223}, {224, 255}, {256, 287}, {288, 319},
        };
        std::set<std::string> ids;
        std::set<std::string> raw_prompts;
        std::set<std::string> families;
        json frozen_cases = json::array();
        json order = json::array();
        bool all_counts_valid = true;
        size_t index = 0;
        for (const auto & item : draft.at("cases")) {
            const int band = item.at("token_band").get<int>();
            const int family_index = item.at("family_index").get<int>();
            const std::string id = item.at("id").get<std::string>();
            const std::string family = item.at("semantic_family").get<std::string>();
            const std::string raw = item.at("raw_prompt").get<std::string>();
            if (band < 1 || band > 8 || family_index < 1 || family_index > 16) {
                throw std::runtime_error("invalid family or band index");
            }
            const size_t expected_index = size_t((band - 1)*16 + (family_index - 1));
            if (index != expected_index) throw std::runtime_error("cases are not in frozen band-outer/family-inner order");
            const std::string expected_id = "f" + std::string(family_index < 10 ? "0" : "") +
                std::to_string(family_index) + "-b" + std::to_string(band);
            if (id != expected_id || !ids.insert(id).second || !raw_prompts.insert(raw).second) {
                throw std::runtime_error("case ID or prompt uniqueness failure");
            }
            families.insert(family);
            const std::string rendered = render_prompt(model.get(), raw);
            const int count = token_count(vocab, rendered);
            if (count < bands[size_t(band - 1)].first || count > bands[size_t(band - 1)].second) {
                std::cerr << id << " count=" << count << " expected=" << bands[size_t(band - 1)].first
                          << "-" << bands[size_t(band - 1)].second << "\n";
                all_counts_valid = false;
            }
            if (count + 64 > 512) throw std::runtime_error("prompt plus decode horizon exceeds n_ctx=512");
            json frozen = item;
            frozen["templated_prompt"] = rendered;
            frozen["expected_prompt_tokens"] = count;
            frozen_cases.push_back(std::move(frozen));
            order.push_back(id);
            if (family_index == 16) order.push_back("issue102-sentinel");
            ++index;
        }
        if (families.size() != 16) throw std::runtime_error("corpus does not contain exactly 16 semantic families");
        if (!all_counts_valid) throw std::runtime_error("one or more templated prompts are outside their assigned token band");

        json sentinel = draft.at("sentinel");
        sentinel["templated_prompt"] = render_prompt(model.get(), sentinel.at("raw_prompt").get<std::string>());
        sentinel["expected_prompt_tokens"] = token_count(vocab, sentinel.at("templated_prompt").get<std::string>());
        if (sentinel.at("expected_prompt_tokens").get<int>() != 100) {
            throw std::runtime_error("full-prompt sentinel does not preserve the 100-token prompt identity");
        }

        const json output = {
            {"schema_version", "issue102-cross-prompt-corpus-v1"},
            {"status", "frozen-before-performance"},
            {"template", {
                {"model_embedded_template", false},
                {"frozen_path", "issue73 Kimi-K3 max-thinking chat wrapper"},
                {"enable_thinking", true}, {"thinking_effort", "max"},
                {"tokenize_add_special", true}, {"tokenize_parse_special", true},
            }},
            {"design", {
                {"semantic_families", 16}, {"token_bands", 8},
                {"primary_cases", 128}, {"decode_horizon", 64}, {"n_ctx", 512},
                {"ordering", "token-band-outer/family-inner/sentinel-after-each-band"},
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

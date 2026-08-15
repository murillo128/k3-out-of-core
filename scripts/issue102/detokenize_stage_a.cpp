#include "llama.h"
#include "llama-cpp.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using json = nlohmann::ordered_json;
namespace fs = std::filesystem;

namespace {

struct arguments {
    std::string model;
    std::string corpus;
    std::string stage_a_root;
    std::string output;
    std::string project_sha;
    std::string nested_sha;
    std::string checkpoint_sha256;
    std::string model_identity_sha256;
};

bool parse_arguments(int argc, char ** argv, arguments & args) {
    for (int index = 1; index < argc; ++index) {
        if (index + 1 >= argc) {
            return false;
        }
        const std::string option = argv[index];
        const char * value = argv[++index];
        if (option == "--model") args.model = value;
        else if (option == "--corpus") args.corpus = value;
        else if (option == "--stage-a-root") args.stage_a_root = value;
        else if (option == "--output") args.output = value;
        else if (option == "--project-sha") args.project_sha = value;
        else if (option == "--nested-sha") args.nested_sha = value;
        else if (option == "--checkpoint-sha256") args.checkpoint_sha256 = value;
        else if (option == "--model-identity-sha256") args.model_identity_sha256 = value;
        else return false;
    }
    return !args.model.empty() && !args.corpus.empty() && !args.stage_a_root.empty() &&
        !args.output.empty() && !args.project_sha.empty() && !args.nested_sha.empty() &&
        !args.checkpoint_sha256.empty() && !args.model_identity_sha256.empty();
}

json load_json(const fs::path & path) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("unable to open JSON input: " + path.string());
    }
    json value;
    input >> value;
    return value;
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

std::string token_piece(const llama_vocab * vocab, llama_token token) {
    std::vector<char> buffer(32);
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

std::string detokenize(const llama_vocab * vocab, const std::vector<llama_token> & tokens) {
    std::vector<char> buffer(tokens.size()*8 + 32);
    int32_t count = llama_detokenize(
        vocab, tokens.data(), int32_t(tokens.size()), buffer.data(),
        int32_t(buffer.size()), false, true);
    if (count < 0) {
        buffer.resize(size_t(-count));
        count = llama_detokenize(
            vocab, tokens.data(), int32_t(tokens.size()), buffer.data(),
            int32_t(buffer.size()), false, true);
    }
    if (count < 0 || size_t(count) > buffer.size()) {
        throw std::runtime_error("detokenization failed");
    }
    return std::string(buffer.data(), size_t(count));
}

fs::path result_path(const fs::path & root, size_t ordinal, const std::string & case_id) {
    std::ostringstream directory;
    directory << "run-" << std::setfill('0') << std::setw(3) << ordinal << '-' << case_id;
    return root / directory.str() / "result.json";
}

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::cerr
            << "usage: " << argv[0]
            << " --model GGUF --corpus JSON --stage-a-root DIR --output JSON"
               " --project-sha SHA --nested-sha SHA --checkpoint-sha256 SHA"
               " --model-identity-sha256 SHA\n";
        return 2;
    }

    try {
        llama_log_set([](ggml_log_level level, const char * text, void *) {
            if (level == GGML_LOG_LEVEL_ERROR) {
                std::fputs(text, stderr);
            }
        }, nullptr);

        auto params = llama_model_default_params();
        params.n_gpu_layers = 0;
        params.vocab_only = true;
        params.use_extra_bufts = false;
        llama_model_ptr model(llama_model_load_from_file(args.model.c_str(), params));
        if (!model) {
            throw std::runtime_error("vocabulary-only model load failed");
        }
        const llama_vocab * vocab = llama_model_get_vocab(model.get());
        if (vocab == nullptr) {
            throw std::runtime_error("model vocabulary is unavailable");
        }
        const int32_t vocabulary_size = llama_vocab_n_tokens(vocab);

        const fs::path corpus_path = fs::absolute(args.corpus);
        const fs::path stage_a_root = fs::absolute(args.stage_a_root);
        const json corpus = load_json(corpus_path);
        const auto & corpus_cases = corpus.at("cases");
        if (corpus_cases.size() != 128) {
            throw std::runtime_error("expected exactly 128 corpus cases");
        }

        std::vector<std::string> primary_ids;
        const std::string sentinel_id = corpus.at("sentinel").at("id").get<std::string>();
        for (const auto & entry : corpus.at("execution_order")) {
            const std::string id = entry.get<std::string>();
            if (id != sentinel_id) {
                primary_ids.push_back(id);
            }
        }
        if (primary_ids.size() != 128) {
            throw std::runtime_error("execution order does not contain exactly 128 primary cases");
        }

        json rows = json::array();
        for (size_t index = 0; index < primary_ids.size(); ++index) {
            const std::string & case_id = primary_ids[index];
            const auto selected = std::find_if(
                corpus_cases.begin(), corpus_cases.end(), [&](const json & item) {
                    return item.value("id", "") == case_id;
                });
            if (selected == corpus_cases.end()) {
                throw std::runtime_error("execution-order case is absent from corpus: " + case_id);
            }
            const auto & corpus_case = *selected;
            const size_t corpus_index = size_t(std::distance(corpus_cases.begin(), selected));
            const fs::path source_path = result_path(stage_a_root, index + 1, case_id);
            const json result = load_json(source_path);
            const auto & result_case = result.at("case");
            const auto & output = result.at("output");
            const std::vector<llama_token> generated =
                output.at("generated_ids").get<std::vector<llama_token>>();
            const std::string observed_hash = hex_u64(token_hash(generated));
            const int expected_prompt_tokens =
                corpus_case.at("observed_templated_prompt_tokens").get<int>();

            if (result.at("status") != "pass" || result.at("point") != "S2_P50" ||
                result.at("protocol") != "full-prompt" || result_case.at("id") != case_id ||
                result_case.at("semantic_family") != corpus_case.at("semantic_family") ||
                result_case.at("length_level") != corpus_case.at("length_level") ||
                result_case.at("templated_prompt_tokens") != expected_prompt_tokens ||
                output.at("generated_token_count").get<size_t>() != generated.size() ||
                output.at("generated_token_hash").get<std::string>() != observed_hash ||
                generated.size() != 64) {
                throw std::runtime_error("frozen result identity mismatch: " + case_id);
            }
            for (llama_token token : generated) {
                if (token < 0 || token >= vocabulary_size) {
                    throw std::runtime_error("generated token is outside the frozen vocabulary: " + case_id);
                }
            }

            json observations = json::array();
            for (size_t position = 0; position < generated.size(); ++position) {
                const llama_token token = generated[position];
                const bool is_eog = llama_vocab_is_eog(vocab, token);
                const bool is_control = llama_vocab_is_control(vocab, token);
                if (is_eog || is_control) {
                    observations.push_back({
                        {"position", position + 1},
                        {"token_id", token},
                        {"is_eog", is_eog},
                        {"is_control", is_control},
                        {"rendered_piece", token_piece(vocab, token)},
                    });
                }
            }

            rows.push_back({
                {"case_id", case_id},
                {"semantic_family", corpus_case.at("semantic_family")},
                {"length_level", corpus_case.at("length_level")},
                {"corpus_reference", "corpus/phase13/issue102-cross-prompt-v1.json#cases/" +
                    std::to_string(corpus_index)},
                {"source_result_path", source_path.string()},
                {"templated_prompt_tokens", expected_prompt_tokens},
                {"generated_token_count", generated.size()},
                {"generated_token_hash", observed_hash},
                {"generated_ids", generated},
                {"detokenized_generated_text", detokenize(vocab, generated)},
                {"special_control_token_observations", observations},
            });
        }

        const json artifact = {
            {"schema_version", "issue102-semantic-sanity-detokenized-v1"},
            {"status", "pass"},
            {"provenance", {
                {"project", args.project_sha},
                {"nested_llama_cpp", args.nested_sha},
                {"stage_a_checkpoint_sha256", args.checkpoint_sha256},
                {"model_identity_manifest_sha256", args.model_identity_sha256},
                {"model_path", fs::absolute(args.model).string()},
                {"corpus_path", corpus_path.string()},
                {"stage_a_root", stage_a_root.string()},
                {"model_load_mode", "vocabulary-only"},
                {"detokenize_remove_special", false},
                {"detokenize_unparse_special", true},
            }},
            {"case_count", rows.size()},
            {"cases", rows},
        };

        const fs::path output_path = fs::absolute(args.output);
        fs::create_directories(output_path.parent_path());
        const fs::path temporary = output_path.string() + ".tmp";
        {
            std::ofstream output(temporary, std::ios::binary | std::ios::trunc);
            if (!output) {
                throw std::runtime_error("unable to open output artifact");
            }
            output << artifact.dump(2, ' ', false) << '\n';
            if (!output) {
                throw std::runtime_error("unable to write output artifact");
            }
        }
        fs::rename(temporary, output_path);
        std::cout << "detokenized " << rows.size() << " frozen Stage-A outputs\n";
        return 0;
    } catch (const std::exception & error) {
        std::cerr << "issue102-stage-a-detokenizer: " << error.what() << '\n';
        return 1;
    }
}

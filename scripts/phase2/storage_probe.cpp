#include "ggml-backend.h"
#include "llama.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>

namespace {

struct arguments {
    std::string model;
    std::string input_mode = "path";
    int gpu_layers = 0;
    int routed_layer_begin = 1;
    int routed_layer_end = 7;
};

bool parse_int(const char * text, int & value) {
    char * end = nullptr;
    errno = 0;
    const long parsed = std::strtol(text, &end, 10);
    if (errno || end == text || *end != '\0' || parsed < -1 || parsed > 1000000) {
        return false;
    }
    value = int(parsed);
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
        } else if (std::strcmp(argv[i], "--input-mode") == 0 && i + 1 < argc) {
            result.input_mode = argv[++i];
        } else if (std::strcmp(argv[i], "--routed-layer-begin") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.routed_layer_begin)) {
                return false;
            }
        } else if (std::strcmp(argv[i], "--routed-layer-end") == 0 && i + 1 < argc) {
            if (!parse_int(argv[++i], result.routed_layer_end)) {
                return false;
            }
        } else {
            return false;
        }
    }
    return !result.model.empty() && result.routed_layer_begin >= 0 &&
        result.routed_layer_end >= result.routed_layer_begin &&
        (result.input_mode == "path" || result.input_mode == "file-pointer" || result.input_mode == "user-metadata");
}

void json_string(const char * value) {
    std::cout << '"';
    for (const unsigned char c : std::string(value ? value : "")) {
        switch (c) {
            case '"': std::cout << "\\\""; break;
            case '\\': std::cout << "\\\\"; break;
            case '\b': std::cout << "\\b"; break;
            case '\f': std::cout << "\\f"; break;
            case '\n': std::cout << "\\n"; break;
            case '\r': std::cout << "\\r"; break;
            case '\t': std::cout << "\\t"; break;
            default:
                if (c < 0x20) {
                    static const char digits[] = "0123456789abcdef";
                    std::cout << "\\u00" << digits[c >> 4] << digits[c & 0xf];
                } else {
                    std::cout << char(c);
                }
        }
    }
    std::cout << '"';
}

void print_i64_array(const int64_t * values) {
    std::cout << '[';
    for (int i = 0; i < GGML_MAX_DIMS; ++i) {
        if (i) {
            std::cout << ',';
        }
        std::cout << values[i];
    }
    std::cout << ']';
}

void print_u64_array(const uint64_t * values) {
    std::cout << '[';
    for (int i = 0; i < GGML_MAX_DIMS; ++i) {
        if (i) {
            std::cout << ',';
        }
        std::cout << values[i];
    }
    std::cout << ']';
}

void set_tensor_data(ggml_tensor *, void *) {
}

void quiet_log(ggml_log_level, const char *, void *) {
}

struct gguf_deleter {
    void operator()(gguf_context * context) const {
        gguf_free(context);
    }
};

struct ggml_deleter {
    void operator()(ggml_context * context) const {
        ggml_free(context);
    }
};

} // namespace

int main(int argc, char ** argv) {
    arguments args;
    if (!parse_arguments(argc, argv, args)) {
        std::cerr << "usage: storage-probe --model PATH --gpu-layers N [--input-mode path|file-pointer|user-metadata] [--routed-layer-begin N --routed-layer-end N]\n";
        return 2;
    }

    llama_log_set(quiet_log, nullptr);
    ggml_backend_load_all();
    llama_model_params params = llama_model_default_params();
    params.n_gpu_layers = args.gpu_layers;
    params.load_mode = LLAMA_LOAD_MODE_NONE;
    params.no_alloc = true;

    llama_model * model = nullptr;
    FILE * file = nullptr;
    std::unique_ptr<gguf_context, gguf_deleter> user_metadata;
    std::unique_ptr<ggml_context, ggml_deleter> user_tensor_context;

    if (args.input_mode == "path") {
        model = llama_model_load_from_file(args.model.c_str(), params);
    } else if (args.input_mode == "file-pointer") {
        file = std::fopen(args.model.c_str(), "rb");
        if (file) {
            model = llama_model_load_from_file_ptr(file, params);
        }
    } else {
        ggml_context * tensor_context = nullptr;
        const gguf_init_params gguf_params = {/*.no_alloc =*/ true, /*.ctx =*/ &tensor_context};
        user_metadata.reset(gguf_init_from_file(args.model.c_str(), gguf_params));
        user_tensor_context.reset(tensor_context);
        if (user_metadata) {
            model = llama_model_init_from_user(user_metadata.get(), set_tensor_data, nullptr, params);
        }
    }

    if (!model) {
        if (file) {
            std::fclose(file);
        }
        std::cerr << "STORAGE_ERROR: model load failed\n";
        return 3;
    }

    uint32_t source_count = 0;
    const int32_t source_status = llama_model_source_file_count(model, &source_count);
    if (args.input_mode != "path") {
        std::cout << "{\"status\":" << source_status << ",\"source_file_count\":" << source_count << "}\n";
        llama_model_free(model);
        if (file) {
            std::fclose(file);
        }
        return source_status == LLAMA_MODEL_STORAGE_ERROR_NO_FILE_BACKING_METADATA ? 0 : 4;
    }
    if (source_status != LLAMA_MODEL_STORAGE_STATUS_OK) {
        std::cerr << "STORAGE_ERROR: source metadata unavailable with status " << source_status << '\n';
        llama_model_free(model);
        return 4;
    }
    struct llama_model_source_file_metadata missing_source = {};
    struct llama_model_tensor_storage_metadata missing_tensor = {};
    if (llama_model_source_file_count(model, nullptr) != LLAMA_MODEL_STORAGE_ERROR_INVALID_ARGUMENT ||
        llama_model_get_source_file_metadata(model, source_count, &missing_source) != LLAMA_MODEL_STORAGE_ERROR_NOT_FOUND ||
        llama_model_get_tensor_storage_metadata(model, "not-a-tensor", &missing_tensor) != LLAMA_MODEL_STORAGE_ERROR_NOT_FOUND) {
        std::cerr << "STORAGE_ERROR: status contract mismatch\n";
        llama_model_free(model);
        return 5;
    }

    std::cout << "{\"status\":0,\"source_files\":[";
    for (uint32_t index = 0; index < source_count; ++index) {
        struct llama_model_source_file_metadata source = {};
        if (llama_model_get_source_file_metadata(model, index, &source) != LLAMA_MODEL_STORAGE_STATUS_OK) {
            std::cerr << "STORAGE_ERROR: source metadata query failed\n";
            llama_model_free(model);
            return 6;
        }
        if (index) {
            std::cout << ',';
        }
        std::cout << "{\"index\":" << source.index << ",\"identity\":";
        json_string(source.identity);
        std::cout << ",\"size\":" << source.size << ",\"gguf_alignment\":" << source.gguf_alignment << '}';
    }

    std::cout << "],\"tensors\":[";
    bool first_tensor = true;
    const char * projections[] = {"gate", "up", "down"};
    for (int layer = args.routed_layer_begin; layer <= args.routed_layer_end; ++layer) {
        for (const char * projection : projections) {
            const std::string name = "blk." + std::to_string(layer) + ".ffn_" + projection + "_exps.weight";
            struct llama_model_tensor_storage_metadata storage = {};
            const int32_t status = llama_model_get_tensor_storage_metadata(model, name.c_str(), &storage);
            if (status != LLAMA_MODEL_STORAGE_STATUS_OK) {
                std::cerr << "STORAGE_ERROR: tensor query failed for " << name << " with status " << status << '\n';
                llama_model_free(model);
                return 7;
            }
            if (!first_tensor) {
                std::cout << ',';
            }
            first_tensor = false;
            std::cout << "{\"tensor_name\":";
            json_string(storage.tensor_name);
            std::cout << ",\"source_file_index\":" << storage.source_file_index
                      << ",\"gguf_alignment\":" << storage.gguf_alignment
                      << ",\"file_offset\":" << storage.file_offset
                      << ",\"byte_size\":" << storage.byte_size
                      << ",\"ggml_type_id\":" << int(storage.type)
                      << ",\"ggml_type_name\":";
            json_string(ggml_type_name(storage.type));
            std::cout << ",\"n_dims\":" << storage.n_dims << ",\"logical_shape\":";
            print_i64_array(storage.logical_shape);
            std::cout << ",\"physical_strides\":";
            print_u64_array(storage.physical_strides);
            std::cout << ",\"runtime_buffer_type\":";
            json_string(storage.runtime_buffer_type);
            std::cout << ",\"runtime_layout_transform\":" << (storage.runtime_layout_transform ? "true" : "false")
                      << ",\"runtime_backend_transform\":" << (storage.runtime_backend_transform ? "true" : "false")
                      << ",\"runtime_repack\":" << (storage.runtime_repack ? "true" : "false") << '}';
        }
    }
    std::cout << "]}\n";

    llama_model_free(model);
    return 0;
}

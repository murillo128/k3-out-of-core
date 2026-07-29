#pragma once

#include "llama.h"

#include <cstddef>
#include <cstdint>
#include <fstream>
#include <string>
#include <vector>

struct route_trace_metadata {
    std::string model_name;
    uint64_t model_size;
    std::string model_sha256;
    std::string model_source_revision;
    std::string published_gguf_revision;
    std::string llama_cpp_revision;
    std::string run_id;
    uint32_t expert_count;
    uint32_t top_k;
    uint32_t routed_layer_count;
    size_t max_ubatch_payload;
};

class route_trace_writer {
public:
    route_trace_writer(const std::string & path, const route_trace_metadata & metadata);
    ~route_trace_writer();

    bool write(const llama_route_observation & observation);
    bool flush();
    bool finalize();
    void abort();

    bool good() const;
    uint64_t record_count() const;
    uint64_t bytes_written() const;
    uint64_t flush_count() const;

private:
    bool append(const uint8_t * data, size_t size);
    bool append_u32(std::vector<uint8_t> & output, uint32_t value);
    bool append_i32(std::vector<uint8_t> & output, int32_t value);
    bool append_u64(std::vector<uint8_t> & output, uint64_t value);
    bool append_string(std::vector<uint8_t> & output, const std::string & value);
    void fail();

    route_trace_metadata metadata_;
    std::ofstream output_;
    std::vector<uint8_t> buffer_;
    std::vector<uint8_t> frame_;
    size_t buffer_capacity_ = 0;
    uint32_t checksum_ = 0;
    uint64_t record_count_ = 0;
    uint64_t bytes_written_ = 0;
    uint64_t flush_count_ = 0;
    bool failed_ = false;
    bool finalized_ = false;
};

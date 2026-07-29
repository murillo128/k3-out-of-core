#include "route_trace.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

namespace {

constexpr uint8_t kFileMagic[8] = { 'K', '3', 'R', 'O', 'U', 'T', 'E', 0 };
constexpr uint8_t kTrailerMagic[8] = { 'K', '3', 'D', 'O', 'N', 'E', 0, 0 };
constexpr uint32_t kSchemaVersion = 1;
constexpr uint32_t kRecordMagic = 0x44434552U;
constexpr size_t kTrailerSize = 24;
constexpr size_t kMinimumBuffer = 4096;

uint32_t checksum_update(uint32_t checksum, const uint8_t * data, size_t size) {
    uint32_t value = ~checksum;
    for (size_t i = 0; i < size; ++i) {
        value ^= data[i];
        for (int bit = 0; bit < 8; ++bit) {
            const uint32_t mask = -(value & 1U);
            value = (value >> 1) ^ (0xedb88320U & mask);
        }
    }
    return ~value;
}

void append_u32_raw(std::vector<uint8_t> & output, uint32_t value) {
    output.push_back((uint8_t) (value & 0xffU));
    output.push_back((uint8_t) ((value >> 8) & 0xffU));
    output.push_back((uint8_t) ((value >> 16) & 0xffU));
    output.push_back((uint8_t) ((value >> 24) & 0xffU));
}

void append_u64_raw(std::vector<uint8_t> & output, uint64_t value) {
    append_u32_raw(output, (uint32_t) (value & 0xffffffffULL));
    append_u32_raw(output, (uint32_t) (value >> 32));
}

} // namespace

route_trace_writer::route_trace_writer(const std::string & path, const route_trace_metadata & metadata) : metadata_(metadata) {
    buffer_capacity_ = std::max(kMinimumBuffer, metadata_.max_ubatch_payload);
    buffer_.reserve(buffer_capacity_);
    frame_.reserve(buffer_capacity_);
    output_.open(path, std::ios::binary | std::ios::trunc);
    if (!output_) {
        fail();
        return;
    }

    std::vector<uint8_t> header;
    const bool header_valid =
        !metadata_.model_name.empty() && metadata_.model_size != 0 &&
        !metadata_.model_sha256.empty() && !metadata_.model_source_revision.empty() &&
        !metadata_.published_gguf_revision.empty() && !metadata_.llama_cpp_revision.empty() &&
        !metadata_.run_id.empty() && metadata_.expert_count != 0 && metadata_.top_k != 0 &&
        metadata_.routed_layer_count != 0 &&
        append_string(header, metadata_.model_name) &&
        append_u64(header, metadata_.model_size) &&
        append_string(header, metadata_.model_sha256) &&
        append_string(header, metadata_.model_source_revision) &&
        append_string(header, metadata_.published_gguf_revision) &&
        append_string(header, metadata_.llama_cpp_revision) &&
        append_string(header, metadata_.run_id);
    append_u32(header, metadata_.expert_count);
    append_u32(header, metadata_.top_k);
    append_u32(header, metadata_.routed_layer_count);

    if (!header_valid || header.size() > buffer_capacity_ || header.size() > std::numeric_limits<uint32_t>::max()) {
        fail();
        return;
    }

    std::vector<uint8_t> prefix(kFileMagic, kFileMagic + sizeof(kFileMagic));
    append_u32(prefix, kSchemaVersion);
    append_u32(prefix, (uint32_t) header.size());
    if (!append(prefix.data(), prefix.size()) || !append(header.data(), header.size())) {
        fail();
    }
}

route_trace_writer::~route_trace_writer() {
    if (!failed_ && !finalized_) {
        finalize();
    }
}

bool route_trace_writer::append_u32(std::vector<uint8_t> & output, uint32_t value) {
    append_u32_raw(output, value);
    return true;
}

bool route_trace_writer::append_i32(std::vector<uint8_t> & output, int32_t value) {
    append_u32_raw(output, (uint32_t) value);
    return true;
}

bool route_trace_writer::append_u64(std::vector<uint8_t> & output, uint64_t value) {
    append_u64_raw(output, value);
    return true;
}

bool route_trace_writer::append_string(std::vector<uint8_t> & output, const std::string & value) {
    if (value.size() > std::numeric_limits<uint32_t>::max()) {
        return false;
    }
    append_u32(output, (uint32_t) value.size());
    output.insert(output.end(), value.begin(), value.end());
    return true;
}

bool route_trace_writer::append(const uint8_t * data, size_t size) {
    if (failed_ || finalized_ || size > buffer_capacity_) {
        return false;
    }
    if (buffer_.size() + size > buffer_capacity_ && !flush()) {
        return false;
    }
    buffer_.insert(buffer_.end(), data, data + size);
    return true;
}

bool route_trace_writer::flush() {
    if (failed_ || buffer_.empty()) {
        return !failed_;
    }
    output_.write((const char *) buffer_.data(), buffer_.size());
    if (!output_) {
        fail();
        return false;
    }
    checksum_ = checksum_update(checksum_, buffer_.data(), buffer_.size());
    bytes_written_ += buffer_.size();
    flush_count_++;
    buffer_.clear();
    return true;
}

void route_trace_writer::fail() {
    failed_ = true;
}

bool route_trace_writer::write(const llama_route_observation & observation) {
    if (!good() || observation.n_expert_used != metadata_.top_k ||
        observation.phase == LLAMA_ROUTE_PHASE_UNSPECIFIED || observation.phase == LLAMA_ROUTE_PHASE_MIXED ||
        observation.positions == nullptr || observation.n_seq_ids == nullptr || observation.seq_ids == nullptr ||
        observation.selected_experts == nullptr || observation.weights == nullptr) {
        fail();
        return false;
    }

    for (uint32_t row = 0; row < observation.n_tokens; ++row) {
        const int32_t n_seq_ids = observation.n_seq_ids[row];
        if (n_seq_ids <= 0 || observation.seq_ids[row] == nullptr) {
            fail();
            return false;
        }

        const size_t fixed_frame_size = 56;
        if ((size_t) n_seq_ids > (SIZE_MAX - fixed_frame_size)/(sizeof(int32_t)) ||
            metadata_.top_k > (SIZE_MAX - fixed_frame_size - (size_t) n_seq_ids*sizeof(int32_t))/(2*sizeof(uint32_t))) {
            fail();
            return false;
        }
        const size_t frame_size = fixed_frame_size + (size_t) n_seq_ids*sizeof(int32_t) +
            2*(size_t) metadata_.top_k*sizeof(uint32_t);
        if (frame_size > buffer_capacity_ || frame_size - 8 > std::numeric_limits<uint32_t>::max()) {
            fail();
            return false;
        }

        frame_.clear();
        append_u32(frame_, kRecordMagic);
        append_u32(frame_, (uint32_t) (frame_size - 8));
        append_u64(frame_, record_count_);
        append_u64(frame_, observation.request_ordinal);
        append_u64(frame_, observation.ubatch_ordinal);
        append_u32(frame_, (uint32_t) observation.phase);
        append_i32(frame_, observation.layer);
        append_u32(frame_, row);
        append_i32(frame_, observation.positions[row]);
        append_u32(frame_, (uint32_t) n_seq_ids);
        for (int32_t i = 0; i < n_seq_ids; ++i) {
            append_i32(frame_, observation.seq_ids[row][i]);
        }
        append_u32(frame_, observation.n_expert_used);
        for (uint32_t rank = 0; rank < observation.n_expert_used; ++rank) {
            const size_t index = (size_t) row*observation.n_expert_used + rank;
            const int32_t expert = observation.selected_experts[index];
            const float weight = observation.weights[index];
            if (expert < 0 || (uint32_t) expert >= metadata_.expert_count || !std::isfinite(weight)) {
                fail();
                return false;
            }
            append_i32(frame_, expert);
        }
        for (uint32_t rank = 0; rank < observation.n_expert_used; ++rank) {
            const size_t index = (size_t) row*observation.n_expert_used + rank;
            uint32_t bits;
            std::memcpy(&bits, &observation.weights[index], sizeof(bits));
            append_u32(frame_, bits);
        }

        if (frame_.size() != frame_size || !append(frame_.data(), frame_.size())) {
            fail();
            return false;
        }
        record_count_++;
    }
    return true;
}

bool route_trace_writer::finalize() {
    if (failed_ || finalized_ || !flush()) {
        return false;
    }

    std::vector<uint8_t> trailer(kTrailerMagic, kTrailerMagic + sizeof(kTrailerMagic));
    append_u64_raw(trailer, record_count_);
    append_u32_raw(trailer, checksum_);
    append_u32_raw(trailer, 0);
    if (trailer.size() != kTrailerSize) {
        fail();
        return false;
    }

    output_.write((const char *) trailer.data(), trailer.size());
    output_.flush();
    if (!output_) {
        fail();
        return false;
    }
    bytes_written_ += trailer.size();
    finalized_ = true;
    return true;
}

void route_trace_writer::abort() {
    fail();
}

bool route_trace_writer::good() const {
    return !failed_ && !finalized_ && output_.good();
}

uint64_t route_trace_writer::record_count() const {
    return record_count_;
}

uint64_t route_trace_writer::bytes_written() const {
    return bytes_written_ + buffer_.size();
}

uint64_t route_trace_writer::flush_count() const {
    return flush_count_;
}

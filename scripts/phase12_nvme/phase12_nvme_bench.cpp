#include <liburing.h>
#include <openssl/evp.h>

#include "phase12_nvme_trace.h"

#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cerrno>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <dirent.h>
#include <exception>
#include <fcntl.h>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <thread>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

using Clock = std::chrono::steady_clock;

struct Options {
    std::string plan;
    std::string api;
    std::string cache_state;
    std::string output;
    unsigned qd = 1;
    unsigned iterations = 1;
    unsigned cancel_after = 0;
    unsigned inject_eio_after = 0;
    unsigned inject_stale_after = 0;
};

struct Cancelled : std::runtime_error {
    Cancelled() : std::runtime_error("injected cancellation") {}
};

struct Operation {
    std::uint64_t ordinal = 0;
    std::uint32_t source = 0;
    std::string path;
    std::uint64_t offset = 0;
    std::uint64_t length = 0;
    std::array<unsigned char, 32> expected{};
};

struct Source {
    std::string path;
    int fd = -1;
    std::uint64_t size = 0;
    std::string block_stat_path;
};

struct Interval {
    std::int64_t start_ns = 0;
    std::int64_t end_ns = 0;
};

struct Counters {
    std::uint64_t bytes = 0;
    std::uint64_t operations = 0;
    std::uint64_t short_reads = 0;
    std::uint64_t retries = 0;
};

struct BlockStat {
    std::uint64_t read_ios = 0;
    std::uint64_t read_sectors = 0;
    std::uint64_t read_ticks_ms = 0;
    std::uint64_t io_ticks_ms = 0;
    std::uint64_t weighted_ticks_ms = 0;
};

struct Residency {
    std::uint64_t pages = 0;
    std::uint64_t resident_pages = 0;
    std::uint64_t fadvise_failures = 0;
    bool sampled = true;
};

struct RunResult {
    std::vector<double> iteration_ms;
    std::vector<Interval> intervals;
    std::vector<std::size_t> interval_sources;
    Counters counters;
    std::array<unsigned char, 32> sink{};
    unsigned max_active = 0;
    unsigned ring_setup_flags = 0;
    unsigned ring_features = 0;
    unsigned ring_sq_entries = 0;
    unsigned ring_cq_entries = 0;
    unsigned worker_count = 0;
    unsigned checksum_worker_count = 0;
    std::uint64_t buffer_count = 0;
    std::uint64_t buffer_bytes = 0;
    int fd_delta = 0;
    int thread_delta = 0;
};

struct EvpContextDeleter {
    void operator()(EVP_MD_CTX * value) const { EVP_MD_CTX_free(value); }
};

using DigestContext = std::unique_ptr<EVP_MD_CTX, EvpContextDeleter>;

DigestContext new_digest();
void digest_update(EVP_MD_CTX * context, const void * data, std::size_t size);
std::array<unsigned char, 32> digest_finish(DigestContext context);

struct DigestTask {
    std::size_t identity = 0;
    const void * data = nullptr;
    std::size_t size = 0;
};

class DigestPool {
public:
    explicit DigestPool(unsigned workers) {
        for (unsigned index = 0; index < workers; ++index) {
            workers_.emplace_back([this] { worker_loop(); });
        }
    }

    DigestPool(const DigestPool &) = delete;
    DigestPool & operator=(const DigestPool &) = delete;

    ~DigestPool() {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            stop_ = true;
        }
        work_ready_.notify_all();
        for (auto & worker : workers_) worker.join();
    }

    void submit(DigestTask task) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (failure_) std::rethrow_exception(failure_);
            queue_.push_back(task);
            ++outstanding_;
        }
        work_ready_.notify_one();
    }

    bool try_result(std::pair<std::size_t, std::array<unsigned char, 32>> & output) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (results_.empty()) return false;
        output = results_.front();
        results_.pop_front();
        --outstanding_;
        if (failure_) std::rethrow_exception(failure_);
        return true;
    }

    std::pair<std::size_t, std::array<unsigned char, 32>> wait_result() {
        std::unique_lock<std::mutex> lock(mutex_);
        result_ready_.wait(lock, [this] { return !results_.empty(); });
        auto output = results_.front();
        results_.pop_front();
        --outstanding_;
        if (failure_) std::rethrow_exception(failure_);
        return output;
    }

private:
    void worker_loop() {
        while (true) {
            DigestTask task;
            {
                std::unique_lock<std::mutex> lock(mutex_);
                work_ready_.wait(lock, [this] { return stop_ || !queue_.empty(); });
                if (stop_ && queue_.empty()) return;
                task = queue_.front();
                queue_.pop_front();
            }
            std::array<unsigned char, 32> digest{};
            std::exception_ptr failure;
            try {
                auto context = new_digest();
                digest_update(context.get(), task.data, task.size);
                digest = digest_finish(std::move(context));
            } catch (...) {
                failure = std::current_exception();
            }
            {
                std::lock_guard<std::mutex> lock(mutex_);
                if (failure && !failure_) failure_ = failure;
                results_.emplace_back(task.identity, digest);
                result_ready_.notify_one();
            }
        }
    }

    std::mutex mutex_;
    std::condition_variable work_ready_;
    std::condition_variable result_ready_;
    std::deque<DigestTask> queue_;
    std::deque<std::pair<std::size_t, std::array<unsigned char, 32>>> results_;
    std::vector<std::thread> workers_;
    std::size_t outstanding_ = 0;
    std::exception_ptr failure_;
    bool stop_ = false;
};

std::int64_t now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count();
}

std::string json_escape(const std::string & input) {
    std::ostringstream output;
    for (const unsigned char byte : input) {
        switch (byte) {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if (byte < 0x20) {
                    output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<unsigned>(byte) << std::dec;
                } else {
                    output << static_cast<char>(byte);
                }
        }
    }
    return output.str();
}

std::string hex(const unsigned char * data, std::size_t size) {
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < size; ++index) {
        output << std::setw(2) << static_cast<unsigned>(data[index]);
    }
    return output.str();
}

unsigned char from_hex(char value) {
    if (value >= '0' && value <= '9') return static_cast<unsigned char>(value - '0');
    if (value >= 'a' && value <= 'f') return static_cast<unsigned char>(value - 'a' + 10);
    if (value >= 'A' && value <= 'F') return static_cast<unsigned char>(value - 'A' + 10);
    throw std::runtime_error("invalid hex digit");
}

std::array<unsigned char, 32> parse_sha256(const std::string & value) {
    if (value.size() != 64) throw std::runtime_error("SHA-256 must contain 64 hex digits");
    std::array<unsigned char, 32> output{};
    for (std::size_t index = 0; index < output.size(); ++index) {
        output[index] = static_cast<unsigned char>((from_hex(value[index * 2]) << 4) | from_hex(value[index * 2 + 1]));
    }
    return output;
}

DigestContext new_digest() {
    DigestContext context(EVP_MD_CTX_new());
    if (!context || EVP_DigestInit_ex(context.get(), EVP_sha256(), nullptr) != 1) {
        throw std::runtime_error("unable to initialize SHA-256");
    }
    return context;
}

void digest_update(EVP_MD_CTX * context, const void * data, std::size_t size) {
    if (EVP_DigestUpdate(context, data, size) != 1) throw std::runtime_error("SHA-256 update failed");
}

std::array<unsigned char, 32> digest_finish(DigestContext context) {
    std::array<unsigned char, 32> output{};
    unsigned size = 0;
    if (EVP_DigestFinal_ex(context.get(), output.data(), &size) != 1 || size != output.size()) {
        throw std::runtime_error("SHA-256 finalization failed");
    }
    return output;
}

std::vector<std::string> split_tabs(const std::string & line) {
    std::vector<std::string> fields;
    std::size_t start = 0;
    while (true) {
        const auto end = line.find('\t', start);
        fields.push_back(line.substr(start, end == std::string::npos ? end : end - start));
        if (end == std::string::npos) return fields;
        start = end + 1;
    }
}

std::vector<Operation> load_plan(const std::string & path) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("cannot open plan: " + path);
    std::vector<Operation> operations;
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty() || line[0] == '#') continue;
        const auto fields = split_tabs(line);
        if (fields.size() != 6) throw std::runtime_error("plan row must have six tab-separated fields");
        Operation operation;
        operation.ordinal = std::stoull(fields[0]);
        operation.source = static_cast<std::uint32_t>(std::stoul(fields[1]));
        operation.path = fields[2];
        operation.offset = std::stoull(fields[3]);
        operation.length = std::stoull(fields[4]);
        operation.expected = parse_sha256(fields[5]);
        if (!operation.length || operation.offset > std::numeric_limits<std::uint64_t>::max() - operation.length) {
            throw std::runtime_error("invalid operation range");
        }
        operations.push_back(std::move(operation));
    }
    if (operations.empty()) throw std::runtime_error("plan is empty");
    std::vector<std::uint64_t> ordinals;
    ordinals.reserve(operations.size());
    for (const auto & operation : operations) ordinals.push_back(operation.ordinal);
    std::sort(ordinals.begin(), ordinals.end());
    for (std::size_t index = 0; index < ordinals.size(); ++index) {
        if (ordinals[index] != index) throw std::runtime_error("plan ordinals must be exactly 0..N-1");
    }
    return operations;
}

Options parse_options(int argc, char ** argv) {
    Options options;
    for (int index = 1; index < argc; ++index) {
        const std::string argument = argv[index];
        if (argument == "--help") {
            std::cout << "usage: phase12_nvme_bench --plan PATH --api API --cache-state STATE --qd N --iterations N --output PATH [--cancel-after N] [--inject-eio-after N] [--inject-stale-after N]\n";
            std::exit(0);
        }
        if (index + 1 >= argc) throw std::runtime_error("missing value for " + argument);
        const std::string value = argv[++index];
        if (argument == "--plan") options.plan = value;
        else if (argument == "--api") options.api = value;
        else if (argument == "--cache-state") options.cache_state = value;
        else if (argument == "--qd") options.qd = static_cast<unsigned>(std::stoul(value));
        else if (argument == "--iterations") options.iterations = static_cast<unsigned>(std::stoul(value));
        else if (argument == "--cancel-after") options.cancel_after = static_cast<unsigned>(std::stoul(value));
        else if (argument == "--inject-eio-after") options.inject_eio_after = static_cast<unsigned>(std::stoul(value));
        else if (argument == "--inject-stale-after") options.inject_stale_after = static_cast<unsigned>(std::stoul(value));
        else if (argument == "--output") options.output = value;
        else throw std::runtime_error("unknown argument: " + argument);
    }
    const std::vector<std::string> apis = {
        "buffered-pread", "direct-pread", "buffered-io-uring", "direct-io-uring", "mmap-buffered",
    };
    if (options.plan.empty() || options.output.empty() || options.qd == 0 || options.qd > 32 || options.iterations == 0) {
        throw std::runtime_error("missing or invalid required option");
    }
    if (std::find(apis.begin(), apis.end(), options.api) == apis.end()) throw std::runtime_error("unsupported API");
    if (options.cache_state != "OS_COLD_VERIFIED" && options.cache_state != "OS_WARM") {
        throw std::runtime_error("cache state must be OS_COLD_VERIFIED or OS_WARM");
    }
    if (options.api == "mmap-buffered" && options.qd != 1) throw std::runtime_error("mmap comparator supports only QD=1");
    return options;
}

std::map<std::string, std::size_t> source_indices(const std::vector<Operation> & operations) {
    std::map<std::string, std::size_t> indices;
    for (const auto & operation : operations) {
        if (!indices.count(operation.path)) indices.emplace(operation.path, indices.size());
    }
    return indices;
}

std::string block_stat_path(const struct stat & attributes) {
    std::ostringstream path;
    path << "/sys/dev/block/" << major(attributes.st_dev) << ':' << minor(attributes.st_dev) << "/stat";
    return path.str();
}

std::vector<Source> open_sources(const std::vector<Operation> & operations, bool direct) {
    const auto indices = source_indices(operations);
    std::vector<Source> sources(indices.size());
    for (const auto & item : indices) {
        Source & source = sources[item.second];
        source.path = item.first;
        source.fd = open(source.path.c_str(), O_RDONLY | O_CLOEXEC | (direct ? O_DIRECT : 0));
        if (source.fd < 0) throw std::runtime_error("open failed for " + source.path + ": " + std::strerror(errno));
        struct stat attributes{};
        if (fstat(source.fd, &attributes) != 0 || attributes.st_size < 0) throw std::runtime_error("fstat failed");
        source.size = static_cast<std::uint64_t>(attributes.st_size);
        source.block_stat_path = block_stat_path(attributes);
    }
    for (const auto & operation : operations) {
        const Source & source = sources.at(indices.at(operation.path));
        if (operation.offset + operation.length > source.size) throw std::runtime_error("operation extends past EOF");
        if (direct && ((operation.offset | operation.length) & 4095U)) throw std::runtime_error("direct range is not 4 KiB aligned");
    }
    return sources;
}

void close_sources(std::vector<Source> & sources) {
    for (auto & source : sources) {
        if (source.fd >= 0) close(source.fd);
        source.fd = -1;
    }
}

std::vector<std::size_t> operation_sources(const std::vector<Operation> & operations) {
    const auto indices = source_indices(operations);
    std::vector<std::size_t> output;
    output.reserve(operations.size());
    for (const auto & operation : operations) output.push_back(indices.at(operation.path));
    return output;
}

BlockStat read_block_stat(const std::string & path) {
    std::ifstream stream(path);
    BlockStat result;
    std::uint64_t read_merges = 0, write_ios = 0, write_merges = 0, write_sectors = 0, write_ticks = 0;
    std::uint64_t in_flight = 0, discard_ios = 0, discard_merges = 0, discard_sectors = 0, discard_ticks = 0;
    stream >> result.read_ios >> read_merges >> result.read_sectors >> result.read_ticks_ms;
    stream >> write_ios >> write_merges >> write_sectors >> write_ticks >> in_flight;
    stream >> result.io_ticks_ms >> result.weighted_ticks_ms;
    stream >> discard_ios >> discard_merges >> discard_sectors >> discard_ticks;
    if (!stream) return {};
    return result;
}

std::map<std::string, BlockStat> snapshot_block_stats(const std::vector<Source> & sources) {
    std::map<std::string, BlockStat> output;
    for (const auto & source : sources) output[source.block_stat_path] = read_block_stat(source.block_stat_path);
    return output;
}

Residency sample_residency(
    const std::vector<Operation> & operations,
    const std::vector<Source> & sources,
    const std::vector<std::size_t> & op_sources,
    bool evict
) {
    Residency result;
    const long page_size_raw = sysconf(_SC_PAGESIZE);
    if (page_size_raw <= 0) throw std::runtime_error("cannot determine page size");
    const std::uint64_t page_size = static_cast<std::uint64_t>(page_size_raw);
    if (evict) {
        for (std::size_t index = 0; index < operations.size(); ++index) {
            const auto & operation = operations[index];
            const auto & source = sources[op_sources[index]];
            if (posix_fadvise(source.fd, static_cast<off_t>(operation.offset), static_cast<off_t>(operation.length), POSIX_FADV_DONTNEED) != 0) {
                ++result.fadvise_failures;
            }
        }
    }
    for (std::size_t index = 0; index < operations.size(); ++index) {
        const auto & operation = operations[index];
        const auto & source = sources[op_sources[index]];
        const std::uint64_t aligned = operation.offset & ~(page_size - 1);
        const std::uint64_t delta = operation.offset - aligned;
        const std::uint64_t mapped_length = delta + operation.length;
        void * address = mmap(nullptr, mapped_length, PROT_READ, MAP_SHARED, source.fd, static_cast<off_t>(aligned));
        if (address == MAP_FAILED) {
            result.sampled = false;
            continue;
        }
        const std::size_t pages = static_cast<std::size_t>((mapped_length + page_size - 1) / page_size);
        std::vector<unsigned char> vector(pages);
        if (mincore(address, mapped_length, vector.data()) != 0) {
            result.sampled = false;
        } else {
            result.pages += pages;
            result.resident_pages += static_cast<std::uint64_t>(std::count_if(vector.begin(), vector.end(), [](unsigned char value) { return value & 1U; }));
        }
        munmap(address, mapped_length);
    }
    return result;
}

void * aligned_buffer(std::size_t size) {
    void * output = nullptr;
    if (posix_memalign(&output, 4096, size) != 0 || !output) throw std::bad_alloc();
    return output;
}

std::array<unsigned char, 32> read_one_pread(
    int fd,
    const Operation & operation,
    void * buffer,
    Counters & counters
) {
    auto digest = new_digest();
    std::uint64_t cursor = 0;
    while (cursor < operation.length) {
        const std::size_t request = static_cast<std::size_t>(operation.length - cursor);
        ssize_t count;
        do {
            count = pread(fd, buffer, request, static_cast<off_t>(operation.offset + cursor));
            if (count < 0 && errno == EINTR) ++counters.retries;
        } while (count < 0 && errno == EINTR);
        if (count < 0) throw std::runtime_error("pread failed: " + std::string(std::strerror(errno)));
        if (count == 0) throw std::runtime_error("zero-progress pread before EOF");
        if (static_cast<std::size_t>(count) != request) ++counters.short_reads;
        digest_update(digest.get(), buffer, static_cast<std::size_t>(count));
        cursor += static_cast<std::uint64_t>(count);
    }
    counters.bytes += cursor;
    ++counters.operations;
    return digest_finish(std::move(digest));
}

void add_sink_record(EVP_MD_CTX * sink, std::uint64_t ordinal, const std::array<unsigned char, 32> & digest) {
    unsigned char little_endian[8];
    for (unsigned index = 0; index < 8; ++index) little_endian[index] = static_cast<unsigned char>((ordinal >> (index * 8)) & 0xffU);
    digest_update(sink, little_endian, sizeof(little_endian));
    digest_update(sink, digest.data(), digest.size());
}

RunResult run_pread(
    const Options & options,
    const std::vector<Operation> & operations,
    const std::vector<Source> & sources,
    const std::vector<std::size_t> & op_sources
) {
    RunResult result;
    const auto maximum_length = std::max_element(
        operations.begin(), operations.end(),
        [](const auto & left, const auto & right) { return left.length < right.length; }
    )->length;
    result.buffer_count = options.qd;
    result.buffer_bytes = maximum_length * options.qd;
    result.worker_count = options.qd;
    std::array<unsigned char, 32> final_sink{};
    for (unsigned iteration = 0; iteration < options.iterations; ++iteration) {
        const auto iteration_start = Clock::now();
        std::vector<std::array<unsigned char, 32>> digests(operations.size());
        std::vector<Interval> intervals(operations.size());
        std::atomic<std::size_t> next{0};
        std::atomic<unsigned> active{0};
        std::atomic<unsigned> max_active{0};
        std::mutex failure_mutex;
        std::exception_ptr failure;
        std::atomic<bool> stop{false};
        std::atomic<unsigned> accepted{0};
        std::atomic<bool> cancelled{false};
        std::mutex counters_mutex;
        std::vector<std::thread> workers;
        workers.reserve(options.qd);
        for (unsigned worker = 0; worker < options.qd; ++worker) {
            workers.emplace_back([&] {
                std::unique_ptr<void, decltype(&std::free)> buffer(aligned_buffer(static_cast<std::size_t>(maximum_length)), &std::free);
                Counters local;
                try {
                    while (!stop.load()) {
                        const std::size_t index = next.fetch_add(1);
                        if (index >= operations.size()) break;
                        const unsigned current = active.fetch_add(1) + 1;
                        unsigned observed = max_active.load();
                        while (current > observed && !max_active.compare_exchange_weak(observed, current)) {}
                        [[maybe_unused]] const std::uint64_t trace_id = llm_perfetto_trace_id(
                            llm_perfetto_trace_domain::storage,
                            static_cast<std::uint64_t>(iteration) * operations.size() + index + 1);
                        LLM_EXPERT_TRACE_ASYNC_BEGIN("k3.storage", "storage_operation", trace_id,
                            "request_id", uint64_t(1),
                            "operation_index", uint64_t(operations[index].ordinal),
                            "source", uint64_t(operations[index].source),
                            "offset", uint64_t(operations[index].offset),
                            "bytes", uint64_t(operations[index].length),
                            "direct", uint64_t(options.api == "direct-pread"));
                        intervals[index].start_ns = now_ns();
                        const auto observed_digest = read_one_pread(sources[op_sources[index]].fd, operations[index], buffer.get(), local);
                        intervals[index].end_ns = now_ns();
                        LLM_EXPERT_TRACE_ASYNC_END("k3.storage", trace_id,
                            "native_result", int64_t(0), "bytes", uint64_t(operations[index].length));
                        active.fetch_sub(1);
                        if (observed_digest != operations[index].expected) throw std::runtime_error("bundle checksum mismatch");
                        digests[operations[index].ordinal] = observed_digest;
                        const unsigned accepted_now = accepted.fetch_add(1) + 1;
                        if (options.cancel_after && accepted_now >= options.cancel_after) {
                            cancelled.store(true);
                            stop.store(true);
                        }
                    }
                } catch (...) {
                    std::lock_guard<std::mutex> lock(failure_mutex);
                    if (!failure) failure = std::current_exception();
                    stop.store(true);
                }
                std::lock_guard<std::mutex> lock(counters_mutex);
                result.counters.bytes += local.bytes;
                result.counters.operations += local.operations;
                result.counters.short_reads += local.short_reads;
                result.counters.retries += local.retries;
            });
        }
        for (auto & worker : workers) worker.join();
        if (failure) std::rethrow_exception(failure);
        if (cancelled.load()) throw Cancelled();
        auto sink = new_digest();
        for (std::size_t ordinal = 0; ordinal < digests.size(); ++ordinal) add_sink_record(sink.get(), ordinal, digests[ordinal]);
        final_sink = digest_finish(std::move(sink));
        result.intervals.insert(result.intervals.end(), intervals.begin(), intervals.end());
        result.interval_sources.insert(result.interval_sources.end(), op_sources.begin(), op_sources.end());
        result.max_active = std::max(result.max_active, max_active.load());
        result.iteration_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - iteration_start).count());
    }
    result.sink = final_sink;
    return result;
}

struct RingSlot {
    void * buffer = nullptr;
    std::size_t operation = 0;
    std::uint64_t cursor = 0;
    std::int64_t started_ns = 0;
    std::uint32_t generation = 0;
    bool active = false;
    bool hashing = false;
};

void prepare_ring_read(io_uring & ring, RingSlot & slot, const Operation & operation, int fd, std::size_t slot_index) {
    io_uring_sqe * sqe = io_uring_get_sqe(&ring);
    if (!sqe) throw std::runtime_error("io_uring SQ unexpectedly full");
    const auto remaining = operation.length - slot.cursor;
    const auto request = static_cast<unsigned>(std::min<std::uint64_t>(remaining, std::numeric_limits<unsigned>::max()));
    io_uring_prep_read(sqe, fd, static_cast<unsigned char *>(slot.buffer) + slot.cursor, request, static_cast<off_t>(operation.offset + slot.cursor));
    const std::uint64_t identity = (static_cast<std::uint64_t>(slot.generation) << 32) | slot_index;
    io_uring_sqe_set_data64(sqe, identity);
}

RunResult run_io_uring(
    const Options & options,
    const std::vector<Operation> & operations,
    const std::vector<Source> & sources,
    const std::vector<std::size_t> & op_sources
) {
    RunResult result;
    io_uring ring{};
    io_uring_params parameters{};
    const int setup = io_uring_queue_init_params(options.qd, &ring, &parameters);
    if (setup < 0) throw std::runtime_error("io_uring_setup failed: " + std::string(std::strerror(-setup)));
    result.ring_setup_flags = parameters.flags;
    result.ring_features = parameters.features;
    result.ring_sq_entries = parameters.sq_entries;
    result.ring_cq_entries = parameters.cq_entries;
    result.checksum_worker_count = options.qd;
    const auto maximum_length = std::max_element(operations.begin(), operations.end(), [](const auto & left, const auto & right) { return left.length < right.length; })->length;
    std::vector<RingSlot> slots(options.qd * 2);
    result.buffer_count = slots.size();
    result.buffer_bytes = maximum_length * slots.size();
    try {
        for (auto & slot : slots) slot.buffer = aligned_buffer(static_cast<std::size_t>(maximum_length));
        DigestPool digest_pool(options.qd);
        for (unsigned iteration = 0; iteration < options.iterations; ++iteration) {
            const auto iteration_start = Clock::now();
            std::vector<std::array<unsigned char, 32>> digests(operations.size());
            std::vector<Interval> intervals(operations.size());
            std::size_t next = 0, completed = 0, hashing = 0, completions = 0;
            unsigned active = 0;
            std::deque<std::size_t> free_slots;
            for (std::size_t slot = 0; slot < slots.size(); ++slot) free_slots.push_back(slot);
            auto assign = [&](std::size_t slot_index) {
                RingSlot & slot = slots[slot_index];
                if (next >= operations.size()) return false;
                if (slot.active || slot.hashing) throw std::runtime_error("io_uring slot reused while busy");
                slot.operation = next++;
                slot.cursor = 0;
                slot.started_ns = now_ns();
                if (++slot.generation == 0) throw std::runtime_error("io_uring slot generation wrapped");
                slot.active = true;
                prepare_ring_read(ring, slot, operations[slot.operation], sources[op_sources[slot.operation]].fd, slot_index);
                ++active;
                result.max_active = std::max(result.max_active, active);
                return true;
            };
            auto accept_digest = [&](const std::pair<std::size_t, std::array<unsigned char, 32>> & hash_result) {
                const auto & [slot_index, observed_digest] = hash_result;
                if (slot_index >= slots.size()) throw std::runtime_error("invalid digest slot");
                RingSlot & slot = slots[slot_index];
                if (!slot.hashing || slot.active) throw std::runtime_error("stale digest completion");
                const Operation & operation = operations[slot.operation];
                if (observed_digest != operation.expected) throw std::runtime_error("bundle checksum mismatch");
                digests[operation.ordinal] = observed_digest;
                result.counters.bytes += operation.length;
                ++result.counters.operations;
                ++completed;
                --hashing;
                slot.hashing = false;
                free_slots.push_back(slot_index);
            };
            while (completed < operations.size()) {
                std::pair<std::size_t, std::array<unsigned char, 32>> hash_result;
                while (digest_pool.try_result(hash_result)) accept_digest(hash_result);

                while (active < options.qd && next < operations.size() && !free_slots.empty()) {
                    const std::size_t slot_index = free_slots.front();
                    free_slots.pop_front();
                    assign(slot_index);
                }

                if (completed == operations.size()) break;
                if (active == 0) {
                    if (hashing == 0) throw std::runtime_error("io_uring pipeline made no progress");
                    accept_digest(digest_pool.wait_result());
                    if (options.cancel_after && completed >= options.cancel_after) throw Cancelled();
                    continue;
                }

                const int submitted = io_uring_submit_and_wait(&ring, 1);
                if (submitted < 0) throw std::runtime_error("io_uring_enter failed: " + std::string(std::strerror(-submitted)));
                io_uring_cqe * cqe = nullptr;
                while (io_uring_peek_cqe(&ring, &cqe) == 0) {
                    const std::uint64_t identity = io_uring_cqe_get_data64(cqe);
                    const std::size_t slot_index = static_cast<std::size_t>(identity & 0xffffffffU);
                    std::uint32_t generation = static_cast<std::uint32_t>(identity >> 32);
                    ++completions;
                    io_uring_cqe_seen(&ring, cqe);
                    if (options.inject_stale_after && completions >= options.inject_stale_after) ++generation;
                    if (slot_index >= slots.size() || !slots[slot_index].active || slots[slot_index].generation != generation) {
                        throw std::runtime_error("stale io_uring completion");
                    }
                    if (options.inject_eio_after && completions >= options.inject_eio_after) {
                        throw std::runtime_error("injected EIO completion");
                    }
                    RingSlot & slot = slots[slot_index];
                    const Operation & operation = operations[slot.operation];
                    const int count = cqe->res;
                    if (count < 0) throw std::runtime_error("io_uring read failed: " + std::string(std::strerror(-count)));
                    if (count == 0) throw std::runtime_error("zero-progress io_uring read before EOF");
                    const auto remaining = operation.length - slot.cursor;
                    if (static_cast<std::uint64_t>(count) != remaining) ++result.counters.short_reads;
                    slot.cursor += static_cast<std::uint64_t>(count);
                    if (slot.cursor < operation.length) {
                        prepare_ring_read(ring, slot, operation, sources[op_sources[slot.operation]].fd, slot_index);
                        continue;
                    }
                    intervals[slot.operation] = {slot.started_ns, now_ns()};
                    slot.active = false;
                    slot.hashing = true;
                    --active;
                    ++hashing;
                    digest_pool.submit({slot_index, slot.buffer, static_cast<std::size_t>(operation.length)});
                }
                if (options.cancel_after && completed >= options.cancel_after) throw Cancelled();
            }
            auto sink = new_digest();
            for (std::size_t ordinal = 0; ordinal < digests.size(); ++ordinal) add_sink_record(sink.get(), ordinal, digests[ordinal]);
            result.sink = digest_finish(std::move(sink));
            result.intervals.insert(result.intervals.end(), intervals.begin(), intervals.end());
            result.interval_sources.insert(result.interval_sources.end(), op_sources.begin(), op_sources.end());
            result.iteration_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - iteration_start).count());
        }
    } catch (...) {
        io_uring_queue_exit(&ring);
        for (auto & slot : slots) std::free(slot.buffer);
        throw;
    }
    io_uring_queue_exit(&ring);
    for (auto & slot : slots) std::free(slot.buffer);
    return result;
}

RunResult run_mmap(
    const Options & options,
    const std::vector<Operation> & operations,
    const std::vector<Source> & sources,
    const std::vector<std::size_t> & op_sources
) {
    RunResult result;
    result.worker_count = 1;
    std::vector<void *> mappings(sources.size(), MAP_FAILED);
    try {
        for (std::size_t index = 0; index < sources.size(); ++index) {
            mappings[index] = mmap(nullptr, sources[index].size, PROT_READ, MAP_SHARED, sources[index].fd, 0);
            if (mappings[index] == MAP_FAILED) throw std::runtime_error("mmap failed: " + std::string(std::strerror(errno)));
            madvise(mappings[index], sources[index].size, MADV_RANDOM);
        }
        for (unsigned iteration = 0; iteration < options.iterations; ++iteration) {
            const auto iteration_start = Clock::now();
            std::vector<std::array<unsigned char, 32>> digests(operations.size());
            for (const auto & operation : operations) {
                const std::size_t source_index = op_sources[&operation - operations.data()];
                const auto start = now_ns();
                auto digest = new_digest();
                digest_update(digest.get(), static_cast<unsigned char *>(mappings[source_index]) + operation.offset, static_cast<std::size_t>(operation.length));
                const auto observed = digest_finish(std::move(digest));
                const auto end = now_ns();
                if (observed != operation.expected) throw std::runtime_error("bundle checksum mismatch");
                result.intervals.push_back({start, end});
                result.interval_sources.push_back(source_index);
                digests[operation.ordinal] = observed;
                result.counters.bytes += operation.length;
                ++result.counters.operations;
                if (options.cancel_after && result.counters.operations >= options.cancel_after) throw Cancelled();
            }
            auto sink = new_digest();
            for (std::size_t ordinal = 0; ordinal < digests.size(); ++ordinal) add_sink_record(sink.get(), ordinal, digests[ordinal]);
            result.sink = digest_finish(std::move(sink));
            result.iteration_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - iteration_start).count());
        }
        result.max_active = 1;
    } catch (...) {
        for (std::size_t index = 0; index < mappings.size(); ++index) if (mappings[index] != MAP_FAILED) munmap(mappings[index], sources[index].size);
        throw;
    }
    for (std::size_t index = 0; index < mappings.size(); ++index) munmap(mappings[index], sources[index].size);
    return result;
}

std::map<unsigned, std::int64_t> effective_qd_histogram(const std::vector<Interval> & intervals) {
    std::vector<std::pair<std::int64_t, int>> events;
    events.reserve(intervals.size() * 2);
    for (const auto & interval : intervals) {
        events.emplace_back(interval.start_ns, 1);
        events.emplace_back(interval.end_ns, -1);
    }
    std::sort(events.begin(), events.end(), [](const auto & left, const auto & right) {
        return left.first != right.first ? left.first < right.first : left.second < right.second;
    });
    std::map<unsigned, std::int64_t> histogram;
    unsigned active = 0;
    std::int64_t previous = events.empty() ? 0 : events.front().first;
    for (const auto & event : events) {
        if (event.first > previous) histogram[active] += event.first - previous;
        if (event.second < 0) --active; else ++active;
        previous = event.first;
    }
    return histogram;
}

double percentile(std::vector<double> values, double fraction) {
    if (values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    const auto rank = static_cast<std::size_t>(std::ceil(fraction * values.size()));
    return values[std::min(values.size() - 1, std::max<std::size_t>(1, rank) - 1)];
}

std::uint64_t read_status_kib(const std::string & field) {
    std::ifstream stream("/proc/self/status");
    std::string key;
    while (stream >> key) {
        if (key == field + ":") {
            std::uint64_t value = 0;
            stream >> value;
            return value;
        }
        std::string rest;
        std::getline(stream, rest);
    }
    return 0;
}

std::uint64_t read_meminfo_kib(const std::string & field) {
    std::ifstream stream("/proc/meminfo");
    std::string key;
    while (stream >> key) {
        if (key == field + ":") {
            std::uint64_t value = 0;
            stream >> value;
            return value;
        }
        std::string rest;
        std::getline(stream, rest);
    }
    return 0;
}

void write_result(
    const Options & options,
    const std::vector<Operation> & operations,
    const std::vector<Source> & sources,
    const RunResult & run,
    const Residency & residency,
    const std::map<std::string, BlockStat> & before,
    const std::map<std::string, BlockStat> & after,
    const rusage & usage_before,
    const rusage & usage_after
) {
    const auto histogram = effective_qd_histogram(run.intervals);
    std::int64_t active_ns = 0, concurrent_ns = 0;
    for (const auto & item : histogram) {
        if (item.first > 0) active_ns += item.second;
        if (item.first >= 2) concurrent_ns += item.second;
    }
    const bool effective = run.max_active == options.qd;
    const double seconds = std::accumulate(run.iteration_ms.begin(), run.iteration_ms.end(), 0.0) / 1000.0;
    const double useful_gbps = seconds > 0 ? static_cast<double>(run.counters.bytes) / seconds / 1e9 : 0.0;
    std::vector<double> operation_ms;
    operation_ms.reserve(run.intervals.size());
    for (const auto & interval : run.intervals) operation_ms.push_back((interval.end_ns - interval.start_ns) / 1e6);
    const auto maximum_length = std::max_element(
        operations.begin(), operations.end(),
        [](const auto & left, const auto & right) { return left.length < right.length; }
    )->length;
    std::ofstream output(options.output);
    if (!output) throw std::runtime_error("cannot create output JSON");
    output << std::fixed << std::setprecision(6);
    output << "{\n";
    output << "  \"schema_version\": \"phase12-nvme-cell-v1\",\n";
    output << "  \"status\": \"PASS\",\n";
    output << "  \"api\": \"" << json_escape(options.api) << "\",\n";
    output << "  \"cache_state\": \"" << options.cache_state << "\",\n";
    const bool direct = options.api == "direct-pread" || options.api == "direct-io-uring";
    output << "  \"direct_io\": {\"requested\": " << (direct ? "true" : "false")
           << ", \"opened_with_o_direct\": " << (direct ? "true" : "false")
           << ", \"buffered_fallback_allowed\": false},\n";
    output << "  \"requested_qd\": " << options.qd << ",\n";
    output << "  \"effective_qd_status\": \"" << (effective ? "SUPPORTED" : "UNSUPPORTED_EFFECTIVE_QD") << "\",\n";
    output << "  \"effective_qd_basis\": \"maximum observed in-flight operations equals requested QD\",\n";
    output << "  \"maximum_active_operations\": " << run.max_active << ",\n";
    output << "  \"maximum_inflight_bytes\": " << maximum_length * run.max_active << ",\n";
    output << "  \"worker_count\": " << run.worker_count << ",\n";
    output << "  \"checksum_worker_count\": " << run.checksum_worker_count << ",\n";
    output << "  \"buffer_count\": " << run.buffer_count << ",\n";
    output << "  \"buffer_bytes\": " << run.buffer_bytes << ",\n";
    output << "  \"fraction_active_at_least_two\": " << (active_ns ? static_cast<double>(concurrent_ns) / active_ns : 0.0) << ",\n";
    output << "  \"operation_count_per_iteration\": " << operations.size() << ",\n";
    output << "  \"iterations\": " << options.iterations << ",\n";
    output << "  \"completed_operations\": " << run.counters.operations << ",\n";
    output << "  \"useful_bytes\": " << run.counters.bytes << ",\n";
    output << "  \"submitted_bytes\": " << run.counters.bytes << ",\n";
    output << "  \"short_reads\": " << run.counters.short_reads << ",\n";
    output << "  \"retries\": " << run.counters.retries << ",\n";
    output << "  \"useful_gbps\": " << useful_gbps << ",\n";
    output << "  \"token_equivalent_latency_ms\": [";
    for (std::size_t index = 0; index < run.iteration_ms.size(); ++index) output << (index ? "," : "") << run.iteration_ms[index];
    output << "],\n";
    output << "  \"latency_ms\": {\"p50\": " << percentile(run.iteration_ms, 0.50)
           << ", \"p95\": " << percentile(run.iteration_ms, 0.95)
           << ", \"p99\": " << percentile(run.iteration_ms, 0.99)
           << ", \"max\": " << *std::max_element(run.iteration_ms.begin(), run.iteration_ms.end()) << "},\n";
    output << "  \"operation_elapsed_ms\": {\"p50\": " << percentile(operation_ms, 0.50)
           << ", \"p95\": " << percentile(operation_ms, 0.95)
           << ", \"p99\": " << percentile(operation_ms, 0.99)
           << ", \"max\": " << *std::max_element(operation_ms.begin(), operation_ms.end()) << "},\n";
    output << "  \"checksum_sink_sha256\": \"" << hex(run.sink.data(), run.sink.size()) << "\",\n";
    output << "  \"page_cache_pre_read\": {\"sampled\": " << (residency.sampled ? "true" : "false")
           << ", \"pages\": " << residency.pages << ", \"resident_pages\": " << residency.resident_pages
           << ", \"resident_fraction\": " << (residency.pages ? static_cast<double>(residency.resident_pages) / residency.pages : 0.0)
           << ", \"fadvise_failures\": " << residency.fadvise_failures << "},\n";
    output << "  \"io_uring\": {\"setup_flags\": " << run.ring_setup_flags << ", \"features\": " << run.ring_features
           << ", \"sq_entries\": " << run.ring_sq_entries << ", \"cq_entries\": " << run.ring_cq_entries << "},\n";
    output << "  \"rusage\": {\"user_seconds\": "
           << (usage_after.ru_utime.tv_sec - usage_before.ru_utime.tv_sec) + (usage_after.ru_utime.tv_usec - usage_before.ru_utime.tv_usec) / 1e6
           << ", \"system_seconds\": "
           << (usage_after.ru_stime.tv_sec - usage_before.ru_stime.tv_sec) + (usage_after.ru_stime.tv_usec - usage_before.ru_stime.tv_usec) / 1e6
           << ", \"voluntary_context_switches\": " << usage_after.ru_nvcsw - usage_before.ru_nvcsw
           << ", \"involuntary_context_switches\": " << usage_after.ru_nivcsw - usage_before.ru_nivcsw
           << ", \"minor_faults\": " << usage_after.ru_minflt - usage_before.ru_minflt
           << ", \"major_faults\": " << usage_after.ru_majflt - usage_before.ru_majflt
           << ", \"max_rss_kib\": " << usage_after.ru_maxrss << "},\n";
    output << "  \"process_memory\": {\"vm_rss_kib\": " << read_status_kib("VmRSS") << ", \"vm_hwm_kib\": " << read_status_kib("VmHWM") << "},\n";
    output << "  \"swap_used_bytes\": " << (read_meminfo_kib("SwapTotal") - read_meminfo_kib("SwapFree")) * 1024 << ",\n";
    output << "  \"lifetime_resources\": {\"fd_delta\": " << run.fd_delta << ", \"thread_delta\": " << run.thread_delta << "},\n";
    output << "  \"block_devices\": [";
    bool first = true;
    for (const auto & item : after) {
        const auto prior = before.at(item.first);
        if (!first) output << ',';
        first = false;
        const auto read_operations = item.second.read_ios - prior.read_ios;
        const auto read_ticks_ms = item.second.read_ticks_ms - prior.read_ticks_ms;
        const auto io_ticks_ms = item.second.io_ticks_ms - prior.io_ticks_ms;
        const auto weighted_ticks_ms = item.second.weighted_ticks_ms - prior.weighted_ticks_ms;
        output << "{\"stat_path\":\"" << json_escape(item.first) << "\",\"read_operations\":" << item.second.read_ios - prior.read_ios
               << ",\"read_bytes\":" << (item.second.read_sectors - prior.read_sectors) * 512
               << ",\"read_ticks_ms\":" << read_ticks_ms
               << ",\"io_ticks_ms\":" << io_ticks_ms
               << ",\"weighted_ticks_ms\":" << weighted_ticks_ms
               << ",\"mean_read_service_ms\":" << (read_operations ? static_cast<double>(read_ticks_ms) / read_operations : 0.0)
               << ",\"mean_queue_depth_during_io\":" << (io_ticks_ms ? static_cast<double>(weighted_ticks_ms) / io_ticks_ms : 0.0) << '}';
    }
    output << "],\n";
    output << "  \"per_source_activity\": [";
    for (std::size_t source_index = 0; source_index < sources.size(); ++source_index) {
        std::vector<Interval> source_intervals;
        for (std::size_t interval_index = 0; interval_index < run.intervals.size(); ++interval_index) {
            if (run.interval_sources.at(interval_index) == source_index) {
                source_intervals.push_back(run.intervals[interval_index]);
            }
        }
        const auto source_histogram = effective_qd_histogram(source_intervals);
        std::int64_t source_active_ns = 0;
        std::int64_t source_depth_ns = 0;
        unsigned source_maximum_active = 0;
        std::vector<double> source_operation_ms;
        source_operation_ms.reserve(source_intervals.size());
        for (const auto & item : source_histogram) {
            if (item.first > 0) {
                source_active_ns += item.second;
                source_depth_ns += static_cast<std::int64_t>(item.first) * item.second;
                source_maximum_active = std::max(source_maximum_active, item.first);
            }
        }
        for (const auto & interval : source_intervals) {
            source_operation_ms.push_back((interval.end_ns - interval.start_ns) / 1e6);
        }
        if (source_index) output << ',';
        output << "{\"source\":" << source_index
               << ",\"path\":\"" << json_escape(sources[source_index].path)
               << "\",\"block_stat_path\":\"" << json_escape(sources[source_index].block_stat_path)
               << "\",\"operation_intervals\":" << source_intervals.size()
               << ",\"maximum_active_operations\":" << source_maximum_active
               << ",\"active_wall_ns\":" << source_active_ns
               << ",\"average_active_operations\":"
               << (source_active_ns ? static_cast<double>(source_depth_ns) / source_active_ns : 0.0)
               << ",\"operation_elapsed_ms\":{\"p50\":" << percentile(source_operation_ms, 0.50)
               << ",\"p95\":" << percentile(source_operation_ms, 0.95)
               << ",\"p99\":" << percentile(source_operation_ms, 0.99)
               << ",\"max\":" << (source_operation_ms.empty() ? 0.0 : *std::max_element(source_operation_ms.begin(), source_operation_ms.end()))
               << "},\"active_depth_histogram_ns\":{";
        bool first_depth = true;
        for (const auto & item : source_histogram) {
            if (!first_depth) output << ',';
            first_depth = false;
            output << '\"' << item.first << "\":" << item.second;
        }
        output << "}}";
    }
    output << "],\n";
    output << "  \"effective_qd_histogram_ns\": {";
    first = true;
    for (const auto & item : histogram) {
        if (!first) output << ',';
        first = false;
        output << '\"' << item.first << "\":" << item.second;
    }
    output << "}\n}\n";
}

int count_directory_entries(const char * path) {
    DIR * directory = opendir(path);
    if (!directory) throw std::runtime_error("cannot inspect process resources");
    int count = 0;
    while (readdir(directory)) ++count;
    closedir(directory);
    return count;
}

} // namespace

int main(int argc, char ** argv) {
    try {
        const Options options = parse_options(argc, argv);
        const bool trace_active = phase12_nvme_trace_initialize();
        const auto operations = load_plan(options.plan);
        const bool direct = options.api == "direct-pread" || options.api == "direct-io-uring";
        auto sources = open_sources(operations, direct);
        const auto op_sources = operation_sources(operations);
        Residency residency;
        if (!direct) residency = sample_residency(operations, sources, op_sources, options.cache_state == "OS_COLD_VERIFIED");
        else residency.sampled = false;
        const auto before = snapshot_block_stats(sources);
        const int fds_before = count_directory_entries("/proc/self/fd");
        const int threads_before = count_directory_entries("/proc/self/task");
        rusage usage_before{};
        getrusage(RUSAGE_SELF, &usage_before);
        RunResult run;
        [[maybe_unused]] const std::uint64_t request_trace_id = llm_perfetto_trace_id(llm_perfetto_trace_domain::request, 1);
        [[maybe_unused]] const std::uint64_t provider_trace_id = llm_perfetto_trace_id(llm_perfetto_trace_domain::flight, 1);
        LLM_EXPERT_TRACE_ASYNC_BEGIN("k3.request", "request", request_trace_id,
            "request_id", uint64_t(1), "token_index", uint64_t(0), "token_count", uint64_t(options.iterations));
        LLM_EXPERT_TRACE_ASYNC_BEGIN("k3.provider", "provider_request", provider_trace_id,
            "request_id", uint64_t(1), "selected_count", uint64_t(operations.size()));
        LLM_EXPERT_TRACE_COUNTER("k3.resource", "storage_requested_qd", request_trace_id, options.qd);
        if (options.api == "buffered-pread" || options.api == "direct-pread") run = run_pread(options, operations, sources, op_sources);
        else if (options.api == "buffered-io-uring" || options.api == "direct-io-uring") run = run_io_uring(options, operations, sources, op_sources);
        else run = run_mmap(options, operations, sources, op_sources);
        LLM_EXPERT_TRACE_COUNTER("k3.resource", "storage_max_active", request_trace_id, run.max_active);
        LLM_EXPERT_TRACE_ASYNC_END("k3.provider", provider_trace_id,
            "terminal_state", uint64_t(1), "completed_operations", uint64_t(run.counters.operations));
        LLM_EXPERT_TRACE_ASYNC_END("k3.request", request_trace_id,
            "terminal_state", uint64_t(1), "useful_bytes", uint64_t(run.counters.bytes));
        run.fd_delta = count_directory_entries("/proc/self/fd") - fds_before;
        run.thread_delta = count_directory_entries("/proc/self/task") - threads_before;
        rusage usage_after{};
        getrusage(RUSAGE_SELF, &usage_after);
        const auto after = snapshot_block_stats(sources);
        write_result(options, operations, sources, run, residency, before, after, usage_before, usage_after);
        close_sources(sources);
        if (trace_active) phase12_nvme_trace_finish();
        return 0;
    } catch (const Cancelled & error) {
        std::cerr << "phase12_nvme_bench: " << error.what() << '\n';
        return 2;
    } catch (const std::exception & error) {
        std::cerr << "phase12_nvme_bench: " << error.what() << '\n';
        return 1;
    }
}

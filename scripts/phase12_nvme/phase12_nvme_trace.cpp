#include "phase12_nvme_trace.h"

#if defined(PHASE12_NVME_PERFETTO)

#include <cerrno>
#include <chrono>
#include <climits>
#include <cstdlib>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>

PERFETTO_TRACK_EVENT_STATIC_STORAGE();

namespace {

bool initialized = false;

int stop_descriptor() {
    const char * value = std::getenv("LLAMA_PERFETTO_STOP_FD");
    if (value == nullptr) throw std::runtime_error("LLAMA_PERFETTO_STOP_FD is required for traced capture");
    char * end = nullptr;
    errno = 0;
    const long descriptor = std::strtol(value, &end, 10);
    if (errno != 0 || end == value || *end != '\0' || descriptor < 0 || descriptor > INT_MAX) {
        throw std::runtime_error("invalid LLAMA_PERFETTO_STOP_FD");
    }
    return static_cast<int>(descriptor);
}

void wait_for_track_event(bool enabled, std::chrono::seconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (perfetto::TrackEvent::IsEnabled() != enabled) {
        if (std::chrono::steady_clock::now() >= deadline) {
            throw std::runtime_error(enabled ? "Perfetto TrackEvent activation timed out" : "Perfetto TrackEvent stop timed out");
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
}

} // namespace

bool phase12_nvme_trace_initialize() {
    const char * capture = std::getenv("LLAMA_PERFETTO_CAPTURE");
    if (capture == nullptr || std::string(capture) != "1") return false;
    if (initialized) throw std::runtime_error("Perfetto trace initialized more than once");
    (void) stop_descriptor();
    perfetto::TracingInitArgs args;
    args.backends = perfetto::kSystemBackend;
    args.enable_system_consumer = false;
    args.supports_multiple_data_source_instances = false;
    args.use_monotonic_raw_clock = true;
    args.shmem_size_hint_kb = 32768;
    perfetto::Tracing::Initialize(args);
    perfetto::TrackEvent::Register();
    wait_for_track_event(true, std::chrono::seconds(10));
    // TrackEvent activation can precede traced_probes/ftrace readiness. Keep this
    // outside the measured request so all configured sources share its boundary.
    std::this_thread::sleep_for(std::chrono::seconds(1));
    initialized = true;
    LLM_EXPERT_TRACE_INSTANT("k3.lifecycle", "trace_session_start",
        "capture_mode", uint64_t(1), "producer_shmem_kib", uint64_t(32768));
    return true;
}

void phase12_nvme_trace_finish() {
    if (!initialized) return;
    LLM_EXPERT_TRACE_INSTANT("k3.lifecycle", "trace_session_stop",
        "cupti_required", uint64_t(0), "trace_errors", uint64_t(0), "trace_drops", uint64_t(0));
    perfetto::TrackEvent::Flush();
    std::this_thread::sleep_for(std::chrono::seconds(2));
    const uint8_t marker = 1;
    ssize_t written;
    do {
        written = ::write(stop_descriptor(), &marker, sizeof(marker));
    } while (written < 0 && errno == EINTR);
    if (written != 1) throw std::runtime_error("Perfetto stop descriptor write failed");
    wait_for_track_event(false, std::chrono::seconds(60));
    perfetto::Tracing::Shutdown();
    initialized = false;
}

#endif

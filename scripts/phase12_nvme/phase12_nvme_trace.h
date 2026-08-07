#pragma once

#include "llama-perfetto-trace.h"

#if defined(PHASE12_NVME_PERFETTO)

bool phase12_nvme_trace_initialize();
void phase12_nvme_trace_finish();

#else

inline bool phase12_nvme_trace_initialize() {
    return false;
}

inline void phase12_nvme_trace_finish() {
}

#endif

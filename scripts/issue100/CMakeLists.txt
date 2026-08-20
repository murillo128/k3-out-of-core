cmake_minimum_required(VERSION 3.20)
project(issue100_gpqa_tools LANGUAGES CXX)

set(ISSUE100_LLAMA_SOURCE "/workspace/k3-out-of-core/llama.cpp" CACHE PATH "Pinned llama.cpp source")
set(ISSUE100_FROZEN_BUILD "/mnt/nvme1/issue77/build/cpu" CACHE PATH "Pinned CPU runtime build")

function(import_shared name filename)
  add_library(${name} SHARED IMPORTED GLOBAL)
  set_target_properties(${name} PROPERTIES
    IMPORTED_LOCATION "${ISSUE100_FROZEN_BUILD}/bin/${filename}")
endfunction()

import_shared(issue100_llama_common "libllama-common.so.0.0.10345")
import_shared(issue100_llama "libllama.so.0.0.10345")
import_shared(issue100_ggml "libggml.so.0.17.0")
import_shared(issue100_ggml_cpu "libggml-cpu.so.0.17.0")
import_shared(issue100_ggml_base "libggml-base.so.0.17.0")
add_library(issue100_llama_common_base STATIC IMPORTED GLOBAL)
set_target_properties(issue100_llama_common_base PROPERTIES
  IMPORTED_LOCATION "${ISSUE100_FROZEN_BUILD}/common/libllama-common-base.a")

add_executable(issue100-gpqa-probe gpqa_probe.cpp)
target_compile_features(issue100-gpqa-probe PRIVATE cxx_std_17)
target_compile_definitions(issue100-gpqa-probe PRIVATE
  GGML_BACKEND_SHARED GGML_SHARED GGML_USE_CPU LLAMA_SHARED LLAMA_SUBPROCESS)
target_compile_options(issue100-gpqa-probe PRIVATE
  -O3 -DNDEBUG -march=native -Wmissing-declarations -Wmissing-noreturn
  -Wall -Wextra -Wpedantic -Wcast-qual -Wno-unused-function
  -Wno-array-bounds -Wextra-semi)
target_include_directories(issue100-gpqa-probe PRIVATE
  "${ISSUE100_LLAMA_SOURCE}/src"
  "${ISSUE100_LLAMA_SOURCE}/vendor"
  "${ISSUE100_LLAMA_SOURCE}/include"
  "${ISSUE100_LLAMA_SOURCE}/ggml/include"
  "${ISSUE100_LLAMA_SOURCE}/common")
target_link_libraries(issue100-gpqa-probe PRIVATE
  issue100_llama_common issue100_llama issue100_ggml issue100_ggml_cpu
  issue100_ggml_base issue100_llama_common_base)
set_target_properties(issue100-gpqa-probe PROPERTIES
  BUILD_RPATH "${ISSUE100_FROZEN_BUILD}/bin"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin")

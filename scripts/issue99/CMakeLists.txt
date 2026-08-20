cmake_minimum_required(VERSION 3.20)
project(issue99_long_horizon_tools LANGUAGES CXX)

set(ISSUE99_LLAMA_SOURCE "/workspace/k3-out-of-core/llama.cpp" CACHE PATH "Pinned llama.cpp source")
set(ISSUE99_FROZEN_BUILD "/mnt/nvme1/issue77/build/cpu" CACHE PATH "Pinned CPU runtime build")

function(import_shared name filename)
  add_library(${name} SHARED IMPORTED GLOBAL)
  set_target_properties(${name} PROPERTIES IMPORTED_LOCATION "${ISSUE99_FROZEN_BUILD}/bin/${filename}")
endfunction()

import_shared(issue99_llama_common "libllama-common.so.0.0.10345")
import_shared(issue99_llama "libllama.so.0.0.10345")
import_shared(issue99_ggml "libggml.so.0.17.0")
import_shared(issue99_ggml_cpu "libggml-cpu.so.0.17.0")
import_shared(issue99_ggml_base "libggml-base.so.0.17.0")
add_library(issue99_llama_common_base STATIC IMPORTED GLOBAL)
set_target_properties(issue99_llama_common_base PROPERTIES
  IMPORTED_LOCATION "${ISSUE99_FROZEN_BUILD}/common/libllama-common-base.a")

add_executable(issue99-quality-probe quality_probe.cpp)
target_compile_features(issue99-quality-probe PRIVATE cxx_std_17)
target_compile_definitions(issue99-quality-probe PRIVATE
  GGML_BACKEND_SHARED GGML_SHARED GGML_USE_CPU LLAMA_SHARED LLAMA_SUBPROCESS)
target_compile_options(issue99-quality-probe PRIVATE
  -O3 -DNDEBUG -march=native -Wmissing-declarations -Wmissing-noreturn
  -Wall -Wextra -Wpedantic -Wcast-qual -Wno-unused-function -Wno-array-bounds -Wextra-semi)
target_include_directories(issue99-quality-probe PRIVATE
  "${ISSUE99_LLAMA_SOURCE}/src"
  "${ISSUE99_LLAMA_SOURCE}/vendor"
  "${ISSUE99_LLAMA_SOURCE}/include"
  "${ISSUE99_LLAMA_SOURCE}/ggml/include"
  "${ISSUE99_LLAMA_SOURCE}/common")
target_link_libraries(issue99-quality-probe PRIVATE
  issue99_llama_common issue99_llama issue99_ggml issue99_ggml_cpu
  issue99_ggml_base issue99_llama_common_base)
set_target_properties(issue99-quality-probe PROPERTIES
  BUILD_RPATH "${ISSUE99_FROZEN_BUILD}/bin"
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/bin")

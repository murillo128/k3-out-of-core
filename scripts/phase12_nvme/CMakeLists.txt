cmake_minimum_required(VERSION 3.16)
project(phase12_nvme_tools LANGUAGES CXX)

find_package(OpenSSL REQUIRED)
find_package(PkgConfig REQUIRED)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
pkg_check_modules(LIBURING REQUIRED IMPORTED_TARGET liburing)

add_executable(phase12_nvme_bench phase12_nvme_bench.cpp)
target_include_directories(phase12_nvme_bench PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/../../llama.cpp/src)
target_compile_features(phase12_nvme_bench PRIVATE cxx_std_17)
target_compile_options(phase12_nvme_bench PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(phase12_nvme_bench PRIVATE OpenSSL::Crypto PkgConfig::LIBURING pthread)

add_executable(
    phase12_nvme_bench_perfetto
    phase12_nvme_bench.cpp
    phase12_nvme_trace.cpp
    ../../llama.cpp/vendor/perfetto-v50.1/sdk/perfetto.cc
)
target_include_directories(
    phase12_nvme_bench_perfetto PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../llama.cpp/src
    ${CMAKE_CURRENT_SOURCE_DIR}/../../llama.cpp/vendor/perfetto-v50.1/sdk
)
target_compile_definitions(phase12_nvme_bench_perfetto PRIVATE LLAMA_PERFETTO=1 PHASE12_NVME_PERFETTO=1)
target_compile_features(phase12_nvme_bench_perfetto PRIVATE cxx_std_17)
target_compile_options(phase12_nvme_bench_perfetto PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(phase12_nvme_bench_perfetto PRIVATE OpenSSL::Crypto PkgConfig::LIBURING pthread ${CMAKE_DL_LIBS})

enable_testing()
add_test(NAME phase12_nvme_bench_help COMMAND phase12_nvme_bench --help)
add_test(
    NAME phase12_nvme_native_fixture
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/native_fixture_test.py
            --binary $<TARGET_FILE:phase12_nvme_bench>
)

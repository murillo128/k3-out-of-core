#include "route_trace.h"

#include <cstdint>
#include <iostream>
#include <string>
#include <vector>

namespace {

route_trace_metadata metadata() {
    return {
        /*.model_name              =*/ "fixture.gguf",
        /*.model_size              =*/ 784318432,
        /*.model_sha256            =*/ "411c197b503e6fb9199a2b22115e32dc4e2cad803fb112b24967737b3bab26c7",
        /*.model_source_revision   =*/ "source-revision",
        /*.published_gguf_revision =*/ "published-revision",
        /*.llama_cpp_revision      =*/ "llama-revision",
        /*.run_id                  =*/ "writer-test",
        /*.expert_count            =*/ 8,
        /*.top_k                   =*/ 2,
        /*.routed_layer_count      =*/ 7,
        /*.max_ubatch_payload      =*/ 4096,
    };
}

} // namespace

int main(int argc, char ** argv) {
    if (argc != 4) {
        std::cerr << "usage: test-route-trace-writer VALID INVALID OVERSIZED\n";
        return 2;
    }

    llama_pos positions[] = { 7, 8 };
    int32_t n_seq_ids[] = { 1, 2 };
    llama_seq_id row0[] = { 3 };
    llama_seq_id row1[] = { 3, 4 };
    const llama_seq_id * seq_ids[] = { row0, row1 };
    int32_t selected_experts[] = { 0, 2, 7, 1 };
    float weights[] = { 0.75f, 0.25f, 0.6f, 0.4f };
    llama_route_observation observation = {
        /*.request_ordinal  =*/ 5,
        /*.ubatch_ordinal   =*/ 2,
        /*.phase            =*/ LLAMA_ROUTE_PHASE_DECODE,
        /*.layer            =*/ 4,
        /*.n_tokens         =*/ 2,
        /*.n_expert_used    =*/ 2,
        /*.n_pos            =*/ 1,
        /*.positions        =*/ positions,
        /*.n_seq_ids        =*/ n_seq_ids,
        /*.seq_ids          =*/ seq_ids,
        /*.selected_experts =*/ selected_experts,
        /*.weights          =*/ weights,
    };

    route_trace_writer valid(argv[1], metadata());
    if (!valid.good() || !valid.write(observation) || valid.record_count() != 2 ||
        !valid.flush() || valid.flush_count() != 1 || !valid.finalize()) {
        std::cerr << "writer valid-path failure\n";
        return 3;
    }

    selected_experts[0] = 8;
    route_trace_writer invalid(argv[2], metadata());
    if (invalid.write(observation) || invalid.finalize()) {
        std::cerr << "writer accepted an invalid expert\n";
        return 4;
    }
    selected_experts[0] = 0;

    std::vector<llama_seq_id> many_seq_ids(1024, 0);
    const llama_seq_id * oversized_rows[] = { many_seq_ids.data() };
    int32_t oversized_count[] = { (int32_t) many_seq_ids.size() };
    observation.n_tokens = 1;
    observation.n_seq_ids = oversized_count;
    observation.seq_ids = oversized_rows;
    route_trace_writer oversized(argv[3], metadata());
    if (oversized.write(observation) || oversized.finalize()) {
        std::cerr << "writer exceeded its configured bound\n";
        return 5;
    }

    std::cout << "RESULT\texit=0\n";
    return 0;
}

#include <nlohmann/json.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <list>
#include <map>
#include <numeric>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

using json = nlohmann::ordered_json;
namespace fs = std::filesystem;

namespace {

constexpr uint32_t routed_layers = 92;
constexpr uint32_t experts_per_layer = 896;
constexpr uint32_t selected_count = 16;
constexpr uint32_t candidate_count = 32;
constexpr uint32_t decode_forwards = 64;
constexpr uint64_t expert_bytes = 17547264;
constexpr uint64_t gib = uint64_t(1) << 30;
constexpr uint32_t physical_slots = 7849;
constexpr float s2_max_regret = 0.007303759455680847f;
constexpr uint32_t s2_max_swaps = 2;
constexpr std::array<double, 5> thresholds = { 0.80, 0.90, 0.95, 0.96, 0.98 };

struct arguments {
    fs::path progress;
    std::string progress_sha256;
    fs::path selection;
    std::string selection_sha256;
    fs::path stage_a;
    std::string stage_a_sha256;
    fs::path committee;
    std::string committee_sha256;
    fs::path output_root;
};

struct record {
    bool decode = false;
    uint16_t layer = 0;
    std::array<uint16_t, candidate_count> candidates {};
    std::array<float, candidate_count> scores {};
};

struct phase_stats {
    uint64_t requests = 0;
    uint64_t hits = 0;
    uint64_t misses = 0;
    uint64_t evictions = 0;
    uint64_t occupancy_end = 0;
};

struct replay_stats {
    phase_stats prefill;
    phase_stats decode;
};

struct s2_stats : replay_stats {
    uint64_t decisions = 0;
    uint64_t changed_decisions = 0;
    uint64_t swaps = 0;
    double cumulative_regret = 0.0;
    double maximum_regret = 0.0;
    uint64_t eligible_resident_alternatives = 0;
    uint64_t decisions_with_eligible_alternative = 0;
};

struct capture {
    uint32_t ordinal = 0;
    std::string case_id;
    std::string family;
    std::string role;
    uint32_t length_level = 0;
    uint32_t prompt_tokens = 0;
    fs::path result_path;
    std::string result_sha256;
    uint64_t result_bytes = 0;
    std::vector<record> records;
    std::vector<uint32_t> exact_sequence;
    std::vector<int32_t> stack_distances;
    size_t decode_sequence_offset = 0;
    phase_stats measured_decode;
};

struct capacity {
    std::string label;
    uint32_t slots = 0;
    bool physical = false;
    bool full = false;
};

[[noreturn]] void fail(const std::string & message) {
    throw std::runtime_error(message);
}

json load_json(const fs::path & path) {
    std::ifstream input(path);
    if (!input) fail("unable to open JSON: " + path.string());
    json value;
    input >> value;
    return value;
}

void write_json(const fs::path & path, const json & value) {
    fs::create_directories(path.parent_path());
    const fs::path temporary = path.string() + ".tmp";
    {
        std::ofstream output(temporary);
        if (!output) fail("unable to write JSON: " + temporary.string());
        output << value.dump(2) << '\n';
    }
    fs::rename(temporary, path);
}

arguments parse_arguments(int argc, char ** argv) {
    arguments result;
    for (int index = 1; index < argc; ++index) {
        const std::string option = argv[index];
        if (index + 1 >= argc) fail("missing value for " + option);
        const std::string value = argv[++index];
        if (option == "--progress") result.progress = value;
        else if (option == "--progress-sha256") result.progress_sha256 = value;
        else if (option == "--selection") result.selection = value;
        else if (option == "--selection-sha256") result.selection_sha256 = value;
        else if (option == "--stage-a") result.stage_a = value;
        else if (option == "--stage-a-sha256") result.stage_a_sha256 = value;
        else if (option == "--committee") result.committee = value;
        else if (option == "--committee-sha256") result.committee_sha256 = value;
        else if (option == "--output-root") result.output_root = value;
        else fail("unknown option: " + option);
    }
    if (result.progress.empty() || result.progress_sha256.empty() || result.selection.empty() ||
        result.selection_sha256.empty() || result.stage_a.empty() || result.stage_a_sha256.empty() ||
        result.committee.empty() || result.committee_sha256.empty() || result.output_root.empty()) {
        fail("all observer replay arguments are required");
    }
    result.progress = fs::canonical(result.progress);
    result.selection = fs::canonical(result.selection);
    result.stage_a = fs::canonical(result.stage_a);
    result.committee = fs::canonical(result.committee);
    result.output_root = fs::absolute(result.output_root);
    return result;
}

json file_reference(const fs::path & path, const std::string & sha256) {
    return {
        {"path", fs::canonical(path).string()},
        {"bytes", fs::file_size(path)},
        {"sha256", sha256},
    };
}

uint32_t expert_key(uint32_t layer_zero_based, uint32_t expert) {
    return layer_zero_based*experts_per_layer + expert;
}

class fenwick_tree {
public:
    explicit fenwick_tree(size_t size) : values(size + 1, 0) {}

    void add(size_t position_zero_based, int delta) {
        for (size_t index = position_zero_based + 1; index < values.size(); index += index & (~index + 1)) {
            values[index] += delta;
        }
    }

    int sum(size_t count) const {
        int result = 0;
        for (size_t index = count; index != 0; index &= index - 1) result += values[index];
        return result;
    }

private:
    std::vector<int> values;
};

class lru_cache {
public:
    explicit lru_cache(uint32_t capacity) : capacity_value(capacity), links(routed_layers*experts_per_layer) {}

    bool contains(uint32_t key) const { return links.at(key).present; }

    bool access(uint32_t key, uint64_t & evictions) {
        auto & node = links.at(key);
        if (node.present) {
            detach(int32_t(key));
            attach_front(int32_t(key));
            return true;
        }
        if (capacity_value == 0) return false;
        if (size_value == capacity_value) {
            const int32_t victim = tail;
            detach(victim);
            links.at(size_t(victim)).present = false;
            --size_value;
            ++evictions;
        }
        node.present = true;
        attach_front(int32_t(key));
        ++size_value;
        return false;
    }

    uint32_t size() const { return size_value; }

private:
    struct node {
        int32_t previous = -1;
        int32_t next = -1;
        bool present = false;
    };

    void detach(int32_t key) {
        auto & current = links.at(size_t(key));
        if (current.previous >= 0) links.at(size_t(current.previous)).next = current.next;
        else head = current.next;
        if (current.next >= 0) links.at(size_t(current.next)).previous = current.previous;
        else tail = current.previous;
        current.previous = -1;
        current.next = -1;
    }

    void attach_front(int32_t key) {
        auto & current = links.at(size_t(key));
        current.previous = -1;
        current.next = head;
        if (head >= 0) links.at(size_t(head)).previous = key;
        else tail = key;
        head = key;
    }

    uint32_t capacity_value = 0;
    uint32_t size_value = 0;
    int32_t head = -1;
    int32_t tail = -1;
    std::vector<node> links;
};

class pinned_cache {
public:
    pinned_cache(uint32_t capacity, std::vector<bool> core) :
        core_membership(std::move(core)), core_loaded(core_membership.size(), false),
        core_slots(uint32_t(std::count(core_membership.begin(), core_membership.end(), true))),
        peripheral(capacity >= core_slots ? capacity - core_slots : 0), feasible(capacity >= core_slots) {}

    bool is_feasible() const { return feasible; }
    uint32_t core_slot_count() const { return core_slots; }

    bool access(uint32_t key, uint64_t & evictions) {
        if (core_membership.at(key)) {
            const bool hit = core_loaded.at(key);
            core_loaded.at(key) = true;
            return hit;
        }
        return peripheral.access(key, evictions);
    }

    bool is_core(uint32_t key) const { return core_membership.at(key); }

private:
    std::vector<bool> core_membership;
    std::vector<bool> core_loaded;
    uint32_t core_slots = 0;
    lru_cache peripheral;
    bool feasible = false;
};

json distribution(std::vector<double> values) {
    if (values.empty()) return {{"count", 0}};
    std::sort(values.begin(), values.end());
    const auto quantile = [&](double probability) {
        const double position = probability*double(values.size() - 1);
        const size_t low = size_t(std::floor(position));
        const size_t high = size_t(std::ceil(position));
        const double fraction = position - double(low);
        return values[low]*(1.0 - fraction) + values[high]*fraction;
    };
    const double mean = std::accumulate(values.begin(), values.end(), 0.0)/double(values.size());
    return {
        {"count", values.size()}, {"min", values.front()}, {"p10", quantile(0.10)},
        {"median", quantile(0.50)}, {"mean", mean}, {"p90", quantile(0.90)},
        {"max", values.back()},
    };
}

json phase_json(const phase_stats & stats, uint32_t token_count = decode_forwards) {
    return {
        {"requests", stats.requests}, {"hits", stats.hits}, {"misses", stats.misses},
        {"hit_ratio", stats.requests ? double(stats.hits)/double(stats.requests) : 0.0},
        {"backing_loads", stats.misses},
        {"backing_bytes", stats.misses*expert_bytes},
        {"loads_per_token", double(stats.misses)/double(token_count)},
        {"bytes_per_token", double(stats.misses*expert_bytes)/double(token_count)},
        {"evictions", stats.evictions}, {"occupancy_end", stats.occupancy_end},
    };
}

std::vector<capacity> capacities() {
    const std::array<uint32_t, 16> anchors = {
        64, 96, 128, 160, 192, 256, 320, 384, 448, 512, 640, 768, 896, 1024, 1152, 1280,
    };
    std::vector<capacity> result;
    for (uint32_t value : anchors) {
        result.push_back({std::to_string(value) + "_GiB", uint32_t((uint64_t(value)*gib)/expert_bytes), false, false});
    }
    result.push_back({"PHYSICAL_7849_SLOTS", physical_slots, true, false});
    result.push_back({"FULL_ROUTED_EXPERT_RESIDENCY", routed_layers*experts_per_layer, false, true});
    std::sort(result.begin(), result.end(), [](const capacity & lhs, const capacity & rhs) {
        return std::tie(lhs.slots, lhs.label) < std::tie(rhs.slots, rhs.label);
    });
    result.erase(std::unique(result.begin(), result.end(), [](const capacity & lhs, const capacity & rhs) {
        return lhs.slots == rhs.slots;
    }), result.end());
    return result;
}

json capacity_json(const capacity & value) {
    const uint64_t bytes = uint64_t(value.slots)*expert_bytes;
    return {
        {"label", value.label}, {"slots", value.slots}, {"actual_bytes", bytes},
        {"actual_gib", double(bytes)/double(gib)}, {"physical_anchor", value.physical},
        {"full_routed_expert_residency", value.full},
    };
}

std::vector<int32_t> stack_distances(const std::vector<uint32_t> & sequence) {
    fenwick_tree tree(sequence.size());
    std::vector<int64_t> last(routed_layers*experts_per_layer, -1);
    std::vector<int32_t> result(sequence.size(), -1);
    for (size_t index = 0; index < sequence.size(); ++index) {
        const uint32_t key = sequence[index];
        const int64_t prior = last.at(key);
        if (prior >= 0) {
            result[index] = tree.sum(index) - tree.sum(size_t(prior) + 1);
            tree.add(size_t(prior), -1);
        }
        tree.add(index, 1);
        last[key] = int64_t(index);
    }
    return result;
}

replay_stats exact_replay(const capture & value, uint32_t capacity_slots) {
    replay_stats result;
    uint64_t occupancy = 0;
    for (size_t index = 0; index < value.stack_distances.size(); ++index) {
        phase_stats & phase = index < value.decode_sequence_offset ? result.prefill : result.decode;
        ++phase.requests;
        const int32_t distance = value.stack_distances[index];
        if (distance >= 0 && uint32_t(distance) < capacity_slots) {
            ++phase.hits;
        } else {
            ++phase.misses;
            if (occupancy < capacity_slots) ++occupancy;
            else ++phase.evictions;
        }
        phase.occupancy_end = occupancy;
    }
    return result;
}

struct selected_route {
    std::array<uint16_t, selected_count> experts {};
    uint32_t swaps = 0;
    double regret = 0.0;
    double max_regret = 0.0;
    uint32_t eligible_resident_alternatives = 0;
};

selected_route select_s2(const record & value, const lru_cache & cache) {
    struct option {
        uint8_t improvement = 0;
        float regret = 0.0f;
        uint8_t candidate_rank = 0;
        uint8_t selected_rank = 0;
        uint16_t candidate_expert = 0;
        uint16_t selected_expert = 0;
    };
    selected_route result;
    std::copy(value.candidates.begin(), value.candidates.begin() + selected_count, result.experts.begin());
    std::vector<option> options;
    std::array<bool, candidate_count> eligible_candidate {};
    for (uint32_t selected_rank = 0; selected_rank < selected_count; ++selected_rank) {
        const uint32_t selected_key = expert_key(value.layer, value.candidates[selected_rank]);
        const uint8_t selected_cost = cache.contains(selected_key) ? 1 : 2;
        for (uint32_t candidate_rank = selected_count; candidate_rank < candidate_count; ++candidate_rank) {
            const uint32_t candidate_key = expert_key(value.layer, value.candidates[candidate_rank]);
            const uint8_t candidate_cost = cache.contains(candidate_key) ? 1 : 2;
            const float regret = value.scores[selected_rank] - value.scores[candidate_rank];
            if (candidate_cost >= selected_cost || regret < 0.0f || regret > s2_max_regret) continue;
            eligible_candidate[candidate_rank] = true;
            options.push_back({uint8_t(selected_cost - candidate_cost), regret,
                uint8_t(candidate_rank), uint8_t(selected_rank), value.candidates[candidate_rank],
                value.candidates[selected_rank]});
        }
    }
    result.eligible_resident_alternatives = uint32_t(std::count(
        eligible_candidate.begin(), eligible_candidate.end(), true));
    std::sort(options.begin(), options.end(), [](const option & lhs, const option & rhs) {
        return std::tuple(-int(lhs.improvement), lhs.regret, lhs.candidate_rank, lhs.selected_rank,
                   lhs.candidate_expert, lhs.selected_expert) <
               std::tuple(-int(rhs.improvement), rhs.regret, rhs.candidate_rank, rhs.selected_rank,
                   rhs.candidate_expert, rhs.selected_expert);
    });
    std::array<bool, selected_count> used_slots {};
    std::array<bool, candidate_count> used_candidates {};
    for (const option & item : options) {
        if (result.swaps == s2_max_swaps) break;
        if (used_slots[item.selected_rank] || used_candidates[item.candidate_rank]) continue;
        result.experts[item.selected_rank] = item.candidate_expert;
        used_slots[item.selected_rank] = true;
        used_candidates[item.candidate_rank] = true;
        ++result.swaps;
        result.regret += item.regret;
        result.max_regret = std::max(result.max_regret, double(item.regret));
    }
    return result;
}

s2_stats s2_replay(const capture & value, uint32_t capacity_slots) {
    s2_stats result;
    lru_cache cache(capacity_slots);
    for (const record & item : value.records) {
        if (!item.decode) {
            std::array<uint16_t, selected_count> semantic_order {};
            std::copy(item.candidates.begin(), item.candidates.begin() + selected_count,
                semantic_order.begin());
            std::sort(semantic_order.begin(), semantic_order.end());
            for (uint16_t expert : semantic_order) {
                ++result.prefill.requests;
                const bool hit = cache.access(expert_key(item.layer, expert), result.prefill.evictions);
                hit ? ++result.prefill.hits : ++result.prefill.misses;
            }
            result.prefill.occupancy_end = cache.size();
            continue;
        }
        const selected_route route = select_s2(item, cache);
        ++result.decisions;
        result.changed_decisions += route.swaps != 0;
        result.swaps += route.swaps;
        result.cumulative_regret += route.regret;
        result.maximum_regret = std::max(result.maximum_regret, route.max_regret);
        result.eligible_resident_alternatives += route.eligible_resident_alternatives;
        result.decisions_with_eligible_alternative += route.eligible_resident_alternatives != 0;
        auto semantic_order = route.experts;
        std::sort(semantic_order.begin(), semantic_order.end());
        for (uint16_t expert : semantic_order) {
            ++result.decode.requests;
            const bool hit = cache.access(expert_key(item.layer, expert), result.decode.evictions);
            hit ? ++result.decode.hits : ++result.decode.misses;
        }
        result.decode.occupancy_end = cache.size();
    }
    return result;
}

json exact_thresholds(const capture & value) {
    std::vector<uint32_t> finite;
    uint64_t cold = 0;
    for (size_t index = value.decode_sequence_offset; index < value.stack_distances.size(); ++index) {
        if (value.stack_distances[index] < 0) ++cold;
        else finite.push_back(uint32_t(value.stack_distances[index]));
    }
    std::sort(finite.begin(), finite.end());
    const uint64_t requests = value.stack_distances.size() - value.decode_sequence_offset;
    json result = json::object();
    for (double threshold : thresholds) {
        const uint64_t required_hits = uint64_t(std::ceil(threshold*double(requests)));
        const std::string label = "C" + std::to_string(int(std::round(threshold*100.0)));
        if (required_hits == 0) {
            result[label] = {{"status", "attained"}, {"slots", 0}, {"actual_bytes", 0}};
        } else if (required_hits > finite.size()) {
            result[label] = {{"status", "not_attainable_on_captured_trajectory"},
                {"decode_first_occurrence_misses", cold}};
        } else {
            const uint32_t slots = finite[size_t(required_hits - 1)] + 1;
            result[label] = {{"status", "attained"}, {"slots", slots},
                {"actual_bytes", uint64_t(slots)*expert_bytes},
                {"actual_gib", double(uint64_t(slots)*expert_bytes)/double(gib)}};
        }
    }
    return result;
}

json stack_distance_summary(const capture & value, size_t start, size_t end,
        const std::vector<bool> * core = nullptr, bool core_value = false) {
    std::vector<double> finite;
    uint64_t cold = 0;
    for (size_t index = start; index < end; ++index) {
        if (core != nullptr && core->at(value.exact_sequence[index]) != core_value) continue;
        const int32_t distance = value.stack_distances[index];
        if (distance < 0) ++cold;
        else finite.push_back(double(distance));
    }
    return {{"cold_first_occurrences", cold}, {"finite_stack_distance_slots", distribution(std::move(finite))}};
}

std::vector<capture> load_captures(const json & progress, const json & selection) {
    std::map<std::string, json> metadata;
    for (const auto & row : selection.at("stage_b").at("representatives")) metadata[row.at("case_id")] = row;
    for (const auto & row : selection.at("stage_b2").at("endpoints")) metadata[row.at("case_id")] = row;
    std::vector<capture> result;
    for (const auto & row : progress.at("captures")) {
        capture current;
        current.ordinal = row.at("ordinal");
        current.case_id = row.at("case_id");
        current.role = row.at("selection_role");
        current.prompt_tokens = row.at("prompt_tokens");
        const auto & meta = metadata.at(current.case_id);
        current.family = meta.at("semantic_family");
        current.length_level = meta.at("length_level");
        const json * identity = nullptr;
        if (row.contains("result")) identity = &row.at("result");
        else identity = &row.at("artifacts").at("result");
        current.result_path = fs::canonical(identity->at("path").get<std::string>());
        current.result_sha256 = identity->at("sha256");
        current.result_bytes = identity->at("bytes");
        if (fs::file_size(current.result_path) != current.result_bytes) {
            fail("observer result size changed: " + current.case_id);
        }
        const json document = load_json(current.result_path);
        if (document.at("status") != "pass" || document.at("case").at("id") != current.case_id ||
            document.at("observer").at("performance_evidence") != false) {
            fail("observer result metadata changed: " + current.case_id);
        }
        const auto & records = document.at("observer").at("records");
        const auto & measured = document.at("measured").at("cold_delta");
        current.measured_decode.requests = measured.at("requests");
        current.measured_decode.hits = measured.at("hits");
        current.measured_decode.misses = measured.at("misses");
        current.measured_decode.evictions = measured.at("evictions");
        current.measured_decode.occupancy_end = measured.at("occupancy_after");
        current.records.reserve(records.size());
        current.exact_sequence.reserve(records.size()*selected_count);
        size_t prefill_records = 0;
        for (const auto & source : records) {
            record item;
            item.decode = source.at("phase") == "DECODE";
            if (!item.decode) ++prefill_records;
            item.layer = uint16_t(source.at("layer").get<uint32_t>() - 1);
            const auto & candidates = source.at("candidate_experts");
            const auto & scores = source.at("candidate_selection_scores");
            if (candidates.size() != candidate_count || scores.size() != candidate_count) {
                fail("observer candidate shape changed: " + current.case_id);
            }
            for (uint32_t index = 0; index < candidate_count; ++index) {
                item.candidates[index] = candidates[index].get<uint16_t>();
                item.scores[index] = scores[index].get<float>();
            }
            std::array<uint16_t, selected_count> semantic_order {};
            std::copy(item.candidates.begin(), item.candidates.begin() + selected_count,
                semantic_order.begin());
            std::sort(semantic_order.begin(), semantic_order.end());
            for (uint16_t expert : semantic_order) {
                current.exact_sequence.push_back(expert_key(item.layer, expert));
            }
            current.records.push_back(item);
        }
        current.decode_sequence_offset = prefill_records*selected_count;
        if (current.decode_sequence_offset != size_t(current.prompt_tokens)*routed_layers*selected_count ||
            current.exact_sequence.size() - current.decode_sequence_offset !=
                size_t(decode_forwards)*routed_layers*selected_count) {
            fail("observer phase sequence changed: " + current.case_id);
        }
        current.stack_distances = stack_distances(current.exact_sequence);
        result.push_back(std::move(current));
    }
    std::sort(result.begin(), result.end(), [](const capture & lhs, const capture & rhs) {
        return lhs.ordinal < rhs.ordinal;
    });
    if (result.size() != 44) fail("observer capture count is not 44");
    return result;
}

json input_block(const arguments & args, const json & progress) {
    return {
        {"observer_progress", file_reference(args.progress, args.progress_sha256)},
        {"post_stage_a_selections", file_reference(args.selection, args.selection_sha256)},
        {"stage_a_final_checkpoint", file_reference(args.stage_a, args.stage_a_sha256)},
        {"standing_committee_core_periphery", file_reference(args.committee, args.committee_sha256)},
        {"execution_project_sha", progress.at("execution_project_sha")},
        {"nested_llama_cpp", progress.at("nested_llama_cpp")},
        {"expert_bundle_bytes", expert_bytes},
    };
}

json build_exact_mrc(const std::vector<capture> & captures, const std::vector<capacity> & grid,
        const json & inputs, const std::set<std::string> & representative_ids) {
    json prompt_rows = json::array();
    json prevalidation_mismatches = json::array();
    std::map<uint32_t, std::vector<double>> representative_hits;
    std::map<uint32_t, std::vector<double>> representative_loads;
    for (const capture & item : captures) {
        json curves = json::array();
        std::optional<double> previous_hit;
        std::optional<double> previous_gib;
        for (const capacity & point : grid) {
            const replay_stats stats = exact_replay(item, point.slots);
            const double hit = double(stats.decode.hits)/double(stats.decode.requests);
            const double point_gib = double(uint64_t(point.slots)*expert_bytes)/double(gib);
            json row = capacity_json(point);
            row["prefill"] = phase_json(stats.prefill, item.prompt_tokens);
            row["decode"] = phase_json(stats.decode);
            row["marginal_hit_ratio_gain_per_added_gib_from_previous_anchor"] =
                previous_hit && point_gib > *previous_gib ? (hit - *previous_hit)/(point_gib - *previous_gib) : 0.0;
            if (point.physical) {
                const bool matches = stats.decode.requests == item.measured_decode.requests &&
                    stats.decode.hits == item.measured_decode.hits &&
                    stats.decode.misses == item.measured_decode.misses &&
                    stats.decode.evictions == item.measured_decode.evictions &&
                    stats.decode.occupancy_end == item.measured_decode.occupancy_end;
                row["observer_capture_physical_prevalidation"] = {
                    {"status", matches ? "MATCH" : "MISMATCH"},
                    {"measured_observer_decode", phase_json(item.measured_decode)},
                    {"replay_minus_measured_hits", int64_t(stats.decode.hits) - int64_t(item.measured_decode.hits)},
                    {"replay_minus_measured_misses", int64_t(stats.decode.misses) - int64_t(item.measured_decode.misses)},
                    {"replay_minus_measured_evictions", int64_t(stats.decode.evictions) - int64_t(item.measured_decode.evictions)},
                };
                if (!matches) prevalidation_mismatches.push_back({{"case_id", item.case_id},
                    {"comparison", row["observer_capture_physical_prevalidation"]}});
            }
            previous_hit = hit;
            previous_gib = point_gib;
            curves.push_back(std::move(row));
            if (representative_ids.count(item.case_id)) {
                representative_hits[point.slots].push_back(hit);
                representative_loads[point.slots].push_back(double(stats.decode.misses)/decode_forwards);
            }
        }
        prompt_rows.push_back({
            {"case_id", item.case_id}, {"semantic_family", item.family}, {"length_level", item.length_level},
            {"selection_role", item.role}, {"prompt_tokens", item.prompt_tokens},
            {"observer_result", {{"path", item.result_path.string()}, {"bytes", item.result_bytes},
                {"sha256", item.result_sha256}}},
            {"capacity_curve", std::move(curves)}, {"capacity_thresholds", exact_thresholds(item)},
            {"prefill_stack_distance", stack_distance_summary(item, 0, item.decode_sequence_offset)},
            {"decode_stack_distance", stack_distance_summary(item, item.decode_sequence_offset,
                item.stack_distances.size())},
        });
    }
    json aggregate = json::array();
    for (const capacity & point : grid) {
        aggregate.push_back({
            {"capacity", capacity_json(point)},
            {"representative_prompt_hit_ratio", distribution(representative_hits.at(point.slots))},
            {"representative_prompt_loads_per_token", distribution(representative_loads.at(point.slots))},
        });
    }
    return {
        {"schema_version", "phase13-6pg-exact-capacity-mrc-v1"}, {"status", "pass"},
        {"provenance", {"POST_HOC_EXPLORATORY", "MEASURED_OBSERVER", "EXACT_REPLAY"}},
        {"performance_evidence", false}, {"inputs", inputs},
        {"replacement_policy", "production-equivalent global whole-ExpertKey LRU/ALWAYS"},
        {"capacity_grid", [&] { json rows = json::array(); for (const auto & point : grid) rows.push_back(capacity_json(point)); return rows; }()},
        {"prompt_rows", std::move(prompt_rows)}, {"representative_aggregate", std::move(aggregate)},
        {"observer_capture_physical_prevalidation", {
            {"status", prevalidation_mismatches.empty() ? "PASS" : "FAIL"},
            {"matched_capture_count", captures.size() - prevalidation_mismatches.size()},
            {"expected_capture_count", captures.size()}, {"mismatches", std::move(prevalidation_mismatches)},
            {"authority", "supporting semantic-order check; Stage-C EXACT remains the required physical anchor"},
        }},
        {"physical_anchor_validation", {{"status", "PENDING_STAGE_C_EXACT"},
            {"required_slots", physical_slots}, {"authoritative_larger_capacity_curve", false}}},
        {"disposition", "EXACT_REPLAY_COMPLETE_PENDING_PHYSICAL_ANCHOR_VALIDATION"},
    };
}

json build_s2_counterfactual(const std::vector<capture> & captures, const std::vector<capacity> & grid,
        const json & inputs, const json & stage_a) {
    std::map<std::string, json> stage_a_rows;
    for (const auto & row : stage_a.at("primary_rows")) stage_a_rows[row.at("case_id")] = row;
    json prompts = json::array();
    for (const capture & item : captures) {
        json curve = json::array();
        for (const capacity & point : grid) {
            const replay_stats exact = exact_replay(item, point.slots);
            const s2_stats s2 = s2_replay(item, point.slots);
            const double exact_hit = double(exact.decode.hits)/double(exact.decode.requests);
            const double s2_hit = double(s2.decode.hits)/double(s2.decode.requests);
            json row = capacity_json(point);
            row["exact_decode"] = phase_json(exact.decode);
            row["s2_fixed_route_decode"] = phase_json(s2.decode);
            row["s2_minus_exact_hit_ratio"] = s2_hit - exact_hit;
            if (exact.decode.misses) {
                row["s2_misses_over_exact_misses"] =
                    double(s2.decode.misses)/double(exact.decode.misses);
            } else {
                row["s2_misses_over_exact_misses"] = nullptr;
            }
            row["s2_load_reduction_vs_exact"] = int64_t(exact.decode.misses) - int64_t(s2.decode.misses);
            row["routing"] = {
                {"decisions", s2.decisions}, {"changed_decisions", s2.changed_decisions},
                {"realized_swaps", s2.swaps}, {"cumulative_score_regret", s2.cumulative_regret},
                {"mean_regret_per_swap", s2.swaps ? s2.cumulative_regret/double(s2.swaps) : 0.0},
                {"maximum_realized_regret", s2.maximum_regret},
                {"eligible_resident_low_regret_alternatives", s2.eligible_resident_alternatives},
                {"decisions_with_eligible_alternative", s2.decisions_with_eligible_alternative},
            };
            if (point.physical) {
                const json & physical = stage_a_rows.at(item.case_id);
                row["physical_stage_a_s2_comparison"] = {
                    {"stage_a_result_sha256", physical.at("result_sha256")},
                    {"fixed_route_minus_physical_hit_ratio", s2_hit - physical.at("hit_ratio").get<double>()},
                    {"fixed_route_minus_physical_misses", int64_t(s2.decode.misses) - physical.at("misses").get<int64_t>()},
                    {"fixed_route_minus_physical_changed_decisions", int64_t(s2.changed_decisions) - physical.at("changed_decisions").get<int64_t>()},
                    {"fixed_route_minus_physical_swaps", int64_t(s2.swaps) - physical.at("realized_swaps").get<int64_t>()},
                    {"route_feedback_included", false},
                };
            }
            curve.push_back(std::move(row));
        }
        prompts.push_back({{"case_id", item.case_id}, {"semantic_family", item.family},
            {"length_level", item.length_level}, {"selection_role", item.role}, {"capacity_curve", std::move(curve)}});
    }
    return {
        {"schema_version", "phase13-6pg-s2-fixed-route-capacity-counterfactual-v1"}, {"status", "pass"},
        {"provenance", {"POST_HOC_EXPLORATORY", "MEASURED_OBSERVER", "FIXED_ROUTE_COUNTERFACTUAL"}},
        {"performance_evidence", false}, {"inputs", inputs},
        {"configuration", {{"candidate_count", candidate_count}, {"max_swaps", s2_max_swaps},
            {"max_score_regret", s2_max_regret}, {"prefill_policy", "EXACT"},
            {"decode_candidate_trajectory", "CAPTURED_EXACT_HIDDEN_STATE_TRAJECTORY"}}},
        {"prompt_rows", std::move(prompts)},
        {"interpretation_guard", "Locality-only counterfactual; excludes autoregressive route feedback and semantic effects."},
        {"disposition", "S2_FIXED_ROUTE_COUNTERFACTUAL_COMPLETE_NONPHYSICAL"},
    };
}

std::vector<bool> loo_core(const json & committee, const std::string & case_id, double gamma) {
    for (const auto & sensitivity : committee.at("phases").at("DECODE").at("gamma_sensitivity")) {
        if (std::abs(sensitivity.at("gamma").get<double>() - gamma) > 1e-9) continue;
        for (const auto & held : sensitivity.at("leave_one_family_out")) {
            if (held.at("held_out_case_id") != case_id) continue;
            std::vector<bool> core(routed_layers*experts_per_layer, false);
            const auto & layers = held.at("core_experts_by_layer");
            for (uint32_t layer = 0; layer < routed_layers; ++layer) {
                for (uint32_t expert : layers.at(layer)) core[expert_key(layer, expert)] = true;
            }
            return core;
        }
    }
    fail("leave-one-family-out core not found: " + case_id);
}

json build_committee_pin(const std::vector<capture> & captures, const std::vector<capacity> & grid,
        const json & inputs, const json & committee, const std::set<std::string> & representative_ids) {
    const std::array<double, 5> gammas = {0.50, 0.75, 0.80, 0.90, 1.00};
    json prompts = json::array();
    for (const capture & item : captures) {
        if (!representative_ids.count(item.case_id)) continue;
        json gamma_rows = json::array();
        for (double gamma : gammas) {
            const std::vector<bool> core = loo_core(committee, item.case_id, gamma);
            json curve = json::array();
            for (const capacity & point : grid) {
                pinned_cache cache(point.slots, core);
                json row = capacity_json(point);
                row["core_expert_key_count"] = cache.core_slot_count();
                row["same_total_capacity_as_baseline"] = true;
                if (!cache.is_feasible()) {
                    row["status"] = "INFEASIBLE_CORE_EXCEEDS_CAPACITY";
                    curve.push_back(std::move(row));
                    continue;
                }
                uint64_t prefill_evictions = 0;
                uint64_t decode_evictions = 0;
                uint64_t core_hits = 0, core_misses = 0, peripheral_hits = 0, peripheral_misses = 0;
                for (size_t index = 0; index < item.exact_sequence.size(); ++index) {
                    const uint32_t key = item.exact_sequence[index];
                    uint64_t & evictions = index < item.decode_sequence_offset ? prefill_evictions : decode_evictions;
                    const bool hit = cache.access(key, evictions);
                    if (index < item.decode_sequence_offset) continue;
                    if (cache.is_core(key)) hit ? ++core_hits : ++core_misses;
                    else hit ? ++peripheral_hits : ++peripheral_misses;
                }
                const replay_stats baseline = exact_replay(item, point.slots);
                const uint64_t hits = core_hits + peripheral_hits;
                const uint64_t misses = core_misses + peripheral_misses;
                row["status"] = "pass";
                row["decode"] = {
                    {"requests", hits + misses}, {"hits", hits}, {"misses", misses},
                    {"hit_ratio", double(hits)/double(hits + misses)},
                    {"loads_per_token", double(misses)/decode_forwards},
                    {"backing_bytes_per_token", double(misses*expert_bytes)/decode_forwards},
                    {"peripheral_evictions", decode_evictions},
                };
                row["core"] = {{"requests", core_hits + core_misses}, {"hits", core_hits},
                    {"misses", core_misses}, {"hit_ratio", core_hits + core_misses ?
                        double(core_hits)/double(core_hits + core_misses) : 0.0},
                    {"stack_distance", stack_distance_summary(item, item.decode_sequence_offset,
                        item.stack_distances.size(), &core, true)}};
                row["periphery"] = {{"requests", peripheral_hits + peripheral_misses},
                    {"hits", peripheral_hits}, {"misses", peripheral_misses},
                    {"hit_ratio", peripheral_hits + peripheral_misses ?
                        double(peripheral_hits)/double(peripheral_hits + peripheral_misses) : 0.0},
                    {"stack_distance", stack_distance_summary(item, item.decode_sequence_offset,
                        item.stack_distances.size(), &core, false)}};
                row["vs_global_lru"] = {
                    {"hit_delta", int64_t(hits) - int64_t(baseline.decode.hits)},
                    {"miss_delta", int64_t(misses) - int64_t(baseline.decode.misses)},
                    {"hit_ratio_delta", double(hits)/double(hits + misses) -
                        double(baseline.decode.hits)/double(baseline.decode.requests)},
                };
                curve.push_back(std::move(row));
            }
            gamma_rows.push_back({{"gamma", gamma}, {"capacity_curve", std::move(curve)}});
        }
        prompts.push_back({{"case_id", item.case_id}, {"semantic_family", item.family},
            {"gamma_sensitivity", std::move(gamma_rows)}});
    }
    return {
        {"schema_version", "phase13-6pg-committee-pin-capacity-counterfactual-v1"}, {"status", "pass"},
        {"provenance", {"POST_HOC_EXPLORATORY", "EXACT_REPLAY_COUNTERFACTUAL", "NONPHYSICAL"}},
        {"performance_evidence", false}, {"inputs", inputs},
        {"baseline", "global production-equivalent LRU"},
        {"counterfactual", "reserve leave-one-family-out committee slots; LRU for remaining slots"},
        {"prompt_rows", std::move(prompts)},
        {"interpretation_guard", "A positive replay does not authorize production pinning or a TPS claim."},
        {"disposition", "COMMITTEE_PIN_COUNTERFACTUAL_COMPLETE_NONPHYSICAL"},
    };
}

json build_family_extension(const json & exact_mrc, const json & s2, const json & selection,
        const json & inputs) {
    std::map<std::string, json> exact_rows;
    std::map<std::string, json> s2_rows;
    for (const auto & row : exact_mrc.at("prompt_rows")) exact_rows[row.at("case_id")] = row;
    for (const auto & row : s2.at("prompt_rows")) s2_rows[row.at("case_id")] = row;
    std::map<std::string, std::vector<json>> families;
    for (const auto & row : selection.at("stage_b2").at("endpoints")) families[row.at("semantic_family")].push_back(row);
    json rows = json::array();
    for (auto & [family, endpoints] : families) {
        std::sort(endpoints.begin(), endpoints.end(), [](const json & lhs, const json & rhs) {
            return lhs.at("length_level").get<int>() < rhs.at("length_level").get<int>();
        });
        const std::string b1 = endpoints.at(0).at("case_id");
        const std::string b8 = endpoints.at(1).at("case_id");
        json threshold_deltas = json::object();
        for (double threshold : thresholds) {
            const std::string label = "C" + std::to_string(int(std::round(threshold*100.0)));
            const json & low = exact_rows.at(b1).at("capacity_thresholds").at(label);
            const json & high = exact_rows.at(b8).at("capacity_thresholds").at(label);
            if (low.at("status") == "attained" && high.at("status") == "attained") {
                threshold_deltas[label] = {{"b8_minus_b1_slots", high.at("slots").get<int64_t>() - low.at("slots").get<int64_t>()},
                    {"b8_minus_b1_bytes", high.at("actual_bytes").get<int64_t>() - low.at("actual_bytes").get<int64_t>()}};
            } else threshold_deltas[label] = {{"status", "NOT_COMPARABLE"}};
        }
        rows.push_back({{"semantic_family", family}, {"b1_case_id", b1}, {"b8_case_id", b8},
            {"exact_b1", exact_rows.at(b1)}, {"exact_b8", exact_rows.at(b8)},
            {"s2_fixed_route_b1", s2_rows.at(b1)}, {"s2_fixed_route_b8", s2_rows.at(b8)},
            {"exact_threshold_deltas", std::move(threshold_deltas)}});
    }
    return {
        {"schema_version", "phase13-6pg-family-length-capacity-extension-v1"}, {"status", "pass"},
        {"provenance", {"POST_HOC_EXPLORATORY", "EXACT_REPLAY", "FIXED_ROUTE_COUNTERFACTUAL"}},
        {"performance_evidence", false}, {"inputs", inputs}, {"families", std::move(rows)},
        {"endpoint_limit", "B1/B8 are endpoint sensitivity evidence, not complete family characterization."},
        {"physical_anchor_validation", "PENDING_STAGE_C_EXACT"},
        {"disposition", "FAMILY_LENGTH_CAPACITY_EXTENSION_COMPLETE_PENDING_PHYSICAL_VALIDATION"},
    };
}

void self_test() {
    const std::vector<uint32_t> sequence = {0, 1, 0, 2, 0, 1};
    const std::vector<int32_t> expected = {-1, -1, 1, -1, 1, 2};
    if (stack_distances(sequence) != expected) fail("LRU stack-distance self-test failed");
    lru_cache cache(2);
    uint64_t evictions = 0;
    uint64_t hits = 0;
    for (uint32_t key : sequence) hits += cache.access(key, evictions);
    if (hits != 2 || evictions != 2 || cache.size() != 2) fail("LRU cache self-test failed");

    record fixture;
    fixture.decode = true;
    fixture.layer = 0;
    for (uint32_t rank = 0; rank < candidate_count; ++rank) {
        fixture.candidates[rank] = uint16_t(rank);
        fixture.scores[rank] = 1.0f - float(rank)*0.01f;
    }
    fixture.scores[16] = 0.849f;
    fixture.scores[17] = 0.848f;
    lru_cache routing_cache(4);
    (void) routing_cache.access(expert_key(0, 17), evictions);
    const selected_route selected = select_s2(fixture, routing_cache);
    if (selected.swaps != 1 || selected.experts[15] != 17 || selected.eligible_resident_alternatives != 1) {
        fail("S2 selection self-test failed");
    }
}

} // namespace

int main(int argc, char ** argv) try {
    self_test();
    const arguments args = parse_arguments(argc, argv);
    const json progress = load_json(args.progress);
    const json selection = load_json(args.selection);
    const json stage_a = load_json(args.stage_a);
    const json committee = load_json(args.committee);
    if (progress.at("status") != "pass" || progress.at("accepted_capture_count") != 44 ||
        progress.at("disposition") != "OBSERVER_CAMPAIGN_COMPLETE_READY_FOR_SYNTHESIS" ||
        progress.at("performance_interpretation") != "FORBIDDEN") {
        fail("observer progress is not a complete non-performance pass");
    }
    if (selection.at("status") != "pass" || stage_a.at("status") != "pass" ||
        committee.at("status") != "pass") fail("replay input is not a pass");

    const json inputs = input_block(args, progress);
    const std::vector<capacity> grid = capacities();
    std::set<std::string> representative_ids;
    for (const auto & row : selection.at("stage_b").at("representatives")) {
        representative_ids.insert(row.at("case_id"));
    }
    const std::vector<capture> captures = load_captures(progress, selection);
    const json exact_mrc = build_exact_mrc(captures, grid, inputs, representative_ids);
    const json s2 = build_s2_counterfactual(captures, grid, inputs, stage_a);
    const json committee_pin = build_committee_pin(captures, grid, inputs, committee, representative_ids);
    const json family_extension = build_family_extension(exact_mrc, s2, selection, inputs);
    write_json(args.output_root/"exact-capacity-mrc.json", exact_mrc);
    write_json(args.output_root/"s2-fixed-route-capacity-counterfactual.json", s2);
    write_json(args.output_root/"committee-pin-capacity-counterfactual.json", committee_pin);
    write_json(args.output_root/"family-length-capacity-extension.json", family_extension);
    json index = {
        {"schema_version", "phase13-6pg-observer-replay-index-v1"}, {"status", "pass"},
        {"inputs", inputs}, {"capture_count", captures.size()},
        {"artifacts", {
            {"exact_capacity_mrc", (args.output_root/"exact-capacity-mrc.json").string()},
            {"s2_fixed_route_capacity_counterfactual", (args.output_root/"s2-fixed-route-capacity-counterfactual.json").string()},
            {"committee_pin_capacity_counterfactual", (args.output_root/"committee-pin-capacity-counterfactual.json").string()},
            {"family_length_capacity_extension", (args.output_root/"family-length-capacity-extension.json").string()},
        }},
        {"physical_anchor_validation", "PENDING_STAGE_C_EXACT"},
        {"disposition", "OFFLINE_REPLAY_COMPLETE_PENDING_PHYSICAL_ANCHOR_VALIDATION"},
    };
    write_json(args.output_root/"observer-replay-index.json", index);
    std::cout << json({{"status", "pass"}, {"capture_count", captures.size()},
        {"output_root", args.output_root.string()}}).dump() << std::endl;
    return 0;
} catch (const std::exception & error) {
    std::cerr << "issue102-observer-replay: " << error.what() << std::endl;
    return 1;
}

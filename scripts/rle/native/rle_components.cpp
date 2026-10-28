#include <algorithm>
#include <cstdint>
#include <limits>
#include <new>
#include <vector>

#if defined(_WIN32)
#define RLE_EXPORT __declspec(dllexport)
#else
#define RLE_EXPORT __attribute__((visibility("default")))
#endif

struct Component {
    std::uint32_t x_min;
    std::uint32_t y_min;
    std::uint32_t x_max;
    std::uint32_t y_max;
    std::uint32_t area;
    std::uint64_t sum_x;
    std::uint64_t sum_y;
};

namespace {

struct Node {
    std::uint32_t parent;
    std::uint32_t size;
};

std::uint32_t find_root(std::vector<Node>& nodes, std::uint32_t value) {
    std::uint32_t root = value;
    while (nodes[root].parent != root) root = nodes[root].parent;
    while (nodes[value].parent != value) {
        const std::uint32_t next = nodes[value].parent;
        nodes[value].parent = root;
        value = next;
    }
    return root;
}

void unite(std::vector<Node>& nodes, std::uint32_t first, std::uint32_t second) {
    first = find_root(nodes, first);
    second = find_root(nodes, second);
    if (first == second) return;
    if (nodes[first].size < nodes[second].size) std::swap(first, second);
    nodes[second].parent = first;
    nodes[first].size += nodes[second].size;
}

}  // namespace

// Return component count, or a negative error code: -1 arguments, -2 malformed
// runs, -3 output capacity, -4 allocation failure.
static int components_impl(const std::uint32_t* runs,
                                           std::uint32_t run_count,
                                           std::uint32_t* labels,
                                           Component* output,
                                           std::uint32_t output_capacity,
                                           std::uint32_t minimum_area) {
    if (minimum_area == 0) return -1;
    if ((run_count && (!runs || !labels)) || (output_capacity && !output)) return -1;
    if (run_count == 0) return 0;

    try {
        std::vector<Node> nodes(run_count);
        std::uint32_t previous_end = 0;
        std::uint32_t previous_row = std::numeric_limits<std::uint32_t>::max();

        for (std::uint32_t i = 0; i < run_count; ++i) {
            const std::uint32_t word = runs[i];
            if (word >> 29) return -2;
            const std::uint32_t row = (word >> 20) & 0x1ffu;
            const std::uint32_t x_begin = (word >> 10) & 0x3ffu;
            const std::uint32_t x_end = word & 0x3ffu;
            if (x_begin > x_end) return -2;
            if (i) {
                if (row < previous_row) return -2;
                if (row == previous_row && x_begin <= previous_end + 1u) return -2;
            }
            nodes[i] = Node{i, 1};
            previous_row = row;
            previous_end = x_end;
        }

        std::uint32_t prev_begin = 0, prev_end = 0;
        std::uint32_t curr_begin = 0;
        while (curr_begin < run_count) {
            const std::uint32_t row = (runs[curr_begin] >> 20) & 0x1ffu;
            std::uint32_t curr_end = curr_begin + 1;
            while (curr_end < run_count && ((runs[curr_end] >> 20) & 0x1ffu) == row)
                ++curr_end;

            if (row > 0 && prev_begin < prev_end &&
                ((runs[prev_end - 1] >> 20) & 0x1ffu) + 1u == row) {
                std::uint32_t p0 = prev_begin;
                std::uint32_t p1 = prev_begin;
                for (std::uint32_t c = curr_begin; c < curr_end; ++c) {
                    const std::uint32_t c0 = (runs[c] >> 10) & 0x3ffu;
                    const std::uint32_t c1 = runs[c] & 0x3ffu;
                    while (p0 < prev_end && (runs[p0] & 0x3ffu) + 1u < c0) ++p0;
                    if (p1 < p0) p1 = p0;
                    while (p1 < prev_end && ((runs[p1] >> 10) & 0x3ffu) <= c1 + 1u)
                        ++p1;
                    for (std::uint32_t p = p0; p < p1; ++p) unite(nodes, c, p);
                }
            }
            prev_begin = curr_begin;
            prev_end = curr_end;
            curr_begin = curr_end;
        }

        struct Aggregate {
            Component value;
            std::uint32_t first_run;
            bool initialized;
        };
        std::vector<Aggregate> aggregates(run_count);
        for (std::uint32_t i = 0; i < run_count; ++i) {
            const std::uint32_t root = find_root(nodes, i);
            const std::uint32_t word = runs[i];
            const std::uint32_t row = (word >> 20) & 0x1ffu;
            const std::uint32_t x0 = (word >> 10) & 0x3ffu;
            const std::uint32_t x1 = word & 0x3ffu;
            const std::uint32_t length = x1 - x0 + 1u;
            Aggregate& agg = aggregates[root];
            if (!agg.initialized) {
                agg.value = Component{x0, row, x1, row, 0, 0, 0};
                agg.first_run = i;
                agg.initialized = true;
            }
            agg.value.x_min = std::min(agg.value.x_min, x0);
            agg.value.y_min = std::min(agg.value.y_min, row);
            agg.value.x_max = std::max(agg.value.x_max, x1);
            agg.value.y_max = std::max(agg.value.y_max, row);
            agg.value.area += length;
            agg.value.sum_x += static_cast<std::uint64_t>(x0 + x1) * length / 2u;
            agg.value.sum_y += static_cast<std::uint64_t>(row) * length;
            labels[i] = root;
        }

        std::vector<std::uint32_t> roots;
        roots.reserve(run_count);
        for (std::uint32_t i = 0; i < run_count; ++i) {
            const std::uint32_t root = labels[i];
            if (aggregates[root].first_run == i && aggregates[root].value.area >= minimum_area)
                roots.push_back(root);
        }
        if (roots.size() > output_capacity) return -3;
        std::vector<std::uint32_t> remap(run_count, 0xffffffffu);
        for (std::uint32_t component = 0; component < roots.size(); ++component) {
            const std::uint32_t root = roots[component];
            remap[root] = component;
            output[component] = aggregates[root].value;
        }
        for (std::uint32_t i = 0; i < run_count; ++i) labels[i] = remap[labels[i]];
        return static_cast<int>(roots.size());
    } catch (const std::bad_alloc&) {
        return -4;
    } catch (...) {
        return -4;
    }
}

extern "C" RLE_EXPORT int rle_components(const std::uint32_t* runs,
        std::uint32_t count, std::uint32_t* labels, Component* output,
        std::uint32_t capacity) {
    return components_impl(runs, count, labels, output, capacity, 1);
}

extern "C" RLE_EXPORT int rle_components_filtered(const std::uint32_t* runs,
        std::uint32_t count, std::uint32_t* labels, Component* output,
        std::uint32_t capacity, std::uint32_t minimum_area) {
    return components_impl(runs, count, labels, output, capacity, minimum_area);
}

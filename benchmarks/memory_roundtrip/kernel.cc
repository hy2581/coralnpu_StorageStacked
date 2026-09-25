// Each access reaches external memory; no host data generator is needed.
#include <cstdint>
#include "benchmark_config.h"
int main() {
    auto *in = reinterpret_cast<volatile uint32_t *>(0x90000000u);
    auto *out = reinterpret_cast<volatile uint32_t *>(0x90010000u);
    auto *mailbox = reinterpret_cast<volatile uint32_t *>(0xc0000000u);
    uint32_t errors = 0;
    for (uint32_t repeat = 0; repeat < BENCH_ITERATIONS; ++repeat) {
        for (uint32_t i = 0; i < BENCH_WORDS; ++i)
            in[i * BENCH_STRIDE] = 0x1000u * (i + 1u) + (i ^ BENCH_SEED) + repeat;
        for (uint32_t i = 0; i < BENCH_WORDS; ++i)
            out[i * BENCH_STRIDE] = in[i * BENCH_STRIDE] * BENCH_MULTIPLIER + 1u;
        for (uint32_t i = 0; i < BENCH_WORDS; ++i) {
            uint32_t expected = (0x1000u * (i + 1u) + (i ^ BENCH_SEED) + repeat) * BENCH_MULTIPLIER + 1u;
            errors += out[i * BENCH_STRIDE] != expected;
        }
    }
    mailbox[0] = errors ? 0xbad00000u | (errors & 0xffffu) : 0x600d0000u;
    asm volatile("wfi");
    return 0;
}

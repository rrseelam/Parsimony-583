/*
 * Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
 *
 * NVIDIA CORPORATION and its licensors retain all intellectual property
 * and proprietary rights in and to this software, related documentation
 * and any modifications thereto.  Any use, reproduction, disclosure or
 * distribution of this software and related documentation without an express
 * license agreement from NVIDIA CORPORATION is strictly prohibited.
 */

#include <parsim.h>
#include <stdio.h>
#include <stdint.h>
#include <sys/time.h>

#include <cassert>

#define GANG_SIZE 64
#define LOCAL_ARRAY_SIZE 128
#define INNER_ITERS 128
#define REPEATS 4000

/* return time in microseconds */
static __attribute__((unused)) double GetTimer() {
    struct timeval tv;
    struct timezone tz;
    gettimeofday(&tv, &tz);
    return ((double)1000000 * (double)tv.tv_sec + (double)tv.tv_usec);
}

__attribute__((noinline)) void array_packing_kernel(uint32_t out[GANG_SIZE]) {
#psim gang_size(GANG_SIZE)
    {
        uint32_t lane = psim_get_lane_num();
        uint32_t local[LOCAL_ARRAY_SIZE];
        uint32_t acc = lane + 1;

        for (int r = 0; r < INNER_ITERS; r++) {
#pragma unroll
            for (int i = 0; i < LOCAL_ARRAY_SIZE; i++) {
                local[i] = acc + (uint32_t)(i * 3 + r);
            }

#pragma unroll
            for (int i = 1; i < LOCAL_ARRAY_SIZE - 1; i++) {
                uint32_t mix = local[i - 1] + local[i] + local[i + 1];
                acc = (acc * 33u) ^ (mix + (uint32_t)i + lane);
                local[i] = acc ^ (uint32_t)(r + i);
            }
        }

        out[lane] = acc + local[lane % LOCAL_ARRAY_SIZE];
    }
}

void array_packing_kernel_ref(uint32_t out[GANG_SIZE]) {
    for (uint32_t lane = 0; lane < GANG_SIZE; lane++) {
        uint32_t local[LOCAL_ARRAY_SIZE];
        uint32_t acc = lane + 1;

        for (int r = 0; r < INNER_ITERS; r++) {
            for (int i = 0; i < LOCAL_ARRAY_SIZE; i++) {
                local[i] = acc + (uint32_t)(i * 3 + r);
            }

            for (int i = 1; i < LOCAL_ARRAY_SIZE - 1; i++) {
                uint32_t mix = local[i - 1] + local[i] + local[i + 1];
                acc = (acc * 33u) ^ (mix + (uint32_t)i + lane);
                local[i] = acc ^ (uint32_t)(r + i);
            }
        }

        out[lane] = acc + local[lane % LOCAL_ARRAY_SIZE];
    }
}

int main() {
    uint32_t out[GANG_SIZE] = {};
    uint32_t ref[GANG_SIZE] = {};

    array_packing_kernel(out);
    array_packing_kernel_ref(ref);

    uint64_t checksum = 0;
    for (int i = 0; i < GANG_SIZE; i++) {
        assert(out[i] == ref[i]);
        checksum += out[i];
    }

    double t0 = GetTimer();
    for (int r = 0; r < REPEATS; r++) {
        array_packing_kernel(out);
    }
    double elapsed_us = GetTimer() - t0;

    checksum = 0;
    for (int i = 0; i < GANG_SIZE; i++) {
        checksum += out[i];
    }

    printf("array_packing_perf total_us: %.2f\n", elapsed_us);
    printf("array_packing_perf avg_us: %.4f\n", elapsed_us / REPEATS);
    printf("array_packing_perf checksum: %llu\n",
           (unsigned long long)checksum);
    printf("Success!\n");
}

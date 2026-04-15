/*
 * Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
 */

#include <parsim.h>
#include <cassert>
#include <cstdio>

#define GANG_SIZE 4

struct Pair {
    float x;
    float y;
};

int main() {
    float out_x[GANG_SIZE] = {};
    float out_y[GANG_SIZE] = {};

#psim gang_size(GANG_SIZE)
    {
        volatile Pair p;
        int lane = psim_get_lane_num();
        p.x = 1.0f + lane;
        p.y = 10.0f + 2.0f * lane;
        out_x[lane] = p.x;
        out_y[lane] = p.y;
    }

    for (int lane = 0; lane < GANG_SIZE; ++lane) {
        assert(out_x[lane] == 1.0f + lane);
        assert(out_y[lane] == 10.0f + 2.0f * lane);
    }

    printf("Success!\n");
}

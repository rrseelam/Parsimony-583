/*
 * Copyright (c) 2022, NVIDIA CORPORATION.  All rights reserved.
 */

#include <parsim.h>
#include <cassert>
#include <cstdio>

#define GANG_SIZE 1

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
        p.x = 3.0f + lane;
        p.y = 5.0f + lane;
        out_x[lane] = p.x;
        out_y[lane] = p.y;
    }

    assert(out_x[0] == 3.0f);
    assert(out_y[0] == 5.0f);
    printf("Success!\n");
}

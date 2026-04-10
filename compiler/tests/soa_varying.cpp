/*
 * Test: AoS-to-SoA transformation for a flat struct with varying access.
 * A flat struct with two float fields is accessed with varying lane indices.
 * The pass should replace the single struct alloca with per-field allocas.
 *
 * Compiled at -O0 so that Clang SROA does not decompose the struct alloca
 * before the Parsimony vectorizer processes it.
 */

#include <parsim.h>
#include <stdio.h>
#include <cassert>
#include <cmath>

#define GANG_SIZE 4

struct Point {
    float x;
    float y;
};

int main() {
    float results_x[GANG_SIZE];
    float results_y[GANG_SIZE];

#psim gang_size(GANG_SIZE)
    {
        unsigned lane = psim_get_lane_num();

        PSIM_WARNINGS_OFF
        Point p;
        p.x = 1.0f + lane;
        p.y = 10.0f + lane * 2.0f;

        results_x[lane] = p.x;
        results_y[lane] = p.y;
        PSIM_WARNINGS_ON
    }

    for (int i = 0; i < GANG_SIZE; i++) {
        float expected_x = 1.0f + i;
        float expected_y = 10.0f + i * 2.0f;
        assert(fabs(results_x[i] - expected_x) < 1e-6f);
        assert(fabs(results_y[i] - expected_y) < 1e-6f);
    }
    printf("Success!\n");
}

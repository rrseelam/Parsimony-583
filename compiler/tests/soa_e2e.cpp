/*
 * End-to-end correctness test: run an SPMD kernel that reads and writes a
 * flat struct through the (potentially SoA-transformed) allocas and verify
 * the output values are numerically correct.
 */

#include <parsim.h>
#include <stdio.h>
#include <cassert>
#include <cmath>

#define GANG_SIZE 8

struct Particle {
    float px;
    float py;
    float vx;
    float vy;
};

int main() {
    float out_px[GANG_SIZE];
    float out_py[GANG_SIZE];

#psim gang_size(GANG_SIZE)
    {
        unsigned lane = psim_get_lane_num();

        Particle pt;
        pt.px = (float)lane;
        pt.py = (float)(lane * 2);
        pt.vx = 0.5f;
        pt.vy = -0.25f;

        float dt = 1.0f;
        pt.px = pt.px + pt.vx * dt;
        pt.py = pt.py + pt.vy * dt;

        out_px[lane] = pt.px;
        out_py[lane] = pt.py;
    }

    for (int i = 0; i < GANG_SIZE; i++) {
        float expected_px = (float)i + 0.5f;
        float expected_py = (float)(i * 2) - 0.25f;
        assert(fabs(out_px[i] - expected_px) < 1e-5f);
        assert(fabs(out_py[i] - expected_py) < 1e-5f);
    }
    printf("Success!\n");
}

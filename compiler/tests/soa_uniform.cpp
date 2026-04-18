/*
 * Test: Uniform struct alloca should NOT be transformed by AoS-to-SoA.
 * The struct's address is taken (to preserve the alloca through SROA), but
 * the GEP indices are not varying.  The pass should leave the alloca
 * untransformed because isSoASafe rejects the address-escaping use.
 */

#include <parsim.h>
#include <stdio.h>
#include <cassert>

#define GANG_SIZE 4

struct Config {
    int width;
    int height;
};

int main() {
    int results[GANG_SIZE];
    uint64_t addrs[GANG_SIZE];

#psim gang_size(GANG_SIZE)
    {
        unsigned lane = psim_get_lane_num();

        PSIM_WARNINGS_OFF
        Config cfg;
        cfg.width = 640;
        cfg.height = 480;

        addrs[lane] = (uint64_t)&cfg;
        results[lane] = cfg.width + cfg.height;
        PSIM_WARNINGS_ON
    }

    for (int i = 0; i < GANG_SIZE; i++) {
        assert(results[i] == 1120);
    }
    printf("Success!\n");
}

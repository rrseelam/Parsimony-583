/*
 * Test: A struct containing a nested struct field should be skipped by
 * the AoS-to-SoA pass (left untransformed).
 */

#include <parsim.h>
#include <stdio.h>
#include <cassert>

#define GANG_SIZE 4

struct Inner {
    int a;
    int b;
};

struct Outer {
    Inner inner;
    int c;
};

int main() {
    int results[GANG_SIZE];

#psim gang_size(GANG_SIZE)
    {
        unsigned lane = psim_get_lane_num();

        PSIM_WARNINGS_OFF
        Outer o;
        o.inner.a = lane;
        o.inner.b = lane * 2;
        o.c = lane * 3;

        results[lane] = o.inner.a + o.inner.b + o.c;
        PSIM_WARNINGS_ON
    }

    for (int i = 0; i < GANG_SIZE; i++) {
        assert(results[i] == i + i * 2 + i * 3);
    }
    printf("Success!\n");
}

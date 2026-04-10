#!/bin/bash
set -e

mkdir -p bin

PASS=0
FAIL=0

run_test() {
    local file="$1"
    local opt="$2"
    local expect_soa="$3"
    local BIN=$(echo "$file" | sed "s/.cpp$//")
    
    echo -n "  $file ($opt): "
    
    output=$(parsimony $opt -march=native -mprefer-vector-width=512 "$file" \
        -o "bin/$BIN" --Xpsv="--vtransform 3" --Xtmp tmp 2>&1)
    
    if ! ./bin/$BIN 2>/dev/null | grep -q "Success"; then
        echo "FAIL (runtime)"
        FAIL=$((FAIL + 1))
        return
    fi
    
    if [ "$expect_soa" = "yes" ]; then
        if echo "$output" | grep -q "AoS-to-SoA: transforming struct alloca"; then
            echo "PASS (SoA applied)"
        else
            echo "FAIL (SoA NOT applied)"
            FAIL=$((FAIL + 1))
            return
        fi
    elif [ "$expect_soa" = "no" ]; then
        if echo "$output" | grep -q "AoS-to-SoA: transforming struct alloca"; then
            echo "FAIL (SoA applied, should have been skipped)"
            FAIL=$((FAIL + 1))
            return
        else
            echo "PASS (SoA correctly skipped)"
        fi
    else
        echo "PASS"
    fi
    
    PASS=$((PASS + 1))
}

echo "AoS-to-SoA transformation tests:"
echo

echo "1. Flat struct with varying access (SoA should apply):"
run_test soa_varying.cpp "-O0" "yes"

echo "2. Struct with address taken (SoA should be skipped):"
run_test soa_uniform.cpp "-O3" "no"

echo "3. Nested struct (SoA should be skipped):"
run_test soa_nested_skip.cpp "-O0" "no"

echo "4. End-to-end correctness (SoA should apply):"
run_test soa_e2e.cpp "-O0" "yes"

echo
echo "Results: $PASS passed, $FAIL failed"
[ $FAIL -eq 0 ] || exit 1

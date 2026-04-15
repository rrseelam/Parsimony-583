#!/bin/bash

set -euo pipefail

if [ -z "${PARSIM_ROOT:-}" ]; then
    echo "PARSIM_ROOT must be set"
    exit 1
fi

TMP_DIR="${PARSIM_ROOT}/compiler/tests/tmp_aos_to_soa"
PARSIMONY_BIN="${PARSIM_ROOT}/compiler/build/parsimony"
rm -rf "${TMP_DIR}"
mkdir -p "${TMP_DIR}"

run_case() {
    local src="$1"
    local out="${TMP_DIR}/$(basename "${src}" .cpp)"

    set +e
    "${PARSIMONY_BIN}" -O0 -march=native -mprefer-vector-width=512 \
        -I"${PARSIM_ROOT}/apps/synet-simd/src" \
        "${src}" -o "${out}" --Xtmp "${TMP_DIR}" >/dev/null 2>&1
    local status=$?
    set -e

    if [ ! -f "${TMP_DIR}/$(basename "${src}").post_vec.ll" ]; then
        echo "missing post-vectorization IR for ${src}"
        exit 1
    fi

    return ${status}
}

run_case "${PARSIM_ROOT}/compiler/tests/aos_to_soa_flat.cpp" || true
FLAT_IR="${TMP_DIR}/aos_to_soa_flat.cpp.post_vec.ll"
grep -q "field0" "${FLAT_IR}"
grep -q "field1" "${FLAT_IR}"
if grep -q "alloca %struct.Pair" "${FLAT_IR}"; then
    echo "flat struct alloca was not fully split"
    exit 1
fi
if grep -q "llvm.masked.gather.v4f32.v4p0f32" "${FLAT_IR}"; then
    echo "unexpected float gather in flat struct test"
    exit 1
fi

run_case "${PARSIM_ROOT}/compiler/tests/aos_to_soa_uniform.cpp" || true
UNIFORM_IR="${TMP_DIR}/aos_to_soa_uniform.cpp.post_vec.ll"
if grep -q "field0" "${UNIFORM_IR}"; then
    echo "uniform struct alloca was unexpectedly split"
    exit 1
fi

run_case "${PARSIM_ROOT}/compiler/tests/aos_to_soa_nested.cpp" || true
NESTED_IR="${TMP_DIR}/aos_to_soa_nested.cpp.post_vec.ll"
if grep -q "field0" "${NESTED_IR}"; then
    echo "nested struct alloca was unexpectedly split"
    exit 1
fi

echo "Success!"

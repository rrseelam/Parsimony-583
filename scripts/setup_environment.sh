#!/bin/bash

export LLVM_ROOT=/usr
export CC=/usr/bin/clang-15
export CXX=/usr/bin/clang++-15
export PATH=${PARSIM_ROOT}/ispc-v1.18.0-linux/bin:${PATH}
export Z3_ROOT=${PARSIM_ROOT}/z3/install
export SLEEF_ROOT=${PARSIM_ROOT}/sleef/install
export PARSIM_INSTALL_PATH=${PARSIM_ROOT}/compiler/install
export PATH=${PARSIM_INSTALL_PATH}/bin:${PATH}
export SETUP_ENVIRONMENT_WAS_RUN=1


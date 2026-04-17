# Array Packing Optimization

This document summarizes the array-packing changes made to the Parsimony compiler for use in a project report. The implementation lives primarily in `compiler/src/shapes.cpp` and updates the array layout optimization performed by `ShapesStep::arrayLayoutOpt()`.

## Goal

The goal of this change is to improve memory access behavior for stack-allocated arrays that are accessed by multiple lanes of a gang. Instead of leaving each lane's accesses in an unpacked layout that may require more expensive gather/scatter-style behavior later, the compiler rewrites eligible arrays into a packed layout that is friendlier to vectorized loads and stores.

Conceptually, an original array allocation of the form:

- `[N x T]`

is rewritten into:

- `[N x [num_lanes x T]]`

This inserts the lane dimension directly into the array layout. The pass then rewrites pointer derivations so that each access uses `psim_get_lane_num()` as the innermost index. As a result, values used by different lanes for the same logical array element become contiguous in memory.

## What Changed

The implementation was extended in three main ways.

### 1. Faster and more scalable use analysis

Previously, `analyzeUses()` used a recursive traversal and was conservative about nested use chains. In particular, it rejected cases such as `alloca -> gep -> gep`.

The new implementation replaces this recursive walk with an iterative worklist traversal:

- it uses a `std::vector` worklist and `std::unordered_set` visited set
- it avoids re-traversing the same instructions repeatedly
- it accepts nested GEP chains
- it still rejects unsafe pointer escapes

This improves the compile-time behavior of the pass and increases the number of array allocations that can be optimized safely.

### 2. Full pointer-derivation rewriting for packed arrays

Previously, `generateOptInsts()` only rewrote direct `GetElementPtrInst` users of the alloca. That meant the optimization only worked for very simple patterns such as:

- `alloca -> gep`

The new implementation rewrites a larger local pointer-use graph:

- `alloca -> gep -> gep`
- `alloca -> gep -> bitcast`
- longer chains of supported GEP/bitcast users after the lane dimension has been introduced

This is done by maintaining a worklist of rewritten instructions and recreating derived pointer instructions from the new packed base allocation.

### 3. Lower pass overhead in `arrayLayoutOpt()`

The driver itself was simplified:

- replacement records are stored in a `std::vector` instead of a `std::set`
- allocas with no users are skipped immediately
- the replacement container is pre-reserved based on instruction count

These changes reduce bookkeeping overhead in the optimization pass.

## Why This Improves Performance

The benefit of array packing is that it changes the memory layout to match the way SIMD execution consumes data. Without packing, accesses from different lanes to the same logical array position may be spread apart in memory, which can force the backend to use more expensive memory operations. With packing, those lane-wise values are adjacent, which makes packed vector loads and stores more likely.

In this compiler, that matters because later memory-shape analysis in `ShapesStep::calulateFinalMemInstMappedShapes()` and lowering in `TransformStep::transformMemInst()` can recognize packed access patterns and emit more efficient vector memory instructions.

In short, the optimization helps in two ways:

- it increases the number of cases that can be recognized as packed
- it reduces the cost of the analysis pass itself

## Safety and Current Limitations

The optimization remains conservative. It only applies when the alloca's use graph is safe to rewrite.

The pass still rejects:

- pointer escapes through unsupported stores
- unsupported non-intrinsic calls
- unsupported instruction types in the pointer-use chain
- a `bitcast` that appears before the first array-indexing GEP

The last case is important. If a bitcast occurs before the lane dimension is inserted, the pass loses the correct place to introduce the packed lane index without changing pointer semantics. To avoid generating incorrect IR, the optimization simply declines that case.

The current implementation also continues to skip arrays whose element type is a struct.

## Files Modified

The main implementation changes were made in:

- `compiler/src/shapes.cpp`
- `compiler/src/shapes.h`

The most important updated routines are:

- `ShapesStep::arrayLayoutOpt()`
- `ShapesStep::analyzeUses()`
- `ShapesStep::generateOptInsts()`
- `ShapesStep::insertOptInsts()`

## How To Test

This section gives a consolidated workflow for testing the array-packing change on both the general compiler tests and the dedicated performance microbenchmark.

### 1. Environment setup

Before running any test, set `PARSIM_ROOT` and load the project environment:

```bash
export PARSIM_ROOT=/Users/tanishka/umich/cse583/Parsimony-583
source "$PARSIM_ROOT/scripts/setup_environment.sh"
cd "$PARSIM_ROOT/compiler/tests"
```

The test driver used below is `compiler/tests/run.sh`. It compiles each selected test with Parsimony, writes the executable into `compiler/tests/bin`, writes intermediate compiler artifacts into `compiler/tests/tmp`, and then runs the executable.

### 2. Run the general correctness tests

To check that the array-packing changes do not break the standard stack-array and GEP-style cases, run:

```bash
./run.sh alloca.cpp gep_test1.cpp gep_test2.cpp packed_shuffle.cpp
```

These tests are useful because they cover:

- local stack arrays
- GEP-heavy access patterns
- packed/shuffle-oriented memory behavior

#### What to expect

Each test should:

- compile successfully
- run to completion without crashing
- print `Success!`

If any test fails with an assertion or compile-time error, that suggests the array-packing rewrite changed program behavior or generated invalid IR for that case.

### 3. Inspect the generated IR for array packing

After running the compile step, inspect `compiler/tests/tmp` to verify that the transformation actually occurred:

```bash
ls tmp
rg "psim_get_lane_num" tmp
rg "getelementptr" tmp
rg "\[[0-9]+ x \[[0-9]+ x" tmp
```

#### What to look for

The main signs that array packing happened are:

- calls to `psim_get_lane_num`
- GEPs with an extra lane index inserted
- nested array types that correspond to the packed layout, conceptually `[N x [num_lanes x T]]`

The most important qualitative comparison is:

- before the change: direct accesses to the original array layout
- after the change: rewritten pointer derivations that index through the extra lane dimension

### 4. Run the dedicated performance benchmark

A focused microbenchmark was added at `compiler/tests/array_packing_perf.cpp` to stress the exact optimization targeted by this work.

To build and run it:

```bash
./run.sh array_packing_perf.cpp
```

After it has been compiled once, you can rerun only the executable without recompiling:

```bash
./bin/array_packing_perf
```

#### What this benchmark measures

This kernel repeatedly allocates and updates a stack-local array inside a `#psim` region. That makes it a direct stress test for:

- alloca recognition
- packed array layout rewriting
- repeated reads and writes through the packed local array

It also checks correctness against a scalar reference implementation before reporting timing numbers.

#### What to expect

The benchmark should print lines similar to:

```text
array_packing_perf total_us: ...
array_packing_perf avg_us: ...
array_packing_perf checksum: ...
Success!
```

The important metrics are:

- `avg_us`: the average time per kernel invocation; this is the primary performance metric to compare
- `total_us`: the aggregate runtime across all repeats; useful as a sanity check
- `checksum`: must remain identical across runs and across compiler versions
- `Success!`: confirms the benchmark passed its correctness check

### 5. What metrics to compare

For a before/after comparison of the compiler change, use the same test command on:

- a baseline compiler version without the array-packing changes
- the updated compiler version with the new optimization

For the general tests, compare:

- whether the test compiles
- whether the test prints `Success!`
- whether the generated IR now shows array packing in `tmp`

For the microbenchmark, compare:

- `avg_us` between the old and new compiler
- `total_us` as a secondary runtime signal
- `checksum` to confirm identical behavior

### 6. How to interpret results

The expected outcome is:

- correctness stays the same on the general tests
- the transformed IR shows evidence of lane-based packed indexing
- the microbenchmark's `checksum` stays unchanged
- the microbenchmark's `avg_us` decreases, or at least does not regress materially

In general:

- lower `avg_us` is better
- equal `checksum` is required
- `Success!` is required

If you want a more stable timing comparison, run the microbenchmark several times and compare the median `avg_us` instead of relying on a single run.

### 7. Recommended testing sequence

A practical end-to-end testing sequence is:

```bash
export PARSIM_ROOT=/Users/tanishka/umich/cse583/Parsimony-583
source "$PARSIM_ROOT/scripts/setup_environment.sh"
cd "$PARSIM_ROOT/compiler/tests"

./run.sh alloca.cpp gep_test1.cpp gep_test2.cpp packed_shuffle.cpp
rg "psim_get_lane_num" tmp
rg "\[[0-9]+ x \[[0-9]+ x" tmp

./run.sh array_packing_perf.cpp
./bin/array_packing_perf
./bin/array_packing_perf
./bin/array_packing_perf
```

This sequence first checks correctness and transformation evidence, then gathers multiple timing samples for the dedicated benchmark.

## Report-Ready Summary

We extended Parsimony's array layout optimization to perform more effective array packing for stack-allocated arrays. The original pass only handled simple direct `alloca -> gep` access patterns and used a recursive use analysis. Our implementation replaces that analysis with an iterative worklist-based traversal, supports nested GEP/bitcast-based pointer derivation chains after packing is introduced, and reduces pass overhead by using cheaper replacement bookkeeping. The transformed layout explicitly adds a lane dimension, converting arrays from `[N x T]` to `[N x [num_lanes x T]]`, so lane-wise accesses to the same logical element become contiguous in memory. This improves the compiler's ability to recognize packed memory accesses and lowers vectorization overhead, while remaining conservative in cases that may change pointer semantics.

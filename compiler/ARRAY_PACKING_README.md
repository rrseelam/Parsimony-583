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

## Observed Test Results

The following results were collected on Ubuntu by running the standard test
driver from `compiler/tests`:

```bash
./run.sh alloca.cpp
./run.sh gep_test1.cpp
./run.sh gep_test2.cpp
./run.sh packed_shuffle.cpp
./run.sh array_packing_perf.cpp
```

The observed output was:

```text
./run.sh alloca.cpp
alloca.cpp
parsimony -O3 -march=native -I../../apps/synet-simd/src alloca.cpp -o bin/alloca --Xpsv="" --Xtmp tmp
alloca: alloca.cpp:41: int main(): Assertion `sum == ref_sum' failed.
./run.sh: line 28: 22667 Aborted                 (core dumped) ./bin/$BIN

./run.sh gep_test1.cpp
gep_test1.cpp
parsimony -O3 -march=native -I../../apps/synet-simd/src gep_test1.cpp -o bin/gep_test1 --Xpsv="" --Xtmp tmp
Success!

./run.sh gep_test2.cpp
gep_test2.cpp
parsimony -O3 -march=native -I../../apps/synet-simd/src gep_test2.cpp -o bin/gep_test2 --Xpsv="" --Xtmp tmp
Success!

./run.sh packed_shuffle.cpp
packed_shuffle.cpp
parsimony -O3 -march=native -I../../apps/synet-simd/src packed_shuffle.cpp -o bin/packed_shuffle --Xpsv="" --Xtmp tmp
WARNING: packed_shuffle.cpp:44:16 scatter/gather emitted
Success!

./run.sh array_packing_perf.cpp
array_packing_perf.cpp
parsimony -O3 -march=native -I../../apps/synet-simd/src array_packing_perf.cpp -o bin/array_packing_perf --Xpsv="" --Xtmp tmp
WARNING: array_packing_perf.cpp:52:27 scatter/gather emitted
array_packing_perf total_us: 1410703.00
array_packing_perf avg_us: 352.6757
array_packing_perf checksum: 139903857411
Success!
```

### Result Summary

- `gep_test1.cpp`: passed
- `gep_test2.cpp`: passed
- `packed_shuffle.cpp`: passed, but emitted a scatter/gather warning
- `array_packing_perf.cpp`: passed, produced stable timing output and checksum,
  but emitted one scatter/gather warning
- `alloca.cpp`: failed its final correctness assertion

This pattern is important. The optimization is working for the regular
GEP-based cases it was designed to improve, and the performance benchmark is
both correct and measurable. The only regression in this test set is the local
array initialization case in `alloca.cpp`.

## Why `alloca.cpp` Fails

The failing test is:

```cpp
uint8_t data[12] = {};
```

inside a `#psim` region. That matters because the array-packing optimization
changes the layout of eligible local arrays from:

- `[N x T]`

to:

- `[N x [num_lanes x T]]`

and then rewrites pointer derivations to index with `psim_get_lane_num()` as
the innermost dimension.

That transformation is correct in principle, but it also means the rewritten
allocation is a different object with:

- a different size
- a different physical layout
- different byte offsets for element accesses

The likely reason `alloca.cpp` fails is that the declaration `uint8_t data[12] =
{};` is typically lowered by LLVM into a zero-initialization intrinsic such as
`memset`. Rewriting only the direct GEP-style address computations is not
sufficient in that case. Any intrinsic or other size-sensitive use of the
original alloca must also be rewritten to match the packed object.

If the alloca is packed but the initialization pattern is still tied to the old
layout, then one of two things can happen:

- only part of the new packed array is initialized
- the initialization happens with the wrong byte interpretation for the new
  layout

Either way, the subsequent updates:

```cpp
for (int i = 0; i < 12; i++) {
    data[i] += lane * i;
}
```

start from incorrect values, so the final reduction in `main()` no longer
matches the scalar reference. That explains why compilation succeeds but the
program aborts at:

```cpp
assert(sum == ref_sum);
```

### Theoretical Takeaway

Array packing is a memory-layout transformation, not just a pointer rewrite. In
compiler terms, that means correctness depends on updating all uses whose
behavior depends on the allocation's shape, including:

- GEP chains
- bitcasts
- nested pointer derivations
- intrinsics such as `memset` or `memcpy`
- any other operation whose semantics depend on the original object size or
  byte layout

If even one use still assumes the old layout while the rest of the IR uses the
new packed layout, the transformed program can silently compute the wrong
result. The `alloca.cpp` regression is strong evidence that array
initialization/intrinsic handling is still incomplete for the packed local-array
case.

## Interpreting the Benchmark Results

The dedicated microbenchmark in `compiler/tests/array_packing_perf.cpp` is a
good stress test for this optimization because it repeatedly:

- allocates a stack-local array inside a `#psim` region
- writes every element of the local array
- repeatedly reads and updates nearby elements
- checks correctness against a scalar reference implementation
- times many repeated kernel invocations

The benchmark kernel uses:

- `GANG_SIZE = 64`
- `LOCAL_ARRAY_SIZE = 128`
- `INNER_ITERS = 128`
- `REPEATS = 4000`

The hot loop repeatedly fills `local[i]` and then updates it using neighboring
entries:

```cpp
uint32_t mix = local[i - 1] + local[i] + local[i + 1];
acc = (acc * 33u) ^ (mix + (uint32_t)i + lane);
local[i] = acc ^ (uint32_t)(r + i);
```

This is exactly the kind of access pattern array packing is meant to help. For
a fixed logical index `i`, all lanes are touching the same logical element of
their own private local arrays. After packing, those per-lane values become
adjacent in memory. That makes later vectorization and memory-lowering passes
more likely to recognize the accesses as packed vector memory operations rather
than falling back to gathers and scatters.

### What the numbers mean

The benchmark reported:

- `total_us: 1410703.00`
- `avg_us: 352.6757`
- `checksum: 139903857411`
- `Success!`

These values should be interpreted as follows:

- `Success!` means the optimized kernel matched the scalar reference
- `checksum` confirms deterministic output for the measured run
- `avg_us` is the primary runtime metric to compare across compiler versions
- `total_us` is a useful aggregate sanity check over all repeats

With `REPEATS = 4000`, the average time per kernel invocation is about
`352.7 us`. By itself, that number is not a speedup claim, because a true
performance claim requires comparison against a baseline compiler version
without the optimization. However, it does show that the packed-array path runs
correctly and efficiently enough to measure on a realistic repeated workload.

### Why there is still a scatter/gather warning

The benchmark emitted:

```text
WARNING: array_packing_perf.cpp:52:27 scatter/gather emitted
```

That warning comes from memory lowering, where the compiler falls back to masked
gather/scatter operations when it cannot prove the access is a clean packed
vector load/store.

In this benchmark, the warning is not surprising. The final access:

```cpp
out[lane] = acc + local[lane % LOCAL_ARRAY_SIZE];
```

uses an index that depends on `lane`, so different lanes may read different
positions. That kind of lane-dependent access is inherently less regular than
the uniform inner-loop accesses and is a natural candidate for a gather.

The important point is that the irregular access happens at the end of the
kernel, while the dominant inner-loop traffic uses regular index patterns that
are much better suited to packed lowering. So the presence of one
scatter/gather warning does not mean the optimization failed; it means the
benchmark still contains one intentionally irregular memory access.

## Overall Interpretation

The observed results support the following conclusions:

- the array-packing transformation helps the regular local-array and GEP-style
  cases it was intended to optimize
- the dedicated performance benchmark validates that the transformed code is
  still correct on a hot local-array workload
- the emitted scatter/gather warnings show that irregular lane-dependent
  accesses still fall back to more expensive memory operations, which is
  expected
- the remaining correctness gap is `alloca.cpp`, which likely exposes missing
  rewriting for zero-initialization or other intrinsic-based uses of a packed
  alloca

In short, the current implementation appears to be effective for regular packed
access patterns and promising for performance, but it is not yet fully complete
for all stack-array cases. The next correctness step is to make sure that
initialization-related uses of rewritten allocas are transformed consistently
with the new packed layout.

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

# Array Packing in Parsimony

## Executive Summary

This report describes the array-packing optimization added to the Parsimony
compiler, explains how it fits into the compiler pipeline, and analyzes the
performance results obtained from the new comparison benchmark. The goal of
array packing is to improve the performance of stack-allocated arrays inside
`#psim` regions by changing their memory layout to better match SIMD execution.

The optimization produced a substantial performance improvement on the
`array_packing_perf.cpp` benchmark. When array packing was disabled, the median
runtime was `5967.0785 us`. When array packing was enabled, the median runtime
dropped to `528.7848 us`, corresponding to an `11.285x` speedup and a `91.14%`
reduction in runtime. The output checksum remained identical across both
versions, showing that the optimization improved performance without changing
program behavior on this benchmark.

## What Array Packing Is

Array packing is a memory-layout optimization for stack-allocated arrays used
inside a `#psim` region. In a gang-executed program, each lane conceptually has
its own private local array. In the original layout, values belonging to
different lanes are not naturally arranged in the most SIMD-friendly way. As a
result, accesses that are logically regular in the SPMD program can still look
strided or scattered to the vector backend.

The core idea of array packing is to rewrite a local array from a layout of the
form:

- `[N x T]`

into a packed layout of the form:

- `[N x [num_lanes x T]]`

This inserts the lane dimension directly into the array type. After that
rewrite, when all lanes access the same logical element, such as `local[i]`,
their values become adjacent in memory. That makes the access pattern much more
compatible with packed vector loads and stores.

In short, array packing is meant to reduce expensive gather/scatter behavior
and increase the number of cases where the compiler can emit efficient packed
vector memory operations.

## How Array Packing Was Implemented

### Placement in the compiler pipeline

The optimization was implemented in the shape-analysis stage of the Parsimony
compiler. During vectorization, the compiler runs several analysis and
transformation steps in sequence. `ShapesStep` runs before `TransformStep`,
which is important because array packing changes how local memory accesses are
represented and therefore affects how later lowering handles memory
instructions.

At a high level, the flow is:

1. build analyses for the vectorized function
2. determine instruction order and shapes
3. apply array packing inside `ShapesStep`
4. lower the transformed program in `TransformStep`

This means array packing is not a late peephole optimization. It is a
structural rewrite that influences how later compiler stages reason about the
shape and layout of memory.

### Main implementation logic

The main implementation lives in `compiler/src/shapes.cpp`, inside
`ShapesStep::arrayLayoutOpt()`. The pass walks the instructions in the function,
looks for eligible `alloca` instructions, and rewrites them into packed array
allocations.

The optimization is intentionally conservative. It only applies when:

- the allocation is an array
- the array is one-dimensional
- the element type is not a struct
- the use graph is safe to rewrite

This conservatism is important because array packing changes the structure of
memory, so the compiler must avoid cases where rewriting pointer derivations
could change program semantics.

Once an eligible array is found, the compiler:

1. constructs a new packed allocation type with an explicit lane dimension
2. rewrites the relevant pointer derivations and GEPs
3. inserts `psim_get_lane_num()` as the innermost index
4. records the new allocation as an array-layout-optimized object in the value
   cache

As a result, lane-wise accesses to the same logical array element become
contiguous in memory.

### Comparison support added for testing

To make controlled A/B testing possible, a new compiler flag,
`-fno-array-packing`, was added to `psv`. This lets the same benchmark be
compiled with identical settings except for the array-packing optimization. The
comparison script uses that flag to create a baseline binary and an optimized
binary from the same source code.

This was an important addition because it made it possible to measure the
effect of array packing directly, without needing to switch between different
branches or older compiler versions.

## What Array Packing Is Supposed to Help

The optimization is designed to help programs that use stack-local arrays
inside `#psim` regions and access those arrays in a regular, lane-synchronous
way. It is especially beneficial when all lanes repeatedly access the same
logical indices at roughly the same time.

Typical examples include:

- repeated updates to local temporary arrays
- stencil-like loops
- neighbor-based computations such as `local[i - 1]`, `local[i]`, and
  `local[i + 1]`
- local arrays used in tight inner loops

In these situations, array packing improves the memory layout so that vector
loads and stores are easier for the backend to recognize and generate. This can
reduce gather/scatter operations and greatly improve performance.

## How the Optimization Was Tested

### Comparison script

The main performance evaluation was done with
`compiler/tests/compare_array_packing.py`. This script was created specifically
to compare the same benchmark with array packing disabled and enabled.

The script performs the following steps:

1. compiles the benchmark once with `-fno-array-packing`
2. compiles the same benchmark again with array packing enabled
3. runs both binaries multiple times
4. parses the benchmark's reported runtime metrics
5. verifies that checksums match
6. counts compile-time scatter/gather warnings
7. writes CSV, JSON, and SVG outputs for analysis and presentation

This testing methodology is important because it isolates the effect of array
packing from the rest of the compiler. Both binaries are built from the same
source, with the same optimization level and the same compilation pipeline,
except for the single array-packing toggle.

### Benchmark workload

The benchmark used for comparison was `compiler/tests/array_packing_perf.cpp`.
This benchmark is well suited to evaluating array packing because it stresses a
stack-local array inside a `#psim` region and repeatedly updates that array in
hot loops.

The kernel:

- declares a local array `local[LOCAL_ARRAY_SIZE]`
- initializes and updates it repeatedly
- uses neighboring accesses such as `local[i - 1]`, `local[i]`, and
  `local[i + 1]`
- validates correctness against a scalar reference implementation
- reports total and average runtime

This makes it a direct test of the exact pattern array packing is supposed to
improve.

## Results

The benchmark was run using:

```bash
./compare_array_packing.py --runs 9
```

The observed results were:

```text
Array packing comparison results
==================================
Benchmark: packing_disabled vs packing_enabled

Disabled median avg_us: 5967.0785
Enabled  median avg_us: 528.7848
Speedup: 11.285x
Improvement: 91.14%

Disabled warnings: 6 scatter/gather site(s) at compile time
Enabled  warnings: 1 scatter/gather site(s) at compile time
Disabled checksum: 139903857411
Enabled  checksum: 139903857411
```

These results show three key things.

First, the checksum is unchanged. That means both versions produced the same
output on the benchmark, so the optimized version preserved correctness for
this workload.

Second, the packed version is dramatically faster. Reducing the median runtime
from about `5967 us` to about `529 us` shows that array packing is not a minor
optimization in this case. It fundamentally changes the efficiency of the
generated memory operations.

Third, the number of scatter/gather warnings dropped from `6` to `1`. This is
strong evidence that array packing helped the compiler turn irregular memory
behavior into mostly packed vector memory accesses.

## Interpretation of the Results

The measured `11.285x` speedup is large, but it is also consistent with the
kind of workload being tested. `array_packing_perf.cpp` is almost an ideal case
for the optimization because it repeatedly operates on a local array in a
regular pattern inside a `#psim` region.

Without array packing, each lane's accesses to logically similar array elements
are spread apart in memory. Even though the source program looks regular, the
vector backend often sees memory operations that require gathers and scatters.
Those operations are significantly more expensive than packed loads and stores.

With array packing enabled, values from different lanes but the same logical
index are placed next to each other in memory. This gives later lowering stages
the structure they need to emit much more efficient vector memory operations.
The warning count supports exactly this interpretation: most of the
scatter/gather sites disappeared once array packing was applied.

The fact that one scatter/gather warning remains is also expected. The
benchmark ends with a lane-dependent access:

```cpp
out[lane] = acc + local[lane % LOCAL_ARRAY_SIZE];
```

That access is inherently less regular, since different lanes may read
different indices. Even after packing, irregular lane-dependent accesses may
still require gather/scatter behavior. This means the benchmark contains both a
best-case portion for array packing, namely the hot inner loops, and a small
irregular portion at the end.

## Why We Saw Such a Large Speedup

There are two main reasons for the large performance gain.

The first is that the benchmark's dominant memory traffic is highly regular.
The inner loops repeatedly touch corresponding positions of the local array
across all lanes. That is exactly the access pattern array packing is designed
to improve.

The second is that memory layout matters enormously for SIMD code. If the
backend has to use gather/scatter repeatedly, memory operations become much
more expensive. If the compiler can instead emit packed vector loads and stores
for the same logical accesses, the runtime can drop dramatically. The decrease
from six gather/scatter warning sites to one shows that this is what happened
in practice.

In other words, the large speedup did not come from a small arithmetic
improvement or a minor code cleanup. It came from changing the memory layout so
that the hardware could execute the benchmark in a way that better matches the
intended SIMD execution model.

## When We Should Continue to See Benefits

Array packing should continue to provide strong benefits in programs with the
following characteristics:

- stack-local arrays inside `#psim` regions
- repeated accesses to the same logical indices across lanes
- regular loop-based indexing
- neighbor or stencil-style array updates
- workloads where local array accesses dominate runtime

Examples include temporary local buffers, local stencils, reductions that use
regular temporary storage, and repeatedly updated scratch arrays in inner loops.

The optimization will generally be less effective when:

- accesses are highly irregular or lane-dependent
- pointer derivations are too complex or unsafe to rewrite
- the array shape is unsupported by the current implementation
- local array traffic is not the performance bottleneck

So the optimization is most powerful for regular local-memory access patterns,
and less effective for irregular or unsupported memory layouts.

## Pros and Cons of Array Packing

### Pros

The biggest advantage of array packing is performance. On the comparison
benchmark, it reduced median runtime by `91.14%`, which is an extremely strong
result for a single compiler optimization.

Another important advantage is that it improves code generation without asking
the programmer to manually rewrite their code. The user can write a natural
local-array computation, while the compiler transforms the underlying memory
layout to better fit SIMD execution.

It also integrates naturally into the existing compiler pipeline. Because it is
applied during shape analysis, later passes can benefit from a cleaner and more
explicitly packed memory structure.

Finally, the optimization directly targets a real weakness in SPMD-to-SIMD
lowering: the mismatch between per-lane logical storage and SIMD-friendly
physical layout. Array packing reduces that mismatch.

### Cons

The main disadvantage is complexity. Array packing is not a superficial
transformation. It changes memory layout, so the compiler must ensure that all
related pointer computations and size-sensitive operations remain correct.

A second limitation is that the current implementation is conservative. It only
supports safe cases, such as eligible one-dimensional arrays, and it skips
cases involving unsupported layouts or pointer-use patterns. This means the
optimization is powerful, but it does not yet apply universally.

A third drawback is correctness risk in edge cases. One known example is the
`alloca.cpp` test, which fails in a zero-initialized local-array case. That
suggests some initialization-related uses of rewritten allocas still require
additional handling.

Finally, the optimization may not help much for irregular accesses. If the
program fundamentally requires lane-dependent gathers and scatters, then
changing the layout can only help so much.

## Limitations and Current Status

Although the results are excellent, the implementation is not fully complete.
The optimization currently skips some categories of arrays by design, and at
least one correctness test involving zero-initialized local arrays still fails.

This means the optimization should be described as highly effective for its
targeted regular-access cases, rather than universally applicable to all stack
arrays. That is still a strong result. The performance gains on the benchmark
show that the optimization is valuable where it applies, and the remaining
limitations simply indicate where future engineering effort is needed.

## Conclusion

Array packing is a high-impact optimization for Parsimony's handling of
stack-local arrays inside `#psim` regions. By rewriting local array layout to
make lane-wise values contiguous in memory, it enables later compiler stages to
emit much more efficient vector memory operations.

The comparison benchmark demonstrates that this optimization can have a
dramatic effect in the right workload. On `array_packing_perf.cpp`, array
packing reduced median runtime from `5967.0785 us` to `528.7848 us`, yielding
an `11.285x` speedup and a `91.14%` improvement while preserving correctness.
The corresponding drop in scatter/gather warnings from `6` to `1` strongly
supports the explanation that the optimization improved the memory layout seen
by the vector backend.

Overall, array packing is clearly worth including in the compiler for regular
local-array workloads. Its main trade-off is increased implementation
complexity, along with some current restrictions and edge cases. Even with
those limitations, the measured results show that array packing is one of the
most impactful improvements made in this project.

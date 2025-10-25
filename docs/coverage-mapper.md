### Coverage Mapper

### Problem
Given an instrumented build and a test suite, compute a mapping from functions to the tests that execute them.

### Input
- cvc5 built with gcov instrumentation
- Test list from CTest

### Output
- JSON map: `src/path:demangled_signature:start_line` → `[test_name,...]`

### Algorithm (concise)
1. Discover tests with `ctest --show-only`.
2. For each test (or selected slice):
   a. Reset counters (fastcov --zerocounters).
   b. Run the test in isolation via `ctest -I i,i`.
   c. Run fastcov to produce a per-test JSON report.
   d. For each covered function under `src/` with execution_count > 0:
      - Demangle symbol via c++filt to canonical signature (includes STL defaults, cv-qualifiers).
      - Build function ID: `src/path:demangled_signature:start_line`.
      - Append current test to that function’s list.
3. Deduplicate and sort test lists; write JSON.

### Notes
- Per-test isolation yields precise test attribution vs cumulative coverage.
- Demangling aligns signatures with downstream matching (e.g., analyzer).
- Inlined/tiny functions may be absent as standalone symbols (attributed to callers).

### Artifacts
- Sharded JSONs: `coverage_mapping_START_END.json`
- Merged artifact: `coverage_mapping.json(.gz)`



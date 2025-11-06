### Commit Fuzzer

### Components
- GitHelper: commit metadata, `git show` diff, file blobs at commits.
- PrepareCommitAnalyzer: identifies changed functions and resolves covering tests.
- Matcher: looks up functions in the coverage map and returns tests.

### Inputs
- Commit SHA to analyze
- Coverage map: `coverage_mapping.json(.gz)`
- Build tree for parsing (e.g., `compile_commands.json`)

### Outputs
- For each commit: changed functions with test sets (and unmatched functions)
- Aggregate stats across commits (totals and overall coverage)

### Algorithm
1) Diff parsing (new-side line ranges)
   - Run: `git show -U0 --no-color <sha>`.
   - For each hunk header `@@ -<old>[,<n>] +<new>[,<m>] @@`:
     - Initialize `new_line = <new>`.
     - For each following line until next header:
       - If line starts with `+` (and not `+++`), record `new_line` as changed; increment `new_line`.
       - If context line (neither `+` nor `-`), increment `new_line`.
     - Associate recorded new-side lines with the current `+++ b/<path>` file.

2) Function selection (AST overlap)
   - For each changed C/C++ file:
     - Parse with libclang (prefer arguments from `compile_commands.json`).
     - Traverse functions/methods; keep only definitions.
     - Select those whose source extent overlaps any changed line.
     - If multiple functions cover a line, choose the innermost (smallest span).
     - Move detection: for the same function (stable key = signature without `:line`) that exists in the parent commit, compare normalized bodies (strip comments, collapse whitespace) between parent and target extents; if identical, classify as a pure move and skip.
     - Build function IDs as `path:demangled_signature:start_line` using `cursor.mangled_name` + `c++filt`.

3) Coverage lookup
   - Direct: exact `path:signature:line` match in coverage map.
   - Pathless: drop `:line` (requires identical signature).
   - If still unmatched, emit fuzzy candidates (do not count as covered).

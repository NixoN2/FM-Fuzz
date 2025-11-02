#!/usr/bin/env bash

# Simple fuzzer script that runs typefuzz on tests and reports bugs found.
# No coverage tracking - just fuzzing with cvc4 and z3.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_usage() {
  cat <<USAGE
Usage: $(basename "$0") --tests-json JSON [--job-id ID] [--tests-root PATH] [--timeout SECONDS] [--iterations NUM] [--z3-old-path PATH] [--cvc4-path PATH] [--cvc5-path PATH]

Options:
  --tests-json JSON   JSON array of test names (relative to --tests-root). Required
  --job-id ID         Job identifier (optional, for logging)
  --tests-root PATH   Root dir for tests (default: test/regress/cli)
  --timeout SECONDS   Timeout per fuzzer process (default: 21600 = 6 hours, use 0 for no timeout)
  -i, --iterations NUM  Number of iterations per test (default: 2147483647)
  --z3-old-path PATH  Path to z3-4.8.7 binary (required)
  --cvc4-path PATH    Path to cvc4-1.6 binary (required)
  --cvc5-path PATH    Path to cvc5 binary (default: ./build/bin/cvc5)
  -h, --help          Show this help
USAGE
}

TESTS_JSON=""
JOB_ID=""
TESTS_ROOT="test/regress/cli"
TIMEOUT_SECONDS=21600  # 6 hours (6 * 60 * 60)
ITERATIONS=2147483647
Z3_OLD_PATH=""
CVC4_PATH=""
CVC5_PATH="./build/bin/cvc5"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tests-json) TESTS_JSON="$2"; shift 2 ;;
    --job-id) JOB_ID="$2"; shift 2 ;;
    --tests-root) TESTS_ROOT="$2"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
    -i|--iterations) ITERATIONS="$2"; shift 2 ;;
    --z3-old-path) Z3_OLD_PATH="$2"; shift 2 ;;
    --cvc4-path) CVC4_PATH="$2"; shift 2 ;;
    --cvc5-path) CVC5_PATH="$2"; shift 2 ;;
    -h|--help) show_usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; show_usage; exit 2 ;;
  esac
done

if [[ -z "$TESTS_JSON" ]]; then
  echo "Error: --tests-json is required" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "Error: jq is required but not installed" >&2
  exit 1
fi

# Validate required paths
if [[ -z "$Z3_OLD_PATH" ]]; then
  echo "Error: --z3-old-path is required" >&2
  exit 1
fi

if [[ -z "$CVC4_PATH" ]]; then
  echo "Error: --cvc4-path is required" >&2
  exit 1
fi

# Verify paths exist
if [[ ! -f "$Z3_OLD_PATH" ]]; then
  echo "Error: z3-4.8.7 not found at: $Z3_OLD_PATH" >&2
  exit 1
fi

if [[ ! -f "$CVC4_PATH" ]]; then
  echo "Error: cvc4-1.6 not found at: $CVC4_PATH" >&2
  exit 1
fi

if [[ ! -f "$CVC5_PATH" ]]; then
  echo "Error: cvc5 not found at: $CVC5_PATH" >&2
  exit 1
fi

# z3 (new/stable) comes from PATH
if ! command -v z3 >/dev/null 2>&1; then
  echo "Error: z3 (new) not found in PATH. Please install z3-solver" >&2
  exit 1
fi

# Make paths absolute
Z3_OLD_PATH=$(realpath "$Z3_OLD_PATH" 2>/dev/null || echo "$Z3_OLD_PATH")
CVC4_PATH=$(realpath "$CVC4_PATH" 2>/dev/null || echo "$CVC4_PATH")
CVC5_PATH=$(realpath "$CVC5_PATH" 2>/dev/null || echo "$CVC5_PATH")
Z3_NEW="z3"

run_fuzzer() {
  local test_name="$1"
  local process_id="$2"
  
  # Create unique folders for this process
  local bugs_folder="bugs_${process_id}"
  local scratch_folder="scratch_${process_id}"
  local log_folder="logs_${process_id}"
  
  # Clean up any existing folders
  rm -rf "$bugs_folder" "$scratch_folder" "$log_folder"
  mkdir -p "$bugs_folder" "$scratch_folder" "$log_folder"
  
  local test_path="$TESTS_ROOT/$test_name"
  
  if [[ ! -f "$test_path" ]]; then
    echo "[PROCESS $process_id] Error: Test file not found: $test_path" >&2
    return 1
  fi
  
  echo "[PROCESS $process_id] Starting fuzzer on: $test_name"
  echo "[PROCESS $process_id] Using folders: bugs=$bugs_folder, scratch=$scratch_folder, log=$log_folder"
  echo "[PROCESS $process_id] Found test file: $test_path"
  
  # Build typefuzz command with all 4 solvers: z3-new, z3-old, cvc5, cvc4-1.6
  local solver_clis="$Z3_NEW;$Z3_OLD_PATH;$CVC5_PATH;$CVC4_PATH"
  
  # Timeout wrapper if timeout > 0
  local timeout_cmd=""
  if [[ "$TIMEOUT_SECONDS" -gt 0 ]]; then
    timeout_cmd="timeout -s 9 $TIMEOUT_SECONDS"
  fi
  
  # Run typefuzz
  local typefuzz_cmd=(
    $timeout_cmd
    typefuzz
    -i "$ITERATIONS"
    --bugs "$bugs_folder"
    --scratch "$scratch_folder"
    --logfolder "$log_folder"
    "$solver_clis"
    "$test_path"
  )
  
  set +e  # Don't exit on error, we want to check bugs even if typefuzz fails
  "${typefuzz_cmd[@]}" > "/tmp/typefuzz_${process_id}.out" 2> "/tmp/typefuzz_${process_id}.err"
  local exit_code=$?
  set -e
  
  # Check for bugs
  local bug_count=0
  local bug_files=()
  if [[ -d "$bugs_folder" ]]; then
    while IFS= read -r -d '' bug_file; do
      bug_files+=("$bug_file")
      bug_count=$((bug_count + 1))
    done < <(find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
  fi
  
  if [[ "$bug_count" -gt 0 ]]; then
    echo "[PROCESS $process_id] ✓ Found $bug_count bug(s)!"
    for bug_file in "${bug_files[@]}"; do
      echo "[PROCESS $process_id] Bug file: $bug_file"
      echo "[PROCESS $process_id] Bug file content:"
      echo "[PROCESS $process_id] ============================================================"
      cat "$bug_file" | sed "s/^/[PROCESS $process_id] /"
      echo "[PROCESS $process_id] ============================================================"
    done
  else
    echo "[PROCESS $process_id] No bugs found"
  fi
  
  # Show output if there was an error
  if [[ $exit_code -ne 0 ]]; then
    echo "[PROCESS $process_id] typefuzz exited with code $exit_code"
    if [[ -s "/tmp/typefuzz_${process_id}.err" ]]; then
      echo "[PROCESS $process_id] Error output:"
      head -20 "/tmp/typefuzz_${process_id}.err" | sed 's/^/  /'
    fi
  fi
  
  echo "[PROCESS $process_id] Completed fuzzing on: $test_name"
  
  # Clean up temp files
  rm -f "/tmp/typefuzz_${process_id}.out" "/tmp/typefuzz_${process_id}.err"
}

num_tests=$(echo "$TESTS_JSON" | jq 'length')
if [[ "$num_tests" -eq 0 ]]; then
  echo "No tests provided${JOB_ID:+ for job $JOB_ID}."
  exit 0
fi

echo "Running fuzzer on $num_tests test(s)${JOB_ID:+ for job $JOB_ID}"
echo "Tests root: $TESTS_ROOT"
echo "Timeout: ${TIMEOUT_SECONDS}s"
echo "Iterations per test: $ITERATIONS"
echo "Solvers: z3-new=$Z3_NEW, z3-old=$Z3_OLD_PATH, cvc5=$CVC5_PATH, cvc4=$CVC4_PATH"
echo ""

# Run each test in parallel (background processes)
proc_id=0
for i in $(seq 0 $((num_tests - 1))); do
  test_name=$(echo "$TESTS_JSON" | jq -r ".[$i] // empty")
  if [[ -n "$test_name" && "$test_name" != "null" ]]; then
    proc_id=$((proc_id + 1))
    run_fuzzer "$test_name" "$proc_id" &
  fi
done

wait

echo ""
echo "All fuzzing processes completed${JOB_ID:+ for job $JOB_ID}."
echo ""

# Aggregate and report bugs found
echo "============================================================"
echo "BUG SUMMARY${JOB_ID:+ FOR JOB $JOB_ID}"
echo "============================================================"

total_bugs=0
for proc_id in $(seq 1 $proc_id); do
  bugs_folder="bugs_${proc_id}"
  if [[ -d "$bugs_folder" ]]; then
    while IFS= read -r -d '' bug_file; do
      if [[ -f "$bug_file" ]]; then
        total_bugs=$((total_bugs + 1))
        echo ""
        echo "Bug #$total_bugs from $bugs_folder: $bug_file"
        echo "============================================================"
        cat "$bug_file"
        echo "============================================================"
      fi
    done < <(find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
  fi
done

if [[ $total_bugs -gt 0 ]]; then
  echo ""
  echo "Total bugs found: $total_bugs"
else
  echo "No bugs found in any process."
fi

echo "============================================================"


#!/usr/bin/env bash

# Simple fuzzer script that runs typefuzz on tests and reports bugs found.
# No coverage tracking - just fuzzing with cvc4 and z3.

set -euo pipefail

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

# Get job start time - use GitHub Actions job start time if available
if [[ -n "${GITHUB_RUN_STARTED_AT:-}" ]]; then
  JOB_START_TIME=$(date -u -d "${GITHUB_RUN_STARTED_AT}" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "${GITHUB_RUN_STARTED_AT}" +%s 2>/dev/null || date +%s)
  if [[ -z "$JOB_START_TIME" ]] || [[ ! "$JOB_START_TIME" =~ ^[0-9]+$ ]]; then
    JOB_START_TIME=$(date +%s)
  fi
else
  JOB_START_TIME=$(date +%s)
fi

GITHUB_JOB_TIMEOUT=21600  # 6 hours in seconds
BUGS_FOLDER="bugs"
REPORTED_BUGS_FILE="/tmp/reported_bugs_${JOB_ID:-$$}.txt"
FIVE_MIN_WARNING_FILE="/tmp/five_min_warning_${JOB_ID:-$$}.txt"

# Get time remaining in seconds based on GitHub Actions job timeout
get_time_remaining() {
  local current_time=$(date +%s)
  local elapsed=$((current_time - JOB_START_TIME))
  local remaining=$((GITHUB_JOB_TIMEOUT - elapsed))
  if [[ $remaining -lt 0 ]]; then
    echo "0"
  else
    echo "$remaining"
  fi
}

# Check if we're 5 minutes or less from GitHub Actions job timeout
is_5_minutes_left() {
  local remaining=$(get_time_remaining)
  if [[ "$remaining" == "0" ]]; then
    return 0
  fi
  if [[ $remaining -le 300 ]]; then  # 5 minutes = 300 seconds
    return 0
  fi
  return 1
}

# Check if bug has already been reported
is_bug_already_reported() {
  local bug_file="$1"
  if [[ ! -f "$bug_file" ]]; then
    return 1
  fi
  local bug_file_abs=$(realpath "$bug_file" 2>/dev/null || echo "$bug_file")
  if [[ -f "$REPORTED_BUGS_FILE" ]]; then
    grep -Fxq "$bug_file_abs" "$REPORTED_BUGS_FILE" 2>/dev/null && return 0
  fi
  return 1
}

# Mark bug as reported
mark_bug_as_reported() {
  local bug_file="$1"
  local bug_file_abs=$(realpath "$bug_file" 2>/dev/null || echo "$bug_file")
  echo "$bug_file_abs" >> "$REPORTED_BUGS_FILE"
}

# Output bug summary (only unreported bugs)
output_bug_summary() {
  local summary_title="$1"
  echo ""
  echo "============================================================"
  echo "$summary_title${JOB_ID:+ FOR JOB $JOB_ID}"
  echo "============================================================"
  
  local total_bugs=0
  local unreported_bugs=0
  if [[ -d "$BUGS_FOLDER" ]]; then
    while IFS= read -r -d '' bug_file; do
      if [[ -f "$bug_file" ]]; then
        total_bugs=$((total_bugs + 1))
        if ! is_bug_already_reported "$bug_file"; then
          unreported_bugs=$((unreported_bugs + 1))
          mark_bug_as_reported "$bug_file"
          echo ""
          echo "Bug #$unreported_bugs: $bug_file"
          echo "============================================================"
          cat "$bug_file"
          echo "============================================================"
        fi
      fi
    done < <(find "$BUGS_FOLDER" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
  fi
  
  if [[ $total_bugs -gt 0 ]]; then
    echo ""
    echo "Total bugs found: $total_bugs (unreported: $unreported_bugs)"
  else
    echo "No bugs found."
  fi
  echo "============================================================"
}

# Run a single test (for parallel execution)
run_test_worker() {
  local test_name="$1"
  local worker_id="$2"
  
  # Each worker has its own folders
  local bugs_folder="${BUGS_FOLDER}_${worker_id}"
  local scratch_folder="scratch_${worker_id}"
  local log_folder="logs_${worker_id}"
  local test_path="$TESTS_ROOT/$test_name"
  
  if [[ ! -f "$test_path" ]]; then
    echo "[WORKER $worker_id] Error: Test file not found: $test_path" >&2
    return 1
  fi
  
  echo "[WORKER $worker_id] Starting fuzzer on: $test_name"
  
  rm -rf "$scratch_folder" "$log_folder"
  mkdir -p "$bugs_folder" "$scratch_folder" "$log_folder"
  
  local solver_clis="$Z3_NEW;$Z3_OLD_PATH;$CVC5_PATH;$CVC4_PATH"
  local per_test_timeout=86400  # 24 hours
  local timeout_cmd="timeout -s 9 $per_test_timeout"
  
  set +e
  $timeout_cmd typefuzz \
    -i "$ITERATIONS" \
    --bugs "$bugs_folder" \
    --scratch "$scratch_folder" \
    --logfolder "$log_folder" \
    "$solver_clis" \
    "$test_path" > "/tmp/typefuzz_${worker_id}.out" 2> "/tmp/typefuzz_${worker_id}.err"
  local exit_code=$?
  set -e
  
  # Handle exit code 3
  if [[ $exit_code -eq 3 ]]; then
    echo "[WORKER $worker_id] ⚠ EXIT CODE 3: $test_name (unsupported operation - skipping)"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
    rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
    return $exit_code
  fi
  
  # Handle exit code 10 (bugs found)
  if [[ $exit_code -eq 10 ]]; then
    echo "[WORKER $worker_id] ✓ Exit code 10: Bugs found on $test_name!"
    local bug_count=0
    local new_bug_count=0
    local bug_files=()
    local new_bug_files=()
    if [[ -d "$bugs_folder" ]]; then
      while IFS= read -r -d '' bug_file; do
        bug_files+=("$bug_file")
        bug_count=$((bug_count + 1))
        if ! is_bug_already_reported "$bug_file"; then
          new_bug_files+=("$bug_file")
          new_bug_count=$((new_bug_count + 1))
          mark_bug_as_reported "$bug_file"
        fi
      done < <(find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
    fi
    
    if [[ "$new_bug_count" -gt 0 ]]; then
      echo "[WORKER $worker_id] Found $new_bug_count new bug(s) (total in folder: $bug_count):"
      for bug_file in "${new_bug_files[@]}"; do
        echo "[WORKER $worker_id] Bug file: $bug_file"
        echo "[WORKER $worker_id] Bug file content:"
        echo "[WORKER $worker_id] ============================================================"
        cat "$bug_file" | sed "s/^/[WORKER $worker_id] /"
        echo "[WORKER $worker_id] ============================================================"
      done
      # Move bugs to main bugs folder
      mkdir -p "$BUGS_FOLDER"
      for bug_file in "${new_bug_files[@]}"; do
        mv "$bug_file" "$BUGS_FOLDER/" 2>/dev/null || true
      done
    elif [[ "$bug_count" -gt 0 ]]; then
      echo "[WORKER $worker_id] No new bugs found (all $bug_count bug(s) already reported)"
    else
      echo "[WORKER $worker_id] Warning: Exit code 10 but no bugs found in folder"
    fi
    rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
    return $exit_code
  fi
  
  if [[ $exit_code -ne 0 ]]; then
    echo "[WORKER $worker_id] typefuzz exited with code $exit_code on $test_name"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
  else
    echo "[WORKER $worker_id] No bugs found on $test_name"
  fi
  
  rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
  return $exit_code
}

# Main execution
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

# Initialize reported bugs file
touch "$REPORTED_BUGS_FILE"
mkdir -p "$BUGS_FOLDER"

# Use 4 workers for parallel execution
NUM_WORKERS=4
echo "Starting $NUM_WORKERS worker(s) to process tests in parallel"
echo ""

# Collect all test names into shared array
test_names=()
for i in $(seq 0 $((num_tests - 1))); do
  test_name=$(echo "$TESTS_JSON" | jq -r ".[$i] // empty")
  if [[ -n "$test_name" && "$test_name" != "null" ]]; then
    test_names+=("$test_name")
  fi
done

# Worker process - iterates over all tests repeatedly
worker_process() {
  local worker_id="$1"
  local total_tests=${#test_names[@]}
  
  echo "[WORKER $worker_id] Started"
  
  # Loop through all tests repeatedly
  while true; do
    for test_idx in $(seq 0 $((total_tests - 1))); do
      local test_name="${test_names[$test_idx]}"
      if [[ -z "$test_name" ]]; then
        continue
      fi
      
      # Run fuzzer on this test
      run_test_worker "$test_name" "$worker_id"
      local exit_code=$?
      
      # If exit code 3, skip and continue to next test
      if [[ $exit_code -eq 3 ]]; then
        echo "[WORKER $worker_id] Skipping $test_name (exit code 3), moving to next"
        continue
      fi
    done
    # After processing all tests, start over
    echo "[WORKER $worker_id] Completed one full pass, starting over..."
  done
}

# Background monitor for 5-minute warning
time_monitor() {
  while true; do
    sleep 30
    if [[ ! -f "$FIVE_MIN_WARNING_FILE" ]] && is_5_minutes_left; then
      local remaining=$(get_time_remaining)
      if [[ "$remaining" != "0" ]]; then
        local remaining_min=$((remaining / 60))
        echo "⏰ WARNING: Only $remaining_min minute(s) remaining! Outputting bug summary..."
        touch "$FIVE_MIN_WARNING_FILE"
        output_bug_summary "BUG SUMMARY (5 MINUTES LEFT)"
      fi
    fi
    
    # Check if all workers are done
    local all_done=true
    for pid in "${worker_pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        all_done=false
        break
      fi
    done
    if $all_done; then
      break
    fi
  done
}

# Start time monitor
time_monitor &
monitor_pid=$!

# Start worker processes
worker_pids=()
for worker_id in $(seq 1 $NUM_WORKERS); do
  worker_process "$worker_id" &
  worker_pids+=($!)
done

# Wait for timeout or manual stop (workers run indefinitely)
# For now, wait for all workers (they'll run until timeout)
wait "${worker_pids[@]}"

# Stop time monitor
kill "$monitor_pid" 2>/dev/null || true
wait "$monitor_pid" 2>/dev/null || true

# Collect bugs from all worker folders
for worker_id in $(seq 1 $NUM_WORKERS); do
  bugs_folder="${BUGS_FOLDER}_${worker_id}"
  if [[ -d "$bugs_folder" ]]; then
    mkdir -p "$BUGS_FOLDER"
    find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -exec mv {} "$BUGS_FOLDER/" \; 2>/dev/null || true
  fi
done

echo ""
echo "All tests completed${JOB_ID:+ for job $JOB_ID}."
echo ""

# Final bug summary
output_bug_summary "FINAL BUG SUMMARY"

echo "Versions: z3-new=$Z3_NEW, z3-old=$Z3_OLD_PATH, cvc5=$CVC5_PATH, cvc4=$CVC4_PATH"

# Clean up temp files
rm -f "$REPORTED_BUGS_FILE" "$FIVE_MIN_WARNING_FILE"

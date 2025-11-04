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

# GitHub Actions default job timeout is 6 hours (21600 seconds)
GITHUB_JOB_TIMEOUT=21600  # 6 hours in seconds
BUGS_FOLDER="bugs"
FIVE_MIN_WARNING_FILE="/tmp/five_min_warning_${JOB_ID:-$$}.txt"
SKIP_TESTS_FILE="/tmp/skip_tests_${JOB_ID:-$$}.txt"
SKIP_TESTS_LOCK="/tmp/skip_tests_${JOB_ID:-$$}.lock"

# Get time remaining in seconds based on GitHub Actions job timeout
# Uses GITHUB_RUN_STARTED_AT + 6 hours (default GitHub Actions timeout)
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
  if [[ $remaining -le 21000 ]]; then  # 10 minutes elapsed (5 hours 50 minutes before 6 hours)
    return 0
  fi
  return 1
}

# Handle timeout gracefully - exit with success
handle_timeout() {
  echo ""
  echo "⏰ Timeout reached. Shutting down gracefully..."
  # Kill all workers
  for pid in "${worker_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  # Wait a bit for workers to finish
  sleep 2
  # Force kill if still running
  for pid in "${worker_pids[@]}"; do
    kill -9 "$pid" 2>/dev/null || true
  done
  # Collect bugs from all worker folders
  for worker_id in $(seq 1 $NUM_WORKERS); do
    bugs_folder="${BUGS_FOLDER}_${worker_id}"
    if [[ -d "$bugs_folder" ]]; then
      mkdir -p "$BUGS_FOLDER"
      find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -exec mv {} "$BUGS_FOLDER/" \; 2>/dev/null || true
    fi
  done
  output_bug_summary "FINAL BUG SUMMARY (TIMEOUT)"
  # Clean up temp files
  rm -f "$FIVE_MIN_WARNING_FILE" "$SKIP_TESTS_FILE" "$SKIP_TESTS_LOCK"
  exit 0  # Exit with success
}

# Output bug summary
output_bug_summary() {
  local summary_title="$1"
  echo ""
  echo "============================================================"
  echo "$summary_title${JOB_ID:+ FOR JOB $JOB_ID}"
  echo "============================================================"
  
  local total_bugs=0
  if [[ -d "$BUGS_FOLDER" ]]; then
    while IFS= read -r -d '' bug_file; do
      if [[ -f "$bug_file" ]]; then
        total_bugs=$((total_bugs + 1))
        echo ""
        echo "Bug #$total_bugs: $bug_file"
        echo "============================================================"
        cat "$bug_file"
        echo "============================================================"
      fi
    done < <(find "$BUGS_FOLDER" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
  fi
  
  if [[ $total_bugs -gt 0 ]]; then
    echo ""
    echo "Total bugs found: $total_bugs"
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
  
  # Track start time
  local start_time=$(date +%s)
  
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
  
  # Calculate runtime
  local end_time=$(date +%s)
  local runtime=$((end_time - start_time))
  
  # Handle exit code 3 - mark test as skipped and continue
  if [[ $exit_code -eq 3 ]]; then
    echo "[WORKER $worker_id] ⚠ EXIT CODE 3: $test_name (unsupported operation - skipping)"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
    # Mark test as skipped so we don't run it again
    mark_test_as_skipped "$test_name"
    rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
    return 0  # Return success to continue fuzzing
  fi
  
  # Handle exit code 10 (bugs found)
  if [[ $exit_code -eq 10 ]]; then
    echo "[WORKER $worker_id] ✓ Exit code 10: Bugs found on $test_name!"
    local bug_count=0
    local bug_files=()
    if [[ -d "$bugs_folder" ]]; then
      while IFS= read -r -d '' bug_file; do
        bug_files+=("$bug_file")
        bug_count=$((bug_count + 1))
      done < <(find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -print0 2>/dev/null || true)
    fi
    
    if [[ "$bug_count" -gt 0 ]]; then
      echo "[WORKER $worker_id] Found $bug_count bug(s):"
      for bug_file in "${bug_files[@]}"; do
        echo "[WORKER $worker_id] Bug file: $bug_file"
        echo "[WORKER $worker_id] Bug file content:"
        echo "[WORKER $worker_id] ============================================================"
        cat "$bug_file" | sed "s/^/[WORKER $worker_id] /"
        echo ""
        echo "[WORKER $worker_id] ============================================================"
      done
      # Move bugs to main bugs folder
      mkdir -p "$BUGS_FOLDER"
      for bug_file in "${bug_files[@]}"; do
        mv "$bug_file" "$BUGS_FOLDER/" 2>/dev/null || true
      done
    else
      echo "[WORKER $worker_id] Warning: Exit code 10 but no bugs found in folder"
    fi
    rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
    return 0  # Return success to continue fuzzing
  fi
  
  if [[ $exit_code -ne 0 ]]; then
    echo "[WORKER $worker_id] typefuzz exited with code $exit_code on $test_name (runtime: ${runtime}s)"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
  else
    # Exit code 0 - check if it actually ran or if something went wrong
    if [[ $runtime -lt 5 ]]; then
      echo "[WORKER $worker_id] ⚠ No bugs found on $test_name (runtime: ${runtime}s - very short, may indicate issue)"
      if [[ -s "/tmp/typefuzz_${worker_id}.out" ]]; then
        echo "[WORKER $worker_id] Output (first 10 lines):"
        head -10 "/tmp/typefuzz_${worker_id}.out" | sed 's/^/  /'
      fi
      if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
        echo "[WORKER $worker_id] Error output (first 10 lines):"
        head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
      fi
    else
      echo "[WORKER $worker_id] No bugs found on $test_name (runtime: ${runtime}s)"
    fi
  fi
  
  rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
  return 0  # Always return success to continue fuzzing
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

mkdir -p "$BUGS_FOLDER"

# Use 4 workers for parallel execution
NUM_WORKERS=4
echo "Starting $NUM_WORKERS worker(s) to process tests in parallel"
echo ""

# Initialize worker_pids array (global)
declare -a worker_pids=()

# Collect all test names into shared array
test_names=()
for i in $(seq 0 $((num_tests - 1))); do
  test_name=$(echo "$TESTS_JSON" | jq -r ".[$i] // empty")
  if [[ -n "$test_name" && "$test_name" != "null" ]]; then
    test_names+=("$test_name")
  fi
done

# Check if test should be skipped (thread-safe)
is_test_skipped() {
  local test_name="$1"
  (
    flock -x 202
    if [[ -f "$SKIP_TESTS_FILE" ]]; then
      grep -Fxq "$test_name" "$SKIP_TESTS_FILE" 2>/dev/null && return 0
    fi
    return 1
  ) 202>"$SKIP_TESTS_LOCK"
}

# Mark test as skipped (thread-safe)
mark_test_as_skipped() {
  local test_name="$1"
  (
    flock -x 202
    # Check if already in file to avoid duplicates
    if [[ ! -f "$SKIP_TESTS_FILE" ]] || ! grep -Fxq "$test_name" "$SKIP_TESTS_FILE" 2>/dev/null; then
      echo "$test_name" >> "$SKIP_TESTS_FILE"
    fi
  ) 202>"$SKIP_TESTS_LOCK"
}

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
      
      # Skip tests that returned exit code 3
      if is_test_skipped "$test_name"; then
        continue
      fi
      
      # Run fuzzer on this test (continue even if it fails)
      run_test_worker "$test_name" "$worker_id" || true
      # All exit codes are handled inside run_test_worker, it always returns 0
    done
    # After processing all tests, start over
    echo "[WORKER $worker_id] Completed one full pass, starting over..."
  done
}

# Background monitor for 5-minute warning and timeout
time_monitor() {
  while true; do
    sleep 30
    local remaining=$(get_time_remaining)
    local elapsed=$((GITHUB_JOB_TIMEOUT - remaining))
    local elapsed_min=$((elapsed / 60))
    
    # Debug: log time every 2 minutes
    if [[ $((elapsed % 120)) -lt 30 ]]; then
      echo "[TIME MONITOR] Elapsed: ${elapsed_min}m, Remaining: ${remaining}s ($(($remaining / 60))m)"
    fi
    
    # Check if timeout reached - send SIGTERM to main process
    if [[ "$remaining" == "0" ]] || [[ $remaining -le 0 ]]; then
      kill -TERM "$MAIN_PID" 2>/dev/null || true
      break
    fi
    
    # Check for 5-minute warning - stop workers and output bugs
    if [[ ! -f "$FIVE_MIN_WARNING_FILE" ]] && is_5_minutes_left; then
      if [[ "$remaining" != "0" ]]; then
        local remaining_min=$((remaining / 60))
        echo "⏰ WARNING: Elapsed ${elapsed_min}m, ${remaining_min} minute(s) remaining! Stopping workers and outputting bug summary..."
        touch "$FIVE_MIN_WARNING_FILE"
        
        # Stop all workers gracefully
        for pid in "${worker_pids[@]}"; do
          kill "$pid" 2>/dev/null || true
        done
        # Wait a bit for workers to finish
        sleep 2
        # Force kill if still running
        for pid in "${worker_pids[@]}"; do
          kill -9 "$pid" 2>/dev/null || true
        done
        
        # Collect bugs from all worker folders
        for worker_id in $(seq 1 $NUM_WORKERS); do
          bugs_folder="${BUGS_FOLDER}_${worker_id}"
          if [[ -d "$bugs_folder" ]]; then
            mkdir -p "$BUGS_FOLDER"
            find "$bugs_folder" -type f \( -name "*.smt2" -o -name "*.smt" \) -exec mv {} "$BUGS_FOLDER/" \; 2>/dev/null || true
          fi
        done
        
        output_bug_summary "BUG SUMMARY (5 MINUTES LEFT - STOPPING WORKERS)"
        
        # Exit gracefully with success
        kill -TERM "$MAIN_PID" 2>/dev/null || true
        break
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

# Set up signal handlers for graceful shutdown
trap 'handle_timeout' SIGTERM SIGINT

# Capture main process PID for time monitor
MAIN_PID=$$

# Start time monitor
time_monitor &
monitor_pid=$!

# Start worker processes
for worker_id in $(seq 1 $NUM_WORKERS); do
  worker_process "$worker_id" &
  worker_pids+=($!)
done

# Wait for timeout or manual stop (workers run indefinitely)
# For now, wait for all workers (they'll run until timeout)
set +e  # Don't exit on error in wait
wait "${worker_pids[@]}" 2>/dev/null
wait_exit_code=$?
set -e

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
rm -f "$FIVE_MIN_WARNING_FILE" "$SKIP_TESTS_FILE" "$SKIP_TESTS_LOCK"

# Exit with success
exit 0

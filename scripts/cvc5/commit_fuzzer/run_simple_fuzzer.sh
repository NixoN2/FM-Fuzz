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

# Global queue management
QUEUE_LOCK="/tmp/fuzzer_queue_${JOB_ID:-$$}.lock"
QUEUE_FILE="/tmp/fuzzer_queue_${JOB_ID:-$$}.txt"
JOB_START_TIME=$(date +%s)

# Add test to end of queue (thread-safe)
add_test_to_queue() {
  local test_name="$1"
  (
    flock -x 200
    echo "$test_name" >> "$QUEUE_FILE"
  ) 200>"$QUEUE_LOCK"
}

# Check if we've exceeded the timeout
is_timeout_expired() {
  if [[ "$TIMEOUT_SECONDS" -le 0 ]]; then
    return 1  # No timeout
  fi
  local current_time=$(date +%s)
  local elapsed=$((current_time - JOB_START_TIME))
  if [[ $elapsed -ge $TIMEOUT_SECONDS ]]; then
    return 0  # Timeout expired
  fi
  return 1  # Still within timeout
}

# Run fuzzer on a single test
run_fuzzer_on_test() {
  local test_name="$1"
  local worker_id="$2"
  
  # Create unique folders for this worker (reused across tests)
  local bugs_folder="bugs_${worker_id}"
  local scratch_folder="scratch_${worker_id}"
  local log_folder="logs_${worker_id}"
  
  # Clean up scratch and log folders for fresh run (keep bugs folder)
  rm -rf "$scratch_folder" "$log_folder"
  mkdir -p "$bugs_folder" "$scratch_folder" "$log_folder"
  
  local test_path="$TESTS_ROOT/$test_name"
  
  if [[ ! -f "$test_path" ]]; then
    echo "[WORKER $worker_id] Error: Test file not found: $test_path" >&2
    return 1
  fi
  
  echo "[WORKER $worker_id] Starting fuzzer on: $test_name"
  
  # Build typefuzz command with all 4 solvers: z3-new, z3-old, cvc5, cvc4-1.6
  local solver_clis="$Z3_NEW;$Z3_OLD_PATH;$CVC5_PATH;$CVC4_PATH"
  
  # Don't apply timeout per test - we want tests to run as long as needed
  # The job-level timeout (6 hours) is handled by worker_process timeout check
  # Use a large per-test timeout (24 hours) as a safety net
  local per_test_timeout=86400  # 24 hours (safety net, shouldn't be reached)
  local timeout_cmd="timeout -s 9 $per_test_timeout"
  
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
  "${typefuzz_cmd[@]}" > "/tmp/typefuzz_${worker_id}.out" 2> "/tmp/typefuzz_${worker_id}.err"
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
    echo "[WORKER $worker_id] ✓ Found $bug_count bug(s) on $test_name!"
    for bug_file in "${bug_files[@]}"; do
      echo "[WORKER $worker_id] Bug file: $bug_file"
      echo "[WORKER $worker_id] Bug file content:"
      echo "[WORKER $worker_id] ============================================================"
      cat "$bug_file" | sed "s/^/[WORKER $worker_id] /"
      echo "[WORKER $worker_id] ============================================================"
    done
  else
    echo "[WORKER $worker_id] No bugs found on $test_name"
  fi
  
  # Handle exit codes: 10 = bugs found (success), 3 = error (reallocate immediately), others = show
  if [[ $exit_code -eq 10 ]]; then
    # Exit code 10 means bugs were found - this is success
    if [[ "$bug_count" -eq 0 ]]; then
      echo "[WORKER $worker_id] typefuzz exited with code 10 but no bugs found in folder"
    fi
  elif [[ $exit_code -eq 3 ]]; then
    echo "[WORKER $worker_id] typefuzz exited with code 3 on $test_name (unsupported operation - reallocating)"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
  elif [[ $exit_code -ne 0 ]]; then
    echo "[WORKER $worker_id] typefuzz exited with code $exit_code on $test_name"
    if [[ -s "/tmp/typefuzz_${worker_id}.err" ]]; then
      echo "[WORKER $worker_id] Error output:"
      head -10 "/tmp/typefuzz_${worker_id}.err" | sed 's/^/  /'
    fi
  fi
  
  echo "[WORKER $worker_id] Completed fuzzing on: $test_name (exit code: $exit_code)"
  
  # Clean up temp files
  rm -f "/tmp/typefuzz_${worker_id}.out" "/tmp/typefuzz_${worker_id}.err"
  
  # Return exit code for decision making
  return $exit_code
}

# Worker process that continuously pulls from queue
worker_process() {
  local worker_id="$1"
  
  while true; do
    # Check timeout first
    if is_timeout_expired; then
      echo "[WORKER $worker_id] Timeout expired (${TIMEOUT_SECONDS}s), stopping"
      break
    fi
    
    # Get next test from queue (thread-safe)
    local test_name=""
    
    # Use flock to atomically read and remove first line from queue
    (
      flock -x 200
      if [[ -f "$QUEUE_FILE" && -s "$QUEUE_FILE" ]]; then
        test_name=$(head -n 1 "$QUEUE_FILE")
        # Remove first line if we got a test
        if [[ -n "$test_name" ]]; then
          tail -n +2 "$QUEUE_FILE" > "${QUEUE_FILE}.tmp" && mv "${QUEUE_FILE}.tmp" "$QUEUE_FILE"
        fi
      fi
    ) 200>"$QUEUE_LOCK"
    
    # If no test available, wait a bit and check again (might be re-queued)
    if [[ -z "$test_name" ]]; then
      # Check if queue is truly empty or just temporarily empty
      sleep 2
      
      # Check queue status (using flock for safety)
      local queue_has_tests=false
      (
        flock -x 200
        if [[ -f "$QUEUE_FILE" && -s "$QUEUE_FILE" ]]; then
          echo "true" > "/tmp/queue_check_${JOB_ID:-$$}_${worker_id}.txt"
        fi
      ) 200>"$QUEUE_LOCK"
      
      if [[ -f "/tmp/queue_check_${JOB_ID:-$$}_${worker_id}.txt" ]]; then
        queue_has_tests=true
        rm -f "/tmp/queue_check_${JOB_ID:-$$}_${worker_id}.txt"
      fi
      
      # If queue has tests, continue loop to get one
      if [[ "$queue_has_tests" == "true" ]]; then
        continue
      fi
      
      # If still empty after wait and timeout expired, exit
      if is_timeout_expired; then
        break
      fi
      
      # If queue is still empty and no timeout, wait a bit more
      continue
    fi
    
    # Run fuzzer on this test
    run_fuzzer_on_test "$test_name" "$worker_id"
    local exit_code=$?
    
    # Handle exit codes
    if [[ $exit_code -eq 10 ]]; then
      # Exit code 10 = bugs found! Re-queue this test to continue fuzzing
      echo "[WORKER $worker_id] Re-queuing $test_name to continue finding more bugs"
      add_test_to_queue "$test_name"
      # Continue to next test immediately (don't wait)
    elif [[ $exit_code -eq 3 ]]; then
      # Exit code 3 = unsupported operation, skip this test
      echo "[WORKER $worker_id] Skipping $test_name (exit code 3), moving to next"
      # Continue to next test
    fi
  done
  
  echo "[WORKER $worker_id] Finished - no more tests in queue or timeout expired"
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

# Create test queue file
rm -f "$QUEUE_FILE" "$QUEUE_LOCK"
touch "$QUEUE_FILE"

# Populate queue with all tests
for i in $(seq 0 $((num_tests - 1))); do
  test_name=$(echo "$TESTS_JSON" | jq -r ".[$i] // empty")
  if [[ -n "$test_name" && "$test_name" != "null" ]]; then
    echo "$test_name" >> "$QUEUE_FILE"
  fi
done

# Use 4 workers (can be scaled later)
NUM_WORKERS=4

echo "Starting $NUM_WORKERS worker(s) to process $num_tests test(s) from queue"
echo "Workers will automatically reallocate tests when exit code 3 occurs"
echo ""

# Start worker processes
worker_pids=()
for worker_id in $(seq 1 $NUM_WORKERS); do
  worker_process "$worker_id" &
  worker_pids+=($!)
done

# Wait for all workers to complete
wait "${worker_pids[@]}"

# Clean up queue files
rm -f "$QUEUE_FILE" "$QUEUE_LOCK"

echo ""
echo "All fuzzing workers completed${JOB_ID:+ for job $JOB_ID}."
echo ""

# Aggregate and report bugs found
echo "============================================================"
echo "BUG SUMMARY${JOB_ID:+ FOR JOB $JOB_ID}"
echo "============================================================"

total_bugs=0
for worker_id in $(seq 1 $NUM_WORKERS); do
  bugs_folder="bugs_${worker_id}"
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
echo "Versions: z3-new=$Z3_NEW, z3-old=$Z3_OLD_PATH, cvc5=$CVC5_PATH, cvc4=$CVC4_PATH"


#!/usr/bin/env bash

# Runs assigned tests for a matrix job using commit_fuzzer.py with coverage tracking.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_usage() {
  cat <<USAGE
Usage: $(basename "$0") --tests-json JSON [--job-id ID] [--tests-root PATH] [--timeout SECONDS] [--iterations NUM] [--build-dir PATH]

Options:
  --tests-json JSON   JSON array of test names (relative to --tests-root). Required
  --job-id ID         Job identifier (optional, for logging)
  --tests-root PATH   Root dir for tests (default: test/regress/cli)
  --timeout SECONDS   Timeout per fuzzer process (default: 300, use 0 for no timeout)
  -i, --iterations NUM  Number of iterations per test (default: 2147483647)
  --build-dir PATH    Build directory (default: build)
  -h, --help          Show this help
USAGE
}

TESTS_JSON=""
JOB_ID=""
TESTS_ROOT="test/regress/cli"
TIMEOUT_SECONDS=300
ITERATIONS=2147483647
BUILD_DIR="build"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tests-json) TESTS_JSON="$2"; shift 2 ;;
    --job-id) JOB_ID="$2"; shift 2 ;;
    --tests-root) TESTS_ROOT="$2"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
    -i|--iterations) ITERATIONS="$2"; shift 2 ;;
    --build-dir) BUILD_DIR="$2"; shift 2 ;;
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

# Get the commit_fuzzer.py script path
COMMIT_FUZZER_SCRIPT="${SCRIPT_DIR}/commit_fuzzer.py"
if [[ ! -f "$COMMIT_FUZZER_SCRIPT" ]]; then
  echo "Error: commit_fuzzer.py not found at $COMMIT_FUZZER_SCRIPT" >&2
  exit 1
fi

run_fuzzer() {
  local test_name="$1"
  local process_id="$2"

  echo "[PROCESS $process_id] Starting fuzzer on: $test_name"
  
  python3 "$COMMIT_FUZZER_SCRIPT" \
    "$test_name" \
    --tests-root "$TESTS_ROOT" \
    --build-dir "$BUILD_DIR" \
    --timeout "$TIMEOUT_SECONDS" \
    --iterations "$ITERATIONS"
  
  echo "[PROCESS $process_id] Completed fuzzing on: $test_name"
}

num_tests=$(echo "$TESTS_JSON" | jq 'length')
if [[ "$num_tests" -eq 0 ]]; then
  echo "No tests provided${JOB_ID:+ for job $JOB_ID}."
  exit 0
fi

echo "Running fuzzer on $num_tests test(s)${JOB_ID:+ for job $JOB_ID}"
echo "Tests root: $TESTS_ROOT"
echo "Build dir: $BUILD_DIR"
echo "Timeout: ${TIMEOUT_SECONDS}s"
echo "Iterations per test: $ITERATIONS"
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



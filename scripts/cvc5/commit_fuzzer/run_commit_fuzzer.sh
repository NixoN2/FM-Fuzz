#!/usr/bin/env bash

# Runs assigned tests for a matrix job using multiple parallel typefuzz processes.

set -euo pipefail

show_usage() {
  cat <<USAGE
Usage: $(basename "$0") --tests-json JSON [--job-id ID] [--tests-root PATH] [--timeout SECONDS]

Options:
  --tests-json JSON   JSON array of test names (relative to --tests-root). Required
  --job-id ID         Job identifier (optional, for logging)
  --tests-root PATH   Root dir for tests (default: test/regress/cli)
  --timeout SECONDS   Timeout per fuzzer process (default: 300)
  -h, --help          Show this help
USAGE
}

TESTS_JSON=""
JOB_ID=""
TESTS_ROOT="test/regress/cli"
TIMEOUT_SECONDS=300

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tests-json) TESTS_JSON="$2"; shift 2 ;;
    --job-id) JOB_ID="$2"; shift 2 ;;
    --tests-root) TESTS_ROOT="$2"; shift 2 ;;
    --timeout) TIMEOUT_SECONDS="$2"; shift 2 ;;
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

run_fuzzer() {
  local test_path="$1"
  local process_id="$2"

  local bugs_folder="bugs_${process_id}"
  local scratch_folder="scratch_${process_id}"
  local log_folder="logs_${process_id}"
  local log_file="fuzzer_${process_id}.log"

  rm -rf "$bugs_folder" "$scratch_folder" "$log_folder"

  if [[ -f "$test_path" ]]; then
    timeout -s 9 "$TIMEOUT_SECONDS" typefuzz \
      --bugs "$bugs_folder" \
      --scratch "$scratch_folder" \
      --logfolder "$log_folder" \
      "z3;./build/bin/cvc5" "$test_path" > "$log_file" 2>&1 || true

    if [[ -f "$log_file" ]]; then
      cat "$log_file"
    fi

    rm -rf "$bugs_folder" "$scratch_folder" "$log_folder"
  else
    echo "Test file not found: $test_path"
  fi
}

num_tests=$(echo "$TESTS_JSON" | jq 'length')
if [[ "$num_tests" -eq 0 ]]; then
  echo "No tests provided${JOB_ID:+ for job $JOB_ID}."
  exit 0
fi

proc_id=0
for i in $(seq 0 $((num_tests - 1))); do
  test_name=$(echo "$TESTS_JSON" | jq -r ".[$i] // empty")
  if [[ -n "$test_name" && "$test_name" != "null" ]]; then
    proc_id=$((proc_id + 1))
    run_fuzzer "${TESTS_ROOT}/${test_name}" "$proc_id" &
  fi
done

wait
echo "All fuzzing processes completed${JOB_ID:+ for job $JOB_ID}."



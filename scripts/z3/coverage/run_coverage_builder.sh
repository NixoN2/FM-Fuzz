#!/bin/bash
# Simple coverage analysis script for a test range

START_INDEX=$1
END_INDEX=$2

echo "Running coverage analysis for tests ${START_INDEX}-${END_INDEX}"

# Change to build directory
cd z3/build

# Run coverage analysis - always exit with 0 to prevent GitHub Actions from stopping
# (the Python script already handles all errors gracefully)
python3 ../../scripts/z3/coverage/coverage_mapper.py \
    --build-dir . \
    --z3test-dir ../../z3test \
    --start-index ${START_INDEX} \
    --end-index ${END_INDEX} || true

echo "Coverage analysis completed"
exit 0


#!/bin/bash
# Simple coverage analysis script for a test range

set -e

START_INDEX=$1
END_INDEX=$2

echo "Running coverage analysis for tests ${START_INDEX}-${END_INDEX}"

# Change to build directory
cd z3/build

# Run coverage analysis
python3 ../../scripts/z3/coverage/coverage_mapper.py \
    --build-dir . \
    --z3test-dir ../../z3test \
    --start-index ${START_INDEX} \
    --end-index ${END_INDEX}

echo "Coverage analysis completed"


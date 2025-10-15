#!/bin/bash
# Simple coverage analysis script for a test range

set -e

START_INDEX=$1
END_INDEX=$2

echo "Running coverage analysis for tests ${START_INDEX}-${END_INDEX}"

# Change to build directory
cd cvc5/build

# Run coverage analysis
python3 ../../scripts/cvc5/coverage_mapper.py \
    --build-dir . \
    --start-index ${START_INDEX} \
    --end-index ${END_INDEX}

echo "Coverage analysis completed"

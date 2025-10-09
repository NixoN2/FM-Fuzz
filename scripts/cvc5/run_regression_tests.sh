#!/bin/bash
# CVC5 Regression Test Runner
# This script runs a single regression test suite
# Usage: ./run_regression_tests.sh <suite_name>
# Example: ./run_regression_tests.sh regress0

set -e

# Check arguments
if [ $# -eq 0 ]; then
    echo "Usage: $0 <suite_name>"
    echo "Available suites: regress0, regress1, regress2, regress3, regress4"
    echo "Example: $0 regress0"
    exit 1
fi

SUITE_NAME="$1"

# Validate suite name
case "$SUITE_NAME" in
    regress0|regress1|regress2|regress3|regress4)
        ;;
    *)
        echo "❌ Invalid suite name: $SUITE_NAME"
        echo "Available suites: regress0, regress1, regress2, regress3, regress4"
        exit 1
        ;;
esac

# Ensure we're in the CVC5 build directory
if [ ! -f "CMakeCache.txt" ]; then
    echo "❌ Please run this script from the CVC5 build directory"
    echo "   cd cvc5/build"
    exit 1
fi

echo "🧪 Running CVC5 regression test suite: $SUITE_NAME"
echo "=========================================="

# Run the test suite with parallel execution and timeout
if ctest -L "$SUITE_NAME" -j$(nproc) --timeout 3600 --output-on-failure; then
    echo "✅ $SUITE_NAME tests completed successfully"
    exit 0
else
    echo "❌ Some $SUITE_NAME tests failed"
    exit 1
fi

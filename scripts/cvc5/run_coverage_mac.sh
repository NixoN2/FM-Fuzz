#!/bin/bash
# Improved CVC5 Coverage Script
# This script generates comprehensive code coverage reports for CVC5

# Ensure we're in the build directory
if [ ! -f "CMakeCache.txt" ]; then
    echo "❌ Please run this script from the CVC5 build directory"
    echo "   cd /Users/andrei.zhukov/study/thesis/cvc5/build"
    exit 1
fi

# Check if coverage is enabled
if ! grep -q "ENABLE_COVERAGE:BOOL=ON" CMakeCache.txt; then
    echo "⚠️  Coverage is not enabled in this build"
    echo "   Rebuild with: ./configure.sh debug --coverage --assertions"
    exit 1
fi

echo "🧹 Cleaning old coverage data..."
rm -rf coverage* *.info
find . -name "*.gcda" -delete

echo "🧪 Running regression tests to generate coverage data..."
# echo "   Running first 10 tests from regress0 for testing purposes..."

# Comment out full regress0 suite for testing
if ! ctest -L regress0 --output-on-failure; then
    echo "❌ Some tests failed, but continuing with coverage generation..."
fi

# Run only first 10 tests from regress0 using CTest's -I option
# if ! ctest -L regress0 -I 1,10 --output-on-failure; then
#     echo "❌ Some tests failed, but continuing with coverage generation..."
# fi

# Uncomment these lines to run more comprehensive tests:
# echo "   Running regress1 tests (medium)..."
# ctest -L regress1 --output-on-failure
# echo "   Running regress2 tests (slower)..."
# ctest -L regress2 --output-on-failure

echo "📊 Generating coverage report..."
echo "   Capturing coverage data from .gcda files..."

# Capture coverage data with improved error handling
if ! lcov --capture --directory . --output-file coverage.info --ignore-errors gcov,inconsistent,unsupported,format; then
    echo "❌ Failed to capture coverage data"
    exit 1
fi

# Count source files captured
TOTAL_FILES=$(grep -c "SF:" coverage.info)
echo "   Captured coverage data for $TOTAL_FILES source files"

# Filter out system headers and dependencies
echo "   Filtering out system headers and dependencies..."
if ! lcov --remove coverage.info '/Applications/*' '/usr/include/*' '*/deps/*' -o coverage_filtered.info --ignore-errors unused,inconsistent,format; then
    echo "❌ Failed to filter coverage data"
    exit 1
fi

# Count filtered files
FILTERED_FILES=$(grep -c "SF:" coverage_filtered.info)
echo "   After filtering: $FILTERED_FILES source files"

# Generate HTML report
echo "   Generating HTML coverage report..."
if ! genhtml --branch-coverage --demangle-cpp --no-prefix -o coverage_html coverage_filtered.info --ignore-errors inconsistent,corrupt,unsupported,category; then
    echo "❌ Failed to generate HTML report"
    exit 1
fi

echo ""
echo "✅ Coverage report generated successfully!"
echo "📈 Summary:"
echo "   - Total source files: $TOTAL_FILES"
echo "   - Filtered source files: $FILTERED_FILES"
echo "   - HTML report: coverage_html/index.html"
echo ""
echo "🌐 Open the report in your browser:"
echo "   open coverage_html/index.html"

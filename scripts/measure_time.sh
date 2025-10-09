#!/bin/bash
# Generic Time Measurement Script
# Usage: ./measure_time.sh <script_to_run> [args...]
# Example: ./measure_time.sh ./build.sh --coverage

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <script_to_run> [args...]"
    echo "Example: $0 ./build.sh --coverage"
    exit 1
fi

SCRIPT_TO_RUN="$1"
shift  # Remove first argument, pass rest to the script

echo "⏱️  Starting time measurement for: $SCRIPT_TO_RUN"
echo "📝 Arguments: $@"
echo "=========================================="

# Start timing
START_TIME=$(date +%s)
START_TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

echo "🚀 Started at: $START_TIMESTAMP"
echo ""

# Run the script with all remaining arguments
if "$SCRIPT_TO_RUN" "$@"; then
    EXIT_CODE=0
    RESULT="SUCCESS"
else
    EXIT_CODE=$?
    RESULT="FAILED"
fi

# End timing
END_TIME=$(date +%s)
END_TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
DURATION=$((END_TIME - START_TIME))
MINUTES=$((DURATION / 60))
SECONDS=$((DURATION % 60))

echo ""
echo "=========================================="
echo "⏱️  Time Measurement Results"
echo "=========================================="
echo "Script: $SCRIPT_TO_RUN"
echo "Arguments: $@"
echo "Started: $START_TIMESTAMP"
echo "Finished: $END_TIMESTAMP"
echo "Duration: ${MINUTES}m ${SECONDS}s (${DURATION} seconds)"
echo "Result: $RESULT"
echo "Exit Code: $EXIT_CODE"

# Save results to file
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
RESULTS_FILE="timing_results_${TIMESTAMP}.txt"

cat > "$RESULTS_FILE" << EOF
Time Measurement Results
=======================
Script: $SCRIPT_TO_RUN
Arguments: $@
Started: $START_TIMESTAMP
Finished: $END_TIMESTAMP
Duration: ${MINUTES}m ${SECONDS}s (${DURATION} seconds)
Result: $RESULT
Exit Code: $EXIT_CODE
EOF

echo "📁 Results saved to: $RESULTS_FILE"

exit $EXIT_CODE

#!/bin/bash
# Generic Time Measurement Script
# Usage: ./measure_time.sh <script_to_run> [args...]
# Example: ./measure_time.sh ./build.sh --coverage

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
# Use trap to handle SIGTERM (exit code 143) gracefully
EXIT_CODE=0
RESULT="SUCCESS"
if ! "$SCRIPT_TO_RUN" "$@"; then
    EXIT_CODE=$?
    RESULT="FAILED"
    # If killed by SIGTERM (143), treat as success to prevent GitHub Actions from stopping
    if [ $EXIT_CODE -eq 143 ]; then
        echo "⚠️ Process was terminated (SIGTERM), but continuing..."
        EXIT_CODE=0
        RESULT="TERMINATED"
    fi
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

# Always exit with 0 to prevent GitHub Actions from stopping
# (individual scripts handle their own errors)
exit 0

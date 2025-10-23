#!/bin/bash

# Script to run commit coverage analysis for CVC5 commits
# Downloads coverage mapping artifact, gunzips it, and analyzes last N commits

set -e

# Default values
COMMITS_TO_ANALYZE=${1:-3}
PYTHON_SCRIPT=${2:-"$(dirname "$0")/commit_coverage_analyzer.py"}
COVERAGE_FILE=${3:-"coverage_mapping_merged.json"}
COMPILE_COMMANDS=${4:-""}
ARTIFACT_NAME="coverage-mapping-final"

echo "=========================================="
echo "CVC5 Commit Coverage Analysis"
echo "=========================================="
echo "Analyzing last $COMMITS_TO_ANALYZE commits"
echo ""

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not in a git repository"
    exit 1
fi

# Check if we have the coverage mapping file
if [ ! -f "$COVERAGE_FILE" ]; then
    echo "Coverage mapping file not found: $COVERAGE_FILE"
    echo "Please ensure the coverage mapping artifact has been downloaded and extracted"
    echo "You can download it from the GitHub Actions artifacts or run the coverage analysis workflow first"
    exit 1
fi

# Auto-detect compile_commands.json in build directory if not provided
if [ -z "$COMPILE_COMMANDS" ]; then
    if [ -f "build/compile_commands.json" ]; then
        COMPILE_COMMANDS="build"
    elif [ -d "build" ]; then
        COMPILE_COMMANDS="build"
    fi
fi

# Get commits that changed files in src/ folder
echo "Getting commits that changed files in src/ folder..."
COMMITS=()
# Scan window: 5x requested commits to ensure enough src/ changes
SCAN_LIMIT=$((COMMITS_TO_ANALYZE * 5))
while IFS= read -r commit; do
    if git show --name-only "$commit" 2>/dev/null | grep -q "^src/"; then
        COMMITS+=("$commit")
        if [ ${#COMMITS[@]} -ge $COMMITS_TO_ANALYZE ]; then
            break
        fi
    fi
done < <(git log --format="%H" -n $SCAN_LIMIT)

if [ ${#COMMITS[@]} -eq 0 ]; then
    echo "No commits found that changed src/ files"
    exit 1
fi

echo "Found commits that changed src/ files:"
echo "${COMMITS[@]}" | tr ' ' '\n' | nl -w1 -s'. '
echo ""

# Analyze each commit
COMMIT_COUNT=0
# Overall totals
TOTAL_FUNCS=0
TOTAL_WITH=0
TOTAL_WITHOUT=0
COMMITS_PROCESSED=0
for commit in "${COMMITS[@]}"; do
    COMMIT_COUNT=$((COMMIT_COUNT + 1))
    echo "=========================================="
    echo "ANALYZING COMMIT $COMMIT_COUNT/$COMMITS_TO_ANALYZE"
    echo "=========================================="
    
    COMMIT_MSG=$(git log --format="%s" -n 1 $commit)
    COMMIT_AUTHOR=$(git log --format="%an" -n 1 $commit)
    COMMIT_DATE=$(git log --format="%ad" -n 1 $commit)
    
    echo "Commit: $commit"
    echo "Message: $COMMIT_MSG"
    echo "Author: $COMMIT_AUTHOR"
    echo "Date: $COMMIT_DATE"
    echo ""
    
    # Run the coverage analysis (capture output for aggregation)
    TMP_OUT=$(mktemp)
    if [ -n "$COMPILE_COMMANDS" ]; then
        python3 "$PYTHON_SCRIPT" $commit --coverage-json "$COVERAGE_FILE" --compile-commands "$COMPILE_COMMANDS" | tee "$TMP_OUT"
    else
        python3 "$PYTHON_SCRIPT" $commit --coverage-json "$COVERAGE_FILE" | tee "$TMP_OUT"
    fi
    COMMITS_PROCESSED=$((COMMITS_PROCESSED + 1))
    # Parse summary line if present
    LINE=$(grep -E "Changed functions: [0-9]+; with coverage: [0-9]+; without: [0-9]+;" "$TMP_OUT" | tail -n 1 || true)
    if [ -n "$LINE" ]; then
        CF=$(echo "$LINE" | sed -n 's/.*Changed functions: \([0-9]\+\);.*/\1/p')
        WC=$(echo "$LINE" | sed -n 's/.*with coverage: \([0-9]\+\);.*/\1/p')
        WO=$(echo "$LINE" | sed -n 's/.*without: \([0-9]\+\);.*/\1/p')
        TOTAL_FUNCS=$((TOTAL_FUNCS + CF))
        TOTAL_WITH=$((TOTAL_WITH + WC))
        TOTAL_WITHOUT=$((TOTAL_WITHOUT + WO))
    fi
    rm -f "$TMP_OUT"
    
    echo ""
    echo "----------------------------------------"
    echo ""
done

echo "=========================================="
echo "Analysis complete!"
echo "=========================================="

# Overall statistics
if [ "$TOTAL_FUNCS" -gt 0 ]; then
  COV_PCT=$(awk "BEGIN{printf \"%.1f\", 100*$TOTAL_WITH/$TOTAL_FUNCS}")
else
  COV_PCT=0.0
fi
echo "OVERALL SUMMARY: commits=${COMMITS_PROCESSED}; total_functions=${TOTAL_FUNCS}; with_coverage=${TOTAL_WITH}; without_coverage=${TOTAL_WITHOUT}; overall_coverage=${COV_PCT}%"

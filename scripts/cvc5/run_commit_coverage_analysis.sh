#!/bin/bash

# Script to run commit coverage analysis for CVC5 commits
# Downloads coverage mapping artifact, gunzips it, and analyzes last N commits

set -e

# Default values
COMMITS_TO_ANALYZE=${1:-10}
ARTIFACT_NAME="coverage-mapping-final"
COVERAGE_FILE="coverage_mapping_merged.json"

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

# Get the last N commits from the current branch
echo "Getting last $COMMITS_TO_ANALYZE commits..."
COMMITS=$(git log -n $COMMITS_TO_ANALYZE --format="%H")

if [ -z "$COMMITS" ]; then
    echo "No commits found"
    exit 1
fi

echo "Found commits:"
echo "$COMMITS" | nl
echo ""

# Analyze each commit
COMMIT_COUNT=0
for commit in $COMMITS; do
    COMMIT_COUNT=$((COMMIT_COUNT + 1))
    echo "=========================================="
    echo "ANALYZING COMMIT $COMMIT_COUNT/$COMMITS_TO_ANALYZE"
    echo "=========================================="
    
    # Get commit info
    COMMIT_MSG=$(git log --format="%s" -n 1 $commit)
    COMMIT_AUTHOR=$(git log --format="%an" -n 1 $commit)
    COMMIT_DATE=$(git log --format="%ad" -n 1 $commit)
    
    echo "Commit: $commit"
    echo "Message: $COMMIT_MSG"
    echo "Author: $COMMIT_AUTHOR"
    echo "Date: $COMMIT_DATE"
    echo ""
    
    # Run the coverage analysis (no output file, just console output)
    python3 scripts/cvc5/commit_coverage_analyzer.py $commit --coverage-json $COVERAGE_FILE
    
    echo ""
    echo "----------------------------------------"
    echo ""
done

echo "=========================================="
echo "Analysis complete!"
echo "=========================================="

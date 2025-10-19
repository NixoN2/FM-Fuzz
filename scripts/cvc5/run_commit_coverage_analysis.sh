#!/bin/bash

# Script to run commit coverage analysis for CVC5 commits
# Downloads coverage mapping artifact, gunzips it, and analyzes last N commits

set -e

# Default values
COMMITS_TO_ANALYZE=${1:-10}
PYTHON_SCRIPT=${2:-"$(dirname "$0")/commit_coverage_analyzer.py"}
COVERAGE_FILE=${3:-"coverage_mapping_merged.json"}
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

# Get commits that changed files in src/ folder
echo "Getting commits that changed files in src/ folder..."
COMMITS=$(git log --oneline -50 | while read commit msg; do 
    if git show --name-only $commit | grep -q "^src/"; then 
        echo "$commit"
    fi
done | head -$COMMITS_TO_ANALYZE)

if [ -z "$COMMITS" ]; then
    echo "No commits found that changed src/ files"
    exit 1
fi

echo "Found commits that changed src/ files:"
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
    python3 "$PYTHON_SCRIPT" $commit --coverage-json $COVERAGE_FILE
    
    echo ""
    echo "----------------------------------------"
    echo ""
done

echo "=========================================="
echo "Analysis complete!"
echo "=========================================="

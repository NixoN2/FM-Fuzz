#!/bin/bash
# Extract Z3 build artifacts from artifacts.tar.gz
# This script extracts:
# - All header files to build/ with preserved paths
# - The Z3 binary to build/z3
# - compile_commands.json to build/
#
# Usage: ./extract_build_artifacts.sh <artifact_file> <build_dir> [extract_headers]
# Example: ./extract_build_artifacts.sh artifacts/artifacts.tar.gz z3/build true
#
# If extract_headers is "true" (default), extracts headers. If "false", only extracts binary.

set -e

ARTIFACT_FILE="${1}"
BUILD_DIR="${2:-z3/build}"
EXTRACT_HEADERS="${3:-true}"

if [ -z "$ARTIFACT_FILE" ]; then
    echo "Error: Artifact file not specified"
    exit 1
fi

if [ ! -f "$ARTIFACT_FILE" ]; then
    echo "Error: Artifact file not found: $ARTIFACT_FILE"
    exit 1
fi

echo "📦 Extracting build artifacts from $ARTIFACT_FILE"
echo "   Build directory: $BUILD_DIR"
echo "   Extract headers: $EXTRACT_HEADERS"

mkdir -p "$BUILD_DIR"

# Extract to temp location
TMP_DIR=$(mktemp -d)
trap "rm -rf $TMP_DIR" EXIT

echo "Extracting archive..."
tar -xzf "$ARTIFACT_FILE" -C "$TMP_DIR"

# Extract binary (Z3 binary is in bin/ in artifacts, but goes to build root)
if [ -f "$TMP_DIR/bin/z3" ]; then
    cp "$TMP_DIR/bin/z3" "$BUILD_DIR/z3"
    chmod +x "$BUILD_DIR/z3"
    echo "✓ Binary extracted to $BUILD_DIR/z3"
else
    echo "⚠ Warning: Binary not found in artifacts"
fi

# Extract compile_commands.json
if [ -f "$TMP_DIR/compile_commands.json" ]; then
    cp "$TMP_DIR/compile_commands.json" "$BUILD_DIR/compile_commands.json"
    echo "✓ compile_commands.json extracted"
else
    echo "⚠ Warning: compile_commands.json not found in artifacts"
fi

# Extract headers if requested
if [ "$EXTRACT_HEADERS" = "true" ]; then
    if [ -d "$TMP_DIR/headers" ]; then
        # Move all header directories to build root (preserve structure)
        if [ -d "$TMP_DIR/headers/include" ]; then
            mv "$TMP_DIR/headers/include" "$BUILD_DIR/include"
            echo "✓ Headers extracted: include/"
        fi
        if [ -d "$TMP_DIR/headers/src" ]; then
            mv "$TMP_DIR/headers/src" "$BUILD_DIR/src"
            echo "✓ Headers extracted: src/"
        fi
        
        # Count headers
        HEADER_COUNT=$(find "$BUILD_DIR" -type f \( -name "*.h" -o -name "*.hpp" -o -name "*.hxx" \) 2>/dev/null | wc -l || echo "0")
        echo "  Total headers: $HEADER_COUNT"
    else
        echo "⚠ Warning: headers/ directory not found in artifacts"
    fi
fi

# Verify binary
if [ -f "$BUILD_DIR/z3" ]; then
    "$BUILD_DIR/z3" --version > /dev/null 2>&1 && echo "✓ Binary verified" || echo "⚠ Binary verification failed"
fi

echo "✅ Extraction complete!"


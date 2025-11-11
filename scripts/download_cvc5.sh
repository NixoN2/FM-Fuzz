#!/bin/bash
# Download latest CVC5 binary from GitHub releases
# Usage: ./scripts/download_cvc5.sh [output_dir]

set -e

# Install required tools if not available
if ! command -v curl &> /dev/null; then
    echo "📦 Installing curl..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y curl
    elif command -v yum &> /dev/null; then
        sudo yum install -y curl
    elif command -v brew &> /dev/null; then
        brew install curl
    else
        echo "❌ Cannot install curl automatically. Please install curl manually."
        exit 1
    fi
fi

if ! command -v unzip &> /dev/null; then
    echo "📦 Installing unzip..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update && sudo apt-get install -y unzip
    elif command -v yum &> /dev/null; then
        sudo yum install -y unzip
    elif command -v brew &> /dev/null; then
        brew install unzip
    else
        echo "❌ Cannot install unzip automatically. Please install unzip manually."
        exit 1
    fi
fi

OUTPUT_DIR="${1:-$HOME/.local/bin}"
mkdir -p "$OUTPUT_DIR"

echo "🔍 Finding latest CVC5 release..."

# Get latest release tag
LATEST_TAG=$(curl -s https://api.github.com/repos/cvc5/cvc5/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/')

if [ -z "$LATEST_TAG" ]; then
    echo "❌ Failed to get latest release tag"
    exit 1
fi

echo "📦 Latest release: $LATEST_TAG"

# Get release assets
RELEASE_URL="https://api.github.com/repos/cvc5/cvc5/releases/tags/$LATEST_TAG"
ASSETS=$(curl -s "$RELEASE_URL" | grep '"browser_download_url":' | grep -o 'https://[^"]*')

# Find the Linux x86_64 static binary
CVC5_ASSET=$(echo "$ASSETS" | grep -i "linux.*x86_64.*static" | head -1)

if [ -z "$CVC5_ASSET" ]; then
    echo "❌ Failed to find Linux x86_64 static binary in release $LATEST_TAG"
    echo "Available assets:"
    echo "$ASSETS" | sed 's/^/  /'
    exit 1
fi

echo "📥 Downloading: $CVC5_ASSET"

# Download to temporary file
TEMP_FILE=$(mktemp)
curl -sL "$CVC5_ASSET" -o "$TEMP_FILE"

# Extract ZIP archive
echo "📦 Extracting ZIP archive..."
EXTRACT_DIR=$(mktemp -d)
unzip -q "$TEMP_FILE" -d "$EXTRACT_DIR"

# Find cvc5 binary in extracted files
CVC5_BIN=$(find "$EXTRACT_DIR" -name "cvc5" -type f | head -1)
if [ -z "$CVC5_BIN" ]; then
    echo "❌ cvc5 binary not found in extracted archive"
    rm -f "$TEMP_FILE"
    rm -rf "$EXTRACT_DIR"
    exit 1
fi

# Copy to output directory
cp "$CVC5_BIN" "$OUTPUT_DIR/cvc5"
chmod +x "$OUTPUT_DIR/cvc5"

# Cleanup
rm -f "$TEMP_FILE"
rm -rf "$EXTRACT_DIR"

echo "✅ CVC5 binary installed to: $OUTPUT_DIR/cvc5"
"$OUTPUT_DIR/cvc5" --version


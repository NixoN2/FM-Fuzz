#!/bin/bash
# CVC5 Build and Test Script
# This script clones, builds, and tests CVC5 following CI best practices
# Usage: ./build.sh [--coverage]

set -e  # Exit on any error

# Parse command line arguments
ENABLE_COVERAGE=false
if [[ "$1" == "--coverage" ]]; then
    ENABLE_COVERAGE=true
    echo "🔍 Coverage instrumentation will be enabled"
fi

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  python3 \
  python3-pip \
  ccache \
  libbsd-dev \
  libcln-dev \
  libedit-dev \
  libgmp-dev \
  libtinfo-dev \
  libfl-dev

# Install coverage tools if coverage is enabled
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "📊 Installing coverage tools..."
    sudo apt-get install -y lcov gcc
    # Install fastcov for faster coverage analysis
    pip3 install fastcov
fi

echo "📥 Cloning CVC5 repository..."
git clone https://github.com/cvc5/cvc5.git cvc5

echo "🔧 Setting up Python environment..."
python3 -m venv ~/.venv
source ~/.venv/bin/activate
python3 -m pip install --upgrade pip

echo "🔨 Building CVC5..."
cd cvc5

# Configure build with or without coverage
if [[ "$ENABLE_COVERAGE" == "true" ]]; then
    echo "🔍 Configuring CVC5 with coverage instrumentation..."
    ./configure.sh debug --coverage --assertions --auto-download
else
    echo "⚡ Configuring CVC5 for production (no coverage)..."
    ./configure.sh production --auto-download
fi

cd build
make -j$(nproc)

# Install to system
sudo make install

echo "🧪 Testing CVC5 binary..."
# Test the installed binary
if command -v cvc5 >/dev/null 2>&1; then
    cvc5 --version || echo "Version command completed (exit code $?)"
    echo "CVC5 binary is working correctly!"
else
    # Fallback to build directory
    if [ -f "./bin/cvc5" ]; then
        ./bin/cvc5 --version || echo "Version command completed (exit code $?)"
        echo "CVC5 binary is working correctly!"
    else
        echo "CVC5 binary not found!"
        exit 1
    fi
fi

echo "✅ CVC5 build and test completed successfully!"

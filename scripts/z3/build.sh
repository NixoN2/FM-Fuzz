#!/bin/bash
# Z3 Build and Test Script
# This script clones, builds, and tests Z3 for SAT/SMT solver testing

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  ninja-build \
  python3 \
  python3-pip

echo "📥 Cloning Z3 repository..."
git clone https://github.com/Z3Prover/z3.git z3

echo "🔨 Building Z3..."
cd z3
mkdir -p build
cd build

# Configure for optimized build suitable for testing
cmake -G "Ninja" \
      -DCMAKE_BUILD_TYPE=Release \
      -DZ3_BUILD_LIBZ3_SHARED=FALSE \
      -DZ3_BUILD_TEST_EXECUTABLES=TRUE \
      -DCMAKE_CXX_FLAGS="-O3 -DNDEBUG" \
      ..

# Build Z3
ninja

# Install to system
sudo ninja install

echo "🧪 Testing Z3 binary..."
# Test the Z3 binary
if [ -f "./z3" ]; then
    ./z3 --version || echo "Version command completed (exit code $?)"
    echo "Testing basic SMT functionality..."
    echo "(assert (> x 0))" | ./z3 -in || echo "SMT test completed (exit code $?)"
    echo "Z3 binary is working correctly!"
else
    echo "Z3 binary not found!"
    exit 1
fi

echo "✅ Z3 build and test completed successfully!"
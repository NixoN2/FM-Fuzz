#!/bin/bash
# SMT-RAT Build and Test Script
# This script clones, builds, and tests SMT-RAT following the CI configuration

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  libgmp-dev \
  libboost-all-dev \
  libeigen3-dev \
  libreadline-dev \
  libgtest-dev \
  clang

echo "📥 Cloning SMT-RAT repository..."
git clone https://github.com/ths-rwth/smtrat.git smtrat

echo "🔨 Building SMT-RAT..."
cd smtrat
mkdir -p build
cd build

# Configure with release build type
cmake -DCMAKE_BUILD_TYPE=Release ..

# Build all targets (including smtrat-static)
cmake --build . --config Release --target all -j$(nproc)

# Install to system
sudo cmake --install .

echo "🧪 Testing SMT-RAT binary..."
# Try different possible binary names
if [ -f "./smtrat-static" ]; then
    ./smtrat-static --version
elif [ -f "./smtrat" ]; then
    ./smtrat --version
elif [ -f "../smtrat-static" ]; then
    ../smtrat-static --version
elif [ -f "../smtrat" ]; then
    ../smtrat --version
else
    echo "Binary not found in expected locations, checking build directory..."
    find . -name "smtrat*" -type f -executable
    # Try to run the first executable found
    BINARY=$(find . -name "smtrat*" -type f -executable | head -1)
    if [ -n "$BINARY" ]; then
        echo "Found binary: $BINARY"
        $BINARY --version
    else
        echo "No SMT-RAT binary found!"
        exit 1
    fi
fi

echo "✅ SMT-RAT build and test completed successfully!"

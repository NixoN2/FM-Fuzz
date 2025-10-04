#!/bin/bash
# STP Build and Test Script
# This script clones, builds, and tests STP

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  bison \
  build-essential \
  cmake \
  flex \
  git \
  libboost-program-options-dev \
  ninja-build \
  python3 \
  python3-pip \
  python3-setuptools \
  zlib1g-dev
sudo pip3 install -U lit

echo "📥 Cloning STP repository..."
git clone --recurse-submodules https://github.com/stp/stp.git stp

echo "🔧 Setting up STP dependencies..."
cd stp
./scripts/deps/setup-minisat.sh
./scripts/deps/setup-cms.sh
./scripts/deps/setup-gtest.sh
./scripts/deps/setup-outputcheck.sh

echo "🔨 Building STP..."
mkdir build
cd build
cmake -DNOCRYPTOMINISAT:BOOL=OFF -DENABLE_TESTING:BOOL=ON -DPYTHON_EXECUTABLE:PATH="$(which python3)" -G Ninja ..
cmake --build . --parallel $(nproc)

echo "📦 Installing STP..."
sudo cmake --install .

echo "🔧 Updating library cache..."
sudo ldconfig

echo "🔍 Debugging information..."
echo "Current LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
echo "Checking for STP libraries in /usr/local/lib:"
ls -la /usr/local/lib/libstp* /usr/local/lib/libcadi* 2>/dev/null || echo "Some STP libraries not found in /usr/local/lib"
echo "Checking for STP binaries:"
ls -la /usr/local/bin/stp* 2>/dev/null || echo "STP binaries not found in /usr/local/bin"
echo "Library dependencies for stp binary:"
ldd /usr/local/bin/stp 2>/dev/null || echo "Could not check dependencies for stp binary"

echo "🧪 Testing STP binary..."
# Set LD_LIBRARY_PATH to ensure shared libraries are found
export LD_LIBRARY_PATH="/usr/local/lib:$LD_LIBRARY_PATH"
echo "Updated LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
stp --version

echo "✅ STP build and test completed successfully!"

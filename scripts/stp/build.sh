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

echo "🧪 Testing STP binary..."
stp --version

echo "✅ STP build and test completed successfully!"

#!/bin/bash
# Q3B Build and Test Script
# This script clones, builds, and tests Q3B

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  python3

echo "📥 Cloning Q3B repository..."
git clone https://github.com/martinjonas/Q3B.git q3b

echo "🔧 Setting up Q3B dependencies..."
cd q3b
bash contrib/get_deps.sh

echo "🔨 Building Q3B..."
cmake -S . -B build
cmake --build build -j$(nproc)

echo "🧪 Testing Q3B binary..."
./build/q3b --version

echo "✅ Q3B build and test completed successfully!"

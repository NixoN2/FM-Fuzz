#!/bin/bash
# CVC5 Build and Test Script
# This script clones, builds, and tests CVC5

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3

echo "📥 Cloning CVC5 repository..."
git clone https://github.com/cvc5/cvc5.git cvc5

echo "🔨 Building CVC5..."
cd cvc5
./configure.sh debug --auto-download
cd build
make -j$(nproc)

echo "🧪 Testing CVC5 binary..."
cd ..
./build/bin/cvc5 --version

echo "✅ CVC5 build and test completed successfully!"

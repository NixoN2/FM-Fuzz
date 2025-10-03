#!/bin/bash
# Z3 Build and Test Script
# This script clones, builds, and tests Z3

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3

echo "📥 Cloning Z3 repository..."
git clone https://github.com/Z3Prover/z3.git z3

echo "🔨 Building Z3..."
cd z3
python scripts/mk_make.py
cd build
make -j$(nproc)

echo "🧪 Testing Z3 binary..."
cd ..
./build/z3 --version

echo "✅ Z3 build and test completed successfully!"

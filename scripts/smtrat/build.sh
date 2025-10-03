#!/bin/bash
# SMT-RAT Build and Test Script
# This script clones, builds, and tests SMT-RAT

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3 libboost-all-dev libgmp-dev libgtest-dev

echo "📥 Cloning SMT-RAT repository..."
git clone https://github.com/ths-rwth/smtrat.git smtrat

echo "🔨 Building SMT-RAT..."
cd smtrat
mkdir build
cd build
cmake ..
make -j$(nproc) smtrat

echo "🧪 Testing SMT-RAT binary..."
./smtrat --version

echo "✅ SMT-RAT build and test completed successfully!"

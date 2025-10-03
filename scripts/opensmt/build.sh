#!/bin/bash
# OpenSMT Build and Test Script
# This script clones, builds, and tests OpenSMT

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y build-essential cmake git python3 libgmp-dev libedit-dev flex bison

echo "📥 Cloning OpenSMT repository..."
git clone https://github.com/usi-verification-and-security/opensmt.git opensmt

echo "🔨 Building OpenSMT..."
cd opensmt
make -j$(nproc)

echo "🧪 Testing OpenSMT binary..."
./build/opensmt --version

echo "✅ OpenSMT build and test completed successfully!"

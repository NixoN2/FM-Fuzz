#!/bin/bash
# Bitwuzla Build and Test Script
# This script clones, builds, and tests Bitwuzla

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  libgmp-dev \
  meson \
  ninja-build \
  python3 \
  python3-pip

echo "📥 Cloning Bitwuzla repository..."
git clone https://github.com/bitwuzla/bitwuzla.git bitwuzla

echo "🔨 Building Bitwuzla..."
cd bitwuzla
./configure.py
cd build
ninja

echo "🧪 Testing Bitwuzla binary..."
./src/main/bitwuzla --version

echo "✅ Bitwuzla build and test completed successfully!"

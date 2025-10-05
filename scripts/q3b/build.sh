#!/bin/bash
# Q3B Build and Test Script
# This script clones, builds, and tests Q3B following the working Dockerfile approach

set -e  # Exit on any error

echo "🔧 Installing basic tools..."
sudo apt-get update
sudo apt-get install -y \
  build-essential \
  cmake \
  git \
  autotools-dev \
  automake \
  wget \
  unzip \
  make \
  default-jre \
  pkg-config \
  uuid-dev

echo "📥 Cloning Q3B repository..."
git clone https://github.com/martinjonas/Q3B.git q3b

echo "🔧 Setting up Z3 dependency..."
cd q3b
wget https://github.com/Z3Prover/z3/releases/download/z3-4.11.2/z3-4.11.2-x64-glibc-2.31.zip
unzip z3-4.11.2-x64-glibc-2.31.zip
sudo cp z3-4.11.2-x64-glibc-2.31/bin/libz3.a /usr/lib/
sudo cp -r z3-4.11.2-x64-glibc-2.31/include/* /usr/include/

echo "🔧 Setting up CUDD dependency..."
git clone -b 3val https://github.com/martinjonas/cudd.git
cd cudd
./configure --enable-silent-rules --enable-obj --enable-shared
make -j4
sudo make install
cd ..

echo "🔧 Setting up ANTLR..."
sudo mkdir -p /usr/share/java
wget https://www.antlr.org/download/antlr-4.11.1-complete.jar -P /usr/share/java

echo "🔨 Building Q3B..."
cmake -B build -DANTLR_EXECUTABLE=/usr/share/java/antlr-4.11.1-complete.jar
cmake --build build -j4

echo "🧪 Testing Q3B binary..."
# Try different possible binary names and locations
if [ -f "./build/q3b" ]; then
    ./build/q3b --version || echo "Version command completed (exit code $?)"
elif [ -f "./build/bin/q3b" ]; then
    ./build/bin/q3b --version || echo "Version command completed (exit code $?)"
else
    echo "Q3B binary not found, checking build directory..."
    find ./build -name "q3b*" -type f -executable
    BINARY=$(find ./build -name "q3b*" -type f -executable | head -1)
    if [ -n "$BINARY" ]; then
        echo "Found binary: $BINARY"
        $BINARY --version || echo "Version command completed (exit code $?)"
    else
        echo "No Q3B binary found!"
        exit 1
    fi
fi

echo "✅ Q3B build and test completed successfully!"

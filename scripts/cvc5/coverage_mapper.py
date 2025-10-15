#!/usr/bin/env python3
"""
Coverage Mapper for cvc5
Processes tests using ctest and extracts coverage data using fastcov.
"""

import os
import sys
import json
import subprocess
import re
import argparse
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

class CoverageMapper:
    def __init__(self, build_dir: str = "build"):
        self.build_dir = Path(build_dir)
        # Pre-compile regex for better performance
        self.test_regex = re.compile(r'Test\s+#(\d+):\s*(.+)')
        # Cache for demangled names to avoid repeated subprocess calls
        self.demangle_cache = {}

    def demangle_function_name(self, mangled_name: str) -> str:
        """Demangle C++ function names using c++filt with caching"""
        if mangled_name in self.demangle_cache:
            return self.demangle_cache[mangled_name]
        
        try:
            result = subprocess.run(['c++filt', mangled_name], capture_output=True, text=True)
            demangled = result.stdout.strip() if result.returncode == 0 else mangled_name
            self.demangle_cache[mangled_name] = demangled
            return demangled
        except FileNotFoundError:
            self.demangle_cache[mangled_name] = mangled_name
            return mangled_name
    
    def simplify_file_path(self, file_path: str) -> str:
        """Simplify file path to show only the relevant project path starting from src/"""
        # Always look for 'src/' directory and start from there
        if '/src/' in file_path:
            parts = file_path.split('/src/')
            if len(parts) > 1:
                return 'src/' + parts[1]
        
        # Fallback: return the original path
        return file_path

    def get_ctest_tests(self) -> List[Tuple[int, str]]:
        """Get list of tests from ctest --show-only"""
        try:
            result = subprocess.run(["ctest", "--show-only"], cwd=self.build_dir, 
                                  capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Error running ctest --show-only: {result.stderr}")
                return []
            
            tests = []
            for line in result.stdout.split('\n'):
                match = self.test_regex.match(line.strip())
                if match:
                    tests.append((int(match.group(1)), match.group(2)))
            
            print(f"Found {len(tests)} tests")
            return tests
            
        except Exception as e:
            print(f"Error getting ctest tests: {e}")
            return []

    def process_single_test(self, test_info: Tuple[int, str]) -> Optional[Dict]:
        """Process a single test using ctest and extract coverage data"""
        test_id, test_name = test_info
        
        # Clear existing .gcda files before running test
        for gcda in self.build_dir.rglob("*.gcda"):
            gcda.unlink()
        
        # Reset coverage counters
        self.reset_coverage_counters()
        
        # Measure test execution time
        start_time = time.time()
        
        # Run the test using ctest with parallel execution
        result = subprocess.run(["ctest", "-I", f"{test_id},{test_id}", "-j4", "--output-on-failure"], 
                          cwd=self.build_dir, capture_output=True, text=True, check=False)
        
        end_time = time.time()
        execution_time = round(end_time - start_time, 2)
        
        if result.returncode != 0:
            print(f"❌ {test_name} - {execution_time}s")
            return None
        
        # Extract coverage data
        coverage_data = self.extract_coverage_data(test_name)
        
        if coverage_data:
            print(f"✅ {test_name} - {len(coverage_data['functions'])} functions - {execution_time}s")
        else:
            print(f"❌ {test_name} - {execution_time}s")
        
        return coverage_data

    def extract_coverage_data(self, test_name: str) -> Optional[Dict]:
        """Extract coverage data using fastcov"""
        fastcov_output = self.build_dir / f"fastcov_{test_name.replace('/', '_')}.json"
        
        # Run fastcov with optimized settings
        result = subprocess.run([
            "fastcov", "--gcov", "gcov", "--search-directory", str(self.build_dir),
            "--output", str(fastcov_output), "--exclude", "/usr/include/*",
            "--exclude", "*/deps/*", "--jobs", "4"
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            return None
        
        return self.parse_fastcov_json(fastcov_output, test_name)

    def parse_fastcov_json(self, fastcov_file: Path, test_name: str) -> Optional[Dict]:
        """Parse fastcov JSON file to extract function information"""
        with open(fastcov_file, 'r') as f:
            data = json.load(f)
        
        functions = set()
        
        if 'sources' in data:
            for file_path, file_data in data['sources'].items():
                if self.is_cvc5_source_file(file_path):
                    if '' in file_data and 'functions' in file_data['']:
                        for func_name, func_data in file_data['']['functions'].items():
                            if func_data.get('execution_count', 0) > 0:
                                demangled_name = self.demangle_function_name(func_name)
                                simplified_path = self.simplify_file_path(file_path)
                                line_num = func_data.get('start_line', 0)
                                func_id = f"{simplified_path}:{demangled_name}:{line_num}"
                                functions.add(func_id)
        
        if not functions:
            return None
        
        return {
            "test_name": test_name,
            "functions": sorted(list(functions))
        }

    def is_cvc5_source_file(self, file_path: str) -> bool:
        """Check if a file path belongs to the cvc5 project"""
        # Check if it's a cvc5 source file by looking for 'src/' directory
        has_src_dir = 'src/' in file_path
        
        # Exclude system and build directories
        excluded_patterns = [
            '/usr/include/', '/usr/lib/', '/System/', '/Library/',
            '/Applications/', '/opt/', '/deps/', '/build/deps/',
            '/build/src/', '/build/', '/include/', '/lib/', 
            '/bin/', '/share/', 'CMakeFiles/', 'cmake/', 'Makefile'
        ]
        
        has_excluded_pattern = any(exclude in file_path for exclude in excluded_patterns)
        
        return has_src_dir and not has_excluded_pattern

    def reset_coverage_counters(self):
        """Reset coverage counters using fastcov --zerocounters"""
        subprocess.run([
            "fastcov", "--zerocounters", "--search-directory", str(self.build_dir),
            "--exclude", "/usr/include/*", "--exclude", "*/deps/*"
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)

    def process_tests(self, tests: List[Tuple[int, str]], max_tests: int = None) -> List[Dict]:
        """Process tests sequentially with optimizations"""
        if max_tests:
            tests = tests[:max_tests]
        
        print(f"🚀 Processing {len(tests)} tests")
        
        results = []
        for i, test_info in enumerate(tests, 1):
            test_id, test_name = test_info
            print(f"Test {i}/{len(tests)} (ctest #{test_id}): {test_name}")
            
            result = self.process_single_test(test_info)
            if result:
                results.append(result)
        
        return results

    def generate_coverage_mapping(self, results: List[Dict]) -> Dict:
        """Generate the final coverage mapping from test results"""
        print("📊 Generating coverage mapping...")
        
        function_to_tests = {}
        for result in results:
            test_name = result["test_name"]
            for func in result["functions"]:
                function_to_tests.setdefault(func, []).append(test_name)
        
        return function_to_tests

    def run(self, max_tests: int = None, test_pattern: str = None, start_index: int = None, end_index: int = None):
        """Main execution method"""
        print("🔍 Discovering tests...")
        tests = self.get_ctest_tests()
        
        if not tests:
            print("❌ No tests found")
            return
        
        if test_pattern:
            tests = [t for t in tests if test_pattern in t[1]]
            print(f"🔍 Filtered to {len(tests)} tests matching pattern: {test_pattern}")
        
        # Handle test range selection (1-based indexing to match ctest)
        if start_index is not None and end_index is not None:
            # Convert 1-based to 0-based for slicing
            start_idx = max(0, start_index - 1)
            end_idx = min(len(tests), end_index)
            tests = tests[start_idx:end_idx]
            print(f"🔍 Selected tests {start_index}-{end_index}: {len(tests)} tests")
        elif max_tests:
            tests = tests[:max_tests]
            print(f"🔍 Limited to {len(tests)} tests")
        
        # Process tests
        results = self.process_tests(tests, max_tests)
        
        if not results:
            print("❌ No coverage data generated")
            return
        
        print(f"✅ Successfully processed {len(results)}/{len(tests)} tests")
        
        # Generate final mapping
        coverage_mapping = self.generate_coverage_mapping(results)
        
        # Save to file (optimized for size)
        output_file = f"coverage_mapping_{start_index}_{end_index}.json" if start_index is not None else "coverage_mapping.json"
        with open(output_file, 'w') as f:
            json.dump(coverage_mapping, f, separators=(',', ':'))
        
        print(f"📄 Coverage mapping saved to {output_file}")
        print(f"📊 Total functions: {len(coverage_mapping)}")
        print(f"📊 Total tests: {len(results)}")

def main():
    parser = argparse.ArgumentParser(description='Coverage Mapper for cvc5')
    parser.add_argument('--build-dir', default='build', help='Build directory path')
    parser.add_argument('--max-tests', type=int, help='Maximum number of tests to process')
    parser.add_argument('--test-pattern', help='Filter tests by pattern')
    parser.add_argument('--start-index', type=int, help='Start index for test range (1-based, matches ctest numbering)')
    parser.add_argument('--end-index', type=int, help='End index for test range (1-based, inclusive)')
    
    args = parser.parse_args()
    
    mapper = CoverageMapper(args.build_dir)
    mapper.run(max_tests=args.max_tests, test_pattern=args.test_pattern, 
               start_index=args.start_index, end_index=args.end_index)

if __name__ == "__main__":
    main()
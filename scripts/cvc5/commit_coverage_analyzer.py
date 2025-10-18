#!/usr/bin/env python3
"""
Commit Coverage Analyzer
Gets changed functions from a commit and finds tests that cover those functions.
"""

import json
import sys
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Optional
import argparse
import gc
import git
from unidiff import PatchSet
import re

try:
    import clang.cindex
    CLANG_AVAILABLE = True
except ImportError:
    CLANG_AVAILABLE = False
    print("Warning: clang.cindex not available. Install with: pip install libclang")

class CommitCoverageAnalyzer:
    def __init__(self, repo_path: str = "."):
        """Initialize with repository path."""
        self.repo_path = Path(repo_path)
        self.repo = git.Repo(repo_path)
        self.coverage_map = None
    
    def get_commit_info(self, commit_hash: str) -> Optional[Dict]:
        """Get basic commit information"""
        try:
            commit = self.repo.commit(commit_hash)
            return {
                'hash': commit.hexsha,
                'author_name': commit.author.name,
                'author_email': commit.author.email,
                'date': commit.authored_datetime.isoformat(),
                'message': commit.message.strip(),
                'summary': commit.summary
            }
        except Exception as e:
            print(f"Error getting commit info: {e}")
            return None
    
    def get_commit_diff(self, commit_hash: str) -> str:
        """Get the diff for a commit"""
        try:
            commit = self.repo.commit(commit_hash)
            if len(commit.parents) > 0:
                parent = commit.parents[0]
                result = subprocess.run(['git', 'show', commit_hash], 
                                      capture_output=True, text=True, cwd=self.repo_path)
                return result.stdout
            else:
                result = subprocess.run(['git', 'show', commit_hash], 
                                      capture_output=True, text=True, cwd=self.repo_path)
                return result.stdout
        except Exception as e:
            print(f"Error getting commit diff: {e}")
            return ""
    
    def get_changed_lines(self, diff_text: str) -> Dict[str, Set[int]]:
        """Extract changed line numbers from diff text for each file"""
        changed_lines = {}
        current_file = None
        
        for line in diff_text.split('\n'):
            if line.startswith('+++ b/'):
                current_file = line[6:]
                if current_file not in changed_lines:
                    changed_lines[current_file] = set()
            elif line.startswith('@@'):
                match = re.search(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
                if match and current_file:
                    start_line = int(match.group(1))
                    line_count = int(match.group(2) or 1)
                    for i in range(start_line, start_line + line_count):
                        changed_lines[current_file].add(i)
        
        return changed_lines
    
    def extract_functions_with_clang(self, file_path: str) -> List[Dict]:
        """Extract function signatures using clang AST parsing"""
        if not CLANG_AVAILABLE:
            return []
        
        try:
            index = clang.cindex.Index.create()
            
            # Comprehensive flags based on CVC5's actual build configuration
            args = [
                # Force C++ mode for header files
                '-x', 'c++',
                # C++ standard
                '-std=c++17',
                
                # Include paths (critical for CVC5)
                '-I./include',                    # Public headers
                '-I./build/include',              # Generated headers
                '-I./src/include',                # Internal headers
                '-I./src/.',                      # Source directory
                '-I./build/src',                  # Build source directory
                '-isystem', './build/deps/include',  # Dependencies
                
                # System include paths for Linux
                '-I/usr/include',
                '-I/usr/include/c++/11',
                '-I/usr/include/x86_64-linux-gnu/c++/11',
                '-I/usr/include/c++/11/x86_64-linux-gnu',
                '-I/usr/local/include',
                
                # CVC5-specific preprocessor definitions
                '-DCVC5_ASSERTIONS',
                '-DCVC5_DEBUG', 
                '-DCVC5_STATISTICS_ON',
                '-DCVC5_TRACING',
                '-DCVC5_USE_POLY',
                '-D__BUILDING_CVC5LIB',
                '-Dcvc5_obj_EXPORTS',
                
                # Compiler flags used by CVC5
                '-Wall',
                '-Wsuggest-override',
                '-Wnon-virtual-dtor', 
                '-Wimplicit-fallthrough',
                '-Wshadow',
                '-fno-operator-names',
                '-fno-extern-tls-init',
                '-Wno-deprecated-declarations',
                '-Wno-error=deprecated-declarations',
                '-fPIC',
                '-fvisibility=default',
                
                # Additional flags for better parsing
                '-fparse-all-comments',
                '-Wno-unknown-pragmas',
                '-Wno-unused-parameter',
                '-Wno-unused-variable',
                '-Wno-unused-function'
            ]
            
            tu = index.parse(file_path, args=args)
            
            functions = []
            
            def visit_node(node):
                if node.kind in [clang.cindex.CursorKind.FUNCTION_DECL, 
                               clang.cindex.CursorKind.CXX_METHOD]:
                    signature = self.get_function_signature(node)
                    if signature and self.is_cvc5_function(signature):
                        functions.append({
                            'signature': signature,
                            'line': node.location.line,
                            'file': file_path
                        })
                
                for child in node.get_children():
                    visit_node(child)
            
            visit_node(tu.cursor)
            return functions
            
        except Exception as e:
            print(f"Warning: Could not parse {file_path} with clang: {e}")
            return []
    
    def get_function_signature(self, cursor) -> Optional[str]:
        """Extract gcov-style function signature from a clang cursor"""
        try:
            name = cursor.spelling
            if not name:
                return None

            qualified_name = self.get_qualified_name(cursor)
            params = []
            
            for child in cursor.get_children():
                if child.kind == clang.cindex.CursorKind.PARM_DECL:
                    t = child.type
                    param_type = t.get_canonical().spelling
                    
                    if t.is_const_qualified():
                        param_type = param_type.replace("const ", "")
                        if "&" in param_type:
                            param_type = param_type.replace("&", " const&")
                        elif "*" in param_type:
                            param_type = param_type.replace("*", " const*")
                        else:
                            param_type = param_type + " const"
                    
                    param_type = param_type.replace(" &", "&").replace(" *", "*")
                    params.append(param_type)

            param_str = ", ".join(params)
            const_suffix = " const" if cursor.is_const_method() else ""
            line = cursor.location.line

            signature = f"{qualified_name}({param_str}){const_suffix}:{line}"
            return signature
        except Exception as e:
            return None
    
    def get_qualified_name(self, cursor) -> str:
        """Get the fully qualified name including namespace and class"""
        parts = []
        current = cursor
        
        while current:
            if current.kind in [clang.cindex.CursorKind.NAMESPACE, 
                              clang.cindex.CursorKind.CLASS_DECL,
                              clang.cindex.CursorKind.STRUCT_DECL,
                              clang.cindex.CursorKind.FUNCTION_DECL,
                              clang.cindex.CursorKind.CXX_METHOD]:
                name = current.spelling
                if name:
                    parts.append(name)
            current = current.semantic_parent
        
        parts.reverse()
        return "::".join(parts)
    
    def is_cvc5_function(self, signature: str) -> bool:
        """Check if a function signature belongs to cvc5"""
        if 'std::' in signature or signature.startswith('__') or '__gnu_cxx::' in signature:
            return False
        
        func_name = signature.split('(')[0].split('::')[-1]
        
        if 'cvc5::' in signature:
            return True
        
        if '<' in signature and '>' in signature:
            return False
        
        c_lib_prefixes = ['wcs', 'isw', 'tow', 'wct', 'sched_', 'clock_', 'time', 'atof', 'atoi', 'atol', 'select', 'alloca']
        if any(func_name.startswith(prefix) for prefix in c_lib_prefixes):
            return False
        
        if len(func_name) <= 3 and func_name.islower():
            return False
        
        if func_name.islower() and not any(c.isupper() for c in func_name):
            if '::' in signature and not signature.startswith('std::'):
                return True
            return False
        
        return True
    
    def get_commit_functions(self, commit_hash: str) -> List[str]:
        """Get changed functions from a commit"""
        print(f"Analyzing commit {commit_hash}...")
        
        # Get commit info
        commit_info = self.get_commit_info(commit_hash)
        if not commit_info:
            return []
        
        print(f"Commit: {commit_info['summary']}")
        print(f"Author: {commit_info['author_name']}")
        
        # Get diff and changed lines
        diff_text = self.get_commit_diff(commit_hash)
        changed_files_lines = self.get_changed_lines(diff_text)
        
        changed_functions = []
        
        for file_path, changed_lines in changed_files_lines.items():
            if not file_path.endswith(('.cpp', '.cc', '.c', '.h', '.hpp')):
                continue
            
            full_path = self.repo_path / file_path
            if not full_path.exists():
                continue
            
            print(f"  Analyzing {file_path} (changed lines: {sorted(list(changed_lines))[:10]}{'...' if len(changed_lines) > 10 else ''})")
            
            # Extract functions
            functions = self.extract_functions_with_clang(str(full_path))
            
            # Find functions near changed lines
            for func in functions:
                func_line = func['line']
                if any(abs(func_line - changed_line) <= 20 for changed_line in changed_lines):
                    mapping_entry = f"{file_path}:{func['signature']}"
                    changed_functions.append(mapping_entry)
                    print(f"    Found: {mapping_entry}")
        
        print(f"Found {len(changed_functions)} changed functions")
        return changed_functions
    
    def load_coverage_mapping(self, coverage_json_path: str):
        """Load coverage mapping only when needed."""
        print(f"Loading coverage mapping from {coverage_json_path}...")
        with open(coverage_json_path, 'r') as f:
            self.coverage_map = json.load(f)
        print(f"Loaded coverage mapping with {len(self.coverage_map)} functions")
    
    def find_tests_for_functions(self, functions: List[str]) -> Dict:
        """Find unique tests that cover the given functions."""
        if not self.coverage_map:
            print("Error: Coverage mapping not loaded")
            return {
                'all_covering_tests': set(),
                'functions_with_tests': 0,
                'functions_without_tests': 0,
                'total_tests': 0,
                'function_test_counts': {},
                'test_function_counts': {},
                'direct_matches': 0,
                'path_removed_matches': 0
            }
        
        print(f"Finding tests for {len(functions)} functions...")
        
        all_covering_tests = set()
        functions_with_tests = 0
        functions_without_tests = 0
        function_test_counts = {}
        test_function_counts = {}
        direct_matches = 0
        path_removed_matches = 0
        
        for func in functions:
            matching_tests = set()
            match_type = "none"
            
            # Strategy 1: Try direct match first
            if func in self.coverage_map:
                tests = self.coverage_map[func]
                matching_tests.update(tests)
                match_type = "direct"
                direct_matches += 1
            else:
                # Strategy 2: Try matching without path (remove file path from our function)
                if ':' in func:
                    # Extract just the function signature part (everything after the first colon)
                    func_signature = ':'.join(func.split(':')[1:])
                    
                    # Look for coverage entries that match this function signature
                    for coverage_key, tests in self.coverage_map.items():
                        if ':' in coverage_key:
                            # Extract function signature from coverage key (everything after first colon, before last colon)
                            coverage_parts = coverage_key.split(':')
                            if len(coverage_parts) >= 3:
                                coverage_func = ':'.join(coverage_parts[1:-1])  # Everything except first (path) and last (line)
                                
                                if func_signature == coverage_func:
                                    matching_tests.update(tests)
                                    match_type = "path_removed"
                                    path_removed_matches += 1
                                    break  # Found match without path
            
            if matching_tests:
                all_covering_tests.update(matching_tests)
                functions_with_tests += 1
                function_test_counts[func] = len(matching_tests)
                
                # Count how many functions each test covers
                for test in matching_tests:
                    if test not in test_function_counts:
                        test_function_counts[test] = 0
                    test_function_counts[test] += 1
                
                print(f"  ✓ {func} ({match_type}): {len(matching_tests)} tests")
            else:
                functions_without_tests += 1
                function_test_counts[func] = 0
                print(f"  ✗ {func}: No tests found")
        
        print(f"\nMATCHING STATISTICS:")
        print(f"Functions with test coverage: {functions_with_tests}/{len(functions)}")
        print(f"Functions without test coverage: {functions_without_tests}/{len(functions)}")
        print(f"Direct matches: {direct_matches}")
        print(f"Path-removed matches: {path_removed_matches}")
        print(f"Total unique tests found: {len(all_covering_tests)}")
        
        return {
            'all_covering_tests': all_covering_tests,
            'functions_with_tests': functions_with_tests,
            'functions_without_tests': functions_without_tests,
            'total_tests': len(all_covering_tests),
            'function_test_counts': function_test_counts,
            'test_function_counts': test_function_counts,
            'direct_matches': direct_matches,
            'path_removed_matches': path_removed_matches
        }
    
    def normalize_function_signature(self, func_sig: str) -> str:
        """Normalize function signature by standardizing const placement."""
        # Replace "const Type&" with "Type const&" and "const Type*" with "Type const*"
        import re
        
        # Pattern to match "const " followed by a type name
        # This handles cases like "const cvc5::internal::LogicInfo&" -> "cvc5::internal::LogicInfo const&"
        pattern = r'const\s+([a-zA-Z_][a-zA-Z0-9_:]*\s*[&\*])'
        
        def replace_const(match):
            type_part = match.group(1)
            if '&' in type_part:
                return type_part.replace('&', ' const&')
            elif '*' in type_part:
                return type_part.replace('*', ' const*')
            return type_part + ' const'
        
        normalized = re.sub(pattern, replace_const, func_sig)
        return normalized
    
    def cleanup_coverage_mapping(self):
        """Clean up coverage mapping from memory."""
        print("Cleaning up coverage mapping from memory...")
        self.coverage_map = None
        gc.collect()
        print("Memory cleaned up")
    
    def analyze_commit_coverage(self, commit_hash: str, coverage_json_path: str) -> Dict:
        """Complete analysis: get functions from commit and find covering tests."""
        print(f"\n{'='*60}")
        print(f"COMMIT COVERAGE ANALYSIS")
        print(f"{'='*60}")
        
        # Step 1: Get changed functions from commit
        changed_functions = self.get_commit_functions(commit_hash)
        
        if not changed_functions:
            print("No functions found in commit")
            return {
                'commit': commit_hash,
                'changed_functions': [],
                'covering_tests': [],
                'summary': {
                    'total_functions': 0,
                    'functions_with_tests': 0,
                    'total_covering_tests': 0,
                    'coverage_percentage': 0
                }
            }
        
        # Step 2: Load coverage mapping and find tests
        self.load_coverage_mapping(coverage_json_path)
        test_results = self.find_tests_for_functions(changed_functions)
        
        # Step 3: Clean up memory
        self.cleanup_coverage_mapping()
        
        # Step 4: Generate detailed statistics
        summary = {
            'total_functions': len(changed_functions),
            'functions_with_tests': test_results['functions_with_tests'],
            'functions_without_tests': test_results['functions_without_tests'],
            'total_covering_tests': test_results['total_tests'],
            'coverage_percentage': (test_results['functions_with_tests'] / len(changed_functions) * 100) if changed_functions else 0
        }
        
        print(f"\n{'='*60}")
        print(f"COVERAGE SUMMARY")
        print(f"{'='*60}")
        print(f"Total functions changed: {summary['total_functions']}")
        print(f"Functions with test coverage: {summary['functions_with_tests']}")
        print(f"Functions without test coverage: {summary['functions_without_tests']}")
        print(f"Coverage percentage: {summary['coverage_percentage']:.1f}%")
        print(f"Total unique tests covering changes: {summary['total_covering_tests']}")
        
        # Summary statistics only
        print(f"\n{'='*60}")
        print(f"SUMMARY STATISTICS")
        print(f"{'='*60}")
        
        # Show only counts, not individual mappings
        function_test_counts = test_results['function_test_counts']
        if function_test_counts:
            functions_with_tests = sum(1 for count in function_test_counts.values() if count > 0)
            functions_without_tests = sum(1 for count in function_test_counts.values() if count == 0)
            print(f"Functions with tests: {functions_with_tests}")
            print(f"Functions without tests: {functions_without_tests}")
        
        test_function_counts = test_results['test_function_counts']
        if test_function_counts:
            total_tests = len(test_function_counts)
            print(f"Total unique tests: {total_tests}")
        
        # Show some covering tests
        covering_tests = test_results['all_covering_tests']
        if covering_tests:
            print(f"\nSample covering tests (showing first 10):")
            for i, test in enumerate(sorted(covering_tests)[:10], 1):
                print(f"  {i:2d}. {test}")
            if len(covering_tests) > 10:
                print(f"  ... and {len(covering_tests) - 10} more tests")
        
        return {
            'commit': commit_hash,
            'changed_functions': changed_functions,
            'covering_tests': sorted(list(covering_tests)),
            'summary': summary
        }
    
def main():
    parser = argparse.ArgumentParser(description='Analyze commit coverage using coverage mapping')
    parser.add_argument('commit', help='Commit hash to analyze')
    parser.add_argument('--coverage-json', default='coverage_mapping_merged.json', 
                       help='Path to coverage mapping JSON file')
    
    args = parser.parse_args()
    
    # Check if coverage JSON exists
    if not os.path.exists(args.coverage_json):
        print(f"Error: Coverage JSON file not found: {args.coverage_json}")
        sys.exit(1)
    
    # Initialize analyzer
    analyzer = CommitCoverageAnalyzer(".")
    
    # Analyze commit coverage (output to console only)
    analyzer.analyze_commit_coverage(args.commit, args.coverage_json)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

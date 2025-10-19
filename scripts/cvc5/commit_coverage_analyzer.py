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
        """Get the unified diff for a commit with zero context for precise line tracking."""
        try:
            commit = self.repo.commit(commit_hash)
            if len(commit.parents) > 0:
                parent = commit.parents[0]
                result = subprocess.run(['git', 'show', '-U0', '--no-color', commit_hash], 
                                      capture_output=True, text=True, cwd=self.repo_path)
                return result.stdout
            else:
                result = subprocess.run(['git', 'show', '-U0', '--no-color', commit_hash], 
                                      capture_output=True, text=True, cwd=self.repo_path)
                return result.stdout
        except Exception as e:
            print(f"Error getting commit diff: {e}")
            return ""
    
    def get_changed_lines(self, diff_text: str) -> Dict[str, Set[int]]:
        """Extract precise changed new-file line numbers per file from a -U0 diff.
        Tracks only '+' lines (added/modified) and maps them to new file line numbers.
        """
        changed_lines: Dict[str, Set[int]] = {}
        current_file: Optional[str] = None
        in_hunk = False
        new_line = None

        lines = diff_text.split('\n')
        for raw in lines:
            if raw.startswith('diff --git '):
                current_file = None
                in_hunk = False
                new_line = None
                continue
            if raw.startswith('+++ b/'):
                current_file = raw[6:]
                if current_file not in changed_lines:
                    changed_lines[current_file] = set()
                continue
            if raw.startswith('@@ '):
                # Hunk header, capture new file start
                m = re.search(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', raw)
                if current_file and m:
                    new_line = int(m.group(1))
                    in_hunk = True
                else:
                    in_hunk = False
                    new_line = None
                continue
            if not in_hunk or current_file is None or new_line is None:
                continue
            if raw.startswith('+') and not raw.startswith('+++'):
                changed_lines[current_file].add(new_line)
                new_line += 1
            elif raw.startswith('-') and not raw.startswith('---'):
                # deletion: only old file line advances; new_line stays
                pass
            else:
                # context line (in -U0 there should be none, but be safe)
                new_line += 1

        return changed_lines
    
    def extract_functions_with_clang(self, file_path: str) -> List[Dict]:
        """Extract function signatures using clang AST parsing"""
        if not CLANG_AVAILABLE:
            print("DEBUG_CLANG: clang.cindex not available")
            return []
        
        print(f"DEBUG_CLANG: Starting clang parsing of {file_path}")
        
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
            
            print(f"DEBUG_CLANG: Parsing with args: {' '.join(args[:10])}... (showing first 10)")
            
            tu = index.parse(file_path, args=args)
            
            # Debug: Check for parsing errors
            if tu.diagnostics:
                print(f"DEBUG_CLANG: Found {len(tu.diagnostics)} diagnostics:")
                for diag in tu.diagnostics:
                    print(f"DEBUG_CLANG_DIAG: {diag.severity}: {diag.spelling}")
            
            functions = []
            
            def visit_node(node, depth=0):
                if node.kind in [clang.cindex.CursorKind.FUNCTION_DECL, 
                               clang.cindex.CursorKind.CXX_METHOD]:
                    signature = self.get_function_signature(node)
                    is_cvc5 = self.is_cvc5_function(signature) if signature else False
                    
                    if signature and is_cvc5:
                        func_data = {
                            'signature': signature,
                            'line': node.location.line,
                            'file': file_path
                        }
                        functions.append(func_data)
                        print(f"DEBUG_CVC5_FUNCTION: Found CVC5 function: {signature}")
                
                for child in node.get_children():
                    visit_node(child, depth + 1)
            
            print(f"DEBUG_CLANG: Starting AST traversal...")
            visit_node(tu.cursor)
            
            print(f"DEBUG_CLANG: Parsing complete. Found {len(functions)} functions")
            if functions:
                print(f"DEBUG_CLANG_SAMPLE: Sample functions found:")
                for i, func in enumerate(functions[:3]):  # Show first 3 functions
                    print(f"DEBUG_CLANG_SAMPLE_{i}: {func['signature']}")
                if len(functions) > 3:
                    print(f"DEBUG_CLANG_SAMPLE: ... and {len(functions) - 3} more functions")
            
            return functions
            
        except Exception as e:
            print(f"DEBUG_CLANG_ERROR: Could not parse {file_path} with clang: {e}")
            import traceback
            print(f"DEBUG_CLANG_ERROR_TRACEBACK: {traceback.format_exc()}")
            return []
    
    def get_function_signature(self, cursor) -> Optional[str]:
        """Extract gcov-style function signature from a clang cursor"""
        try:
            name = cursor.spelling
            if not name:
                return None

            qualified_name = self.get_qualified_name(cursor)
            params = []
            
            # Get parameters with more detailed type information
            for child in cursor.get_children():
                if child.kind == clang.cindex.CursorKind.PARM_DECL:
                    t = child.type
                    # Use the canonical type for better accuracy
                    param_type = t.get_canonical().spelling
                    
                    # Handle const qualification more carefully
                    if t.is_const_qualified():
                        # Move const to the right position
                        if "&" in param_type:
                            param_type = param_type.replace("const ", "").replace("&", " const&")
                        elif "*" in param_type:
                            param_type = param_type.replace("const ", "").replace("*", " const*")
                        else:
                            param_type = param_type.replace("const ", "") + " const"
                    
                    # Clean up spacing
                    param_type = param_type.replace("  ", " ").strip()
                    params.append(param_type)

            param_str = ", ".join(params)
            const_suffix = " const" if cursor.is_const_method() else ""
            
            # Add ABI information if present (like [abi:cxx11])
            abi_info = ""
            if hasattr(cursor, 'mangled_name') and cursor.mangled_name:
                # Check for ABI-specific mangling
                if 'abi:cxx11' in str(cursor.mangled_name):
                    abi_info = "[abi:cxx11]"
            
            line = cursor.location.line
            signature = f"{qualified_name}({param_str}){abi_info}{const_suffix}:{line}"
            return signature
        except Exception as e:
            print(f"DEBUG_SIGNATURE_ERROR: Error generating signature: {e}")
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
                if name and name not in parts:  # Avoid duplicates
                    parts.append(name)
            current = current.semantic_parent
        
        parts.reverse()
        qualified_name = "::".join(parts)
        
        # Ensure we have the full cvc5:: namespace
        if not qualified_name.startswith('cvc5::'):
            # Try to find the actual namespace context
            if 'cvc5::' in qualified_name:
                # Extract everything from cvc5:: onwards
                cvc5_index = qualified_name.find('cvc5::')
                qualified_name = qualified_name[cvc5_index:]
        
        return qualified_name
    
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
        """Get changed C++ functions by intersecting diff ranges with AST extents.
        Includes functions whose body overlaps changed lines or whose signature changed.
        Excludes pure moves (identical normalized body before/after).
        """
        print(f"Analyzing commit {commit_hash}...")

        commit_info = self.get_commit_info(commit_hash)
        if not commit_info:
            return []
        print(f"Commit: {commit_info['summary']}")
        print(f"Author: {commit_info['author_name']}")

        # Get diff and changed line ranges on the new side
        diff_text = self.get_commit_diff(commit_hash)
        changed_files_lines = self.get_changed_lines(diff_text)

        # Parent commit (if any)
        try:
            commit = self.repo.commit(commit_hash)
            parent_hash = commit.parents[0].hexsha if commit.parents else None
        except Exception:
            parent_hash = None

        changed_functions: List[str] = []

        for file_path, changed_lines in changed_files_lines.items():
            if not file_path.endswith(('.cpp', '.cc', '.c', '.h', '.hpp')):
                continue

            print(f"  Analyzing {file_path} (changed lines: {len(changed_lines)})")
            if len(changed_lines) > 50:
                print(f"    Note: large change hunk, limiting to C++ overlap only")

            after_src = self.get_file_text_at_commit(commit_hash, file_path)
            if after_src is None:
                continue
            before_src = self.get_file_text_at_commit(parent_hash, file_path) if parent_hash else None

            # Parse functions from in-memory contents
            after_funcs = self.parse_functions_from_text(file_path, after_src)
            before_funcs = self.parse_functions_from_text(file_path, before_src) if before_src is not None else []

            # Build indexes for before
            before_by_sig = {self.build_signature_key(f['signature']): f for f in before_funcs}

            # Helper to normalize function body slice
            def normalized_body(src: str, f: Dict) -> str:
                lines = src.splitlines()
                s = max(1, int(f['start']))
                e = min(len(lines), int(f['end']))
                snippet = "\n".join(lines[s-1:e])
                return self.normalize_code(snippet)

            # Per changed line: select the innermost enclosing function (smallest span)
            selected: Dict[str, Dict] = {}
            if after_funcs:
                spans = [(f, (int(f['end']) - int(f['start']))) for f in after_funcs if self.is_cvc5_function(f['signature'])]
                for ln in sorted(changed_lines):
                    candidates = [f for f, span in spans if int(f['start']) <= ln <= int(f['end'])]
                    if not candidates:
                        continue
                    # choose innermost by minimal span
                    chosen = min(candidates, key=lambda x: (int(x['end']) - int(x['start']), int(x['start'])))
                    key = self.build_signature_key(chosen['signature'])
                    selected[key] = chosen

            # Emit selected functions, dropping pure moves
            for sig_key, f in selected.items():
                # Exclude pure move if existed before and bodies equal
                is_move = False
                if before_src is not None and sig_key in before_by_sig:
                    bf = before_by_sig[sig_key]
                    if normalized_body(before_src, bf) == normalized_body(after_src, f):
                        is_move = True
                if is_move:
                    continue
                mapping_entry = f"{file_path}:{f['signature']}"
                changed_functions.append(mapping_entry)
                print(f"    Selected: {mapping_entry} (overlap=True, sig_changed=False)")

        print(f"Found {len(changed_functions)} changed functions")
        return changed_functions

    def get_file_text_at_commit(self, rev: Optional[str], path: str) -> Optional[str]:
        """Return file contents at a given revision without checking out."""
        if not rev:
            return None
        try:
            result = subprocess.run(['git', 'show', f'{rev}:{path}'], capture_output=True, text=True, cwd=self.repo_path)
            if result.returncode != 0:
                return None
            return result.stdout
        except Exception:
            return None

    def parse_functions_from_text(self, file_path: str, source_text: Optional[str]) -> List[Dict]:
        """Parse C++ function definitions from provided source text using libclang unsaved_files."""
        if not CLANG_AVAILABLE or source_text is None:
            return []
        try:
            index = clang.cindex.Index.create()
            args = [
                '-x', 'c++',
                '-std=c++17',
                '-I./include',
                '-I./build/include',
                '-I./src/include',
                '-I./src/.',
                '-I./build/src',
                '-isystem', './build/deps/include',
                '-I/usr/include',
                '-I/usr/include/c++/11',
                '-I/usr/include/x86_64-linux-gnu/c++/11',
                '-I/usr/include/c++/11/x86_64-linux-gnu',
                '-I/usr/local/include',
                '-DCVC5_ASSERTIONS',
                '-DCVC5_DEBUG',
                '-DCVC5_STATISTICS_ON',
                '-DCVC5_TRACING',
                '-DCVC5_USE_POLY',
                '-D__BUILDING_CVC5LIB',
                '-Dcvc5_obj_EXPORTS',
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
                '-fparse-all-comments',
                '-Wno-unknown-pragmas',
                '-Wno-unused-parameter',
                '-Wno-unused-variable',
                '-Wno-unused-function'
            ]

            tu = index.parse(file_path, args=args, unsaved_files=[(file_path, source_text)])

            funcs: List[Dict] = []

            def visit(n):
                if n.kind in [clang.cindex.CursorKind.FUNCTION_DECL, clang.cindex.CursorKind.CXX_METHOD] and n.is_definition():
                    sig = self.get_function_signature(n)
                    # Only keep functions physically defined in this file (exclude headers and system files)
                    node_file = str(n.location.file) if n.location and n.location.file else None
                    if sig and node_file and self.is_cvc5_function(sig):
                        # Compare by suffix to allow absolute paths
                        from os.path import normpath
                        nf = normpath(node_file)
                        exp = normpath(file_path)
                        if nf.endswith(exp):
                            funcs.append({
                                'signature': sig,
                                'start': n.extent.start.line,
                                'end': n.extent.end.line,
                                'file': node_file
                            })
                for c in n.get_children():
                    visit(c)

            visit(tu.cursor)
            return funcs
        except Exception:
            return []

    def build_signature_key(self, signature: str) -> str:
        """Normalize a signature to a stable key (drop ':line')."""
        if ':' in signature:
            base, last = signature.rsplit(':', 1)
            if last.isdigit():
                return base
        return signature

    def normalize_code(self, code: str) -> str:
        """Remove comments and collapse whitespace for rough body comparison."""
        code = re.sub(r'//.*', '', code)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.S)
        code = re.sub(r'\s+', ' ', code).strip()
        return code
    
    def load_coverage_mapping(self, coverage_json_path: str):
        """Load coverage mapping only when needed."""
        print(f"DEBUG_COVERAGE: Loading coverage mapping from {coverage_json_path}...")
        with open(coverage_json_path, 'r') as f:
            self.coverage_map = json.load(f)
        print(f"DEBUG_COVERAGE: Loaded coverage mapping with {len(self.coverage_map)} functions")
        
        # Show sample keys for debugging
        sample_keys = list(self.coverage_map.keys())[:5]
        print(f"DEBUG_COVERAGE_SAMPLE_KEYS: Sample coverage mapping keys:")
        for i, key in enumerate(sample_keys):
            print(f"DEBUG_COVERAGE_KEY_{i}: '{key}' -> {len(self.coverage_map[key])} tests")
        
        # Show key format analysis
        key_formats = {}
        for key in list(self.coverage_map.keys())[:100]:  # Analyze first 100 keys
            if ':' in key:
                parts = key.split(':')
                if len(parts) >= 3:
                    format_key = f"{len(parts)}_parts"
                    if format_key not in key_formats:
                        key_formats[format_key] = 0
                    key_formats[format_key] += 1
        
        print(f"DEBUG_COVERAGE_FORMATS: Key format analysis:")
        for format_type, count in sorted(key_formats.items()):
            print(f"DEBUG_COVERAGE_FORMAT: {format_type}: {count} keys")
    
    def find_tests_for_functions(self, functions: List[str]) -> Dict:
        """Find unique tests that cover the given functions."""
        if not self.coverage_map:
            print("DEBUG_MATCHING: Error: Coverage mapping not loaded")
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
        
        print(f"DEBUG_MATCHING: Finding tests for {len(functions)} functions...")
        print(f"DEBUG_MATCHING: Coverage map has {len(self.coverage_map)} entries")
        
        # Show sample functions we're trying to match
        print(f"DEBUG_MATCHING_SAMPLE_FUNCTIONS: Sample functions to match:")
        for i, func in enumerate(functions[:3]):  # Show first 3 functions
            print(f"DEBUG_MATCHING_FUNC_{i}: '{func}'")
        if len(functions) > 3:
            print(f"DEBUG_MATCHING_FUNC_MORE: ... and {len(functions) - 3} more functions")
        
        all_covering_tests = set()
        functions_with_tests = 0
        functions_without_tests = 0
        function_test_counts = {}
        test_function_counts = {}
        direct_matches = 0
        path_removed_matches = 0
        
        for i, func in enumerate(functions):
            matching_tests = set()
            match_type = "none"
            
            # Strategy 1: Try direct match first
            if func in self.coverage_map:
                tests = self.coverage_map[func]
                matching_tests.update(tests)
                match_type = "direct"
                direct_matches += 1
                print(f"DEBUG_MATCHING_DIRECT_SUCCESS: Direct match found for '{func}' -> {len(tests)} tests")
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
                                    print(f"DEBUG_MATCHING_PATH_SUCCESS: Path-removed match found for '{func}' -> '{coverage_key}' -> {len(tests)} tests")
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
                
                print(f"DEBUG_MATCHING_SUCCESS: ✓ {func} ({match_type}): {len(matching_tests)} tests")
            else:
                functions_without_tests += 1
                function_test_counts[func] = 0
                print(f"DEBUG_MATCHING_FAILED: ✗ {func}: No tests found")
        
        print(f"DEBUG_MATCHING_STATS: Matching statistics:")
        print(f"DEBUG_MATCHING_STATS: Functions with test coverage: {functions_with_tests}/{len(functions)}")
        print(f"DEBUG_MATCHING_STATS: Functions without test coverage: {functions_without_tests}/{len(functions)}")
        print(f"DEBUG_MATCHING_STATS: Direct matches: {direct_matches}")
        print(f"DEBUG_MATCHING_STATS: Path-removed matches: {path_removed_matches}")
        print(f"DEBUG_MATCHING_STATS: Total unique tests found: {len(all_covering_tests)}")
        
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

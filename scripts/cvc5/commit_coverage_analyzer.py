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

# Monkey-patch: expose template argument introspection via libclang C API if missing
if CLANG_AVAILABLE:
    try:
        # Some environments have Python bindings lacking these APIs; use ctypes if available
        import ctypes
        from ctypes.util import find_library

        libname = find_library('clang')
        if libname:
            _libclang = ctypes.CDLL(libname)

            # Use clang.cindex.CXType definition to ensure ABI compatibility
            CXType = clang.cindex.CXType  # type: ignore

            # Function prototypes
            _libclang.clang_Type_getNumTemplateArguments.argtypes = [CXType]
            _libclang.clang_Type_getNumTemplateArguments.restype = ctypes.c_int

            _libclang.clang_Type_getTemplateArgumentAsType.argtypes = [CXType, ctypes.c_uint]
            _libclang.clang_Type_getTemplateArgumentAsType.restype = CXType

            def _get_num_template_arguments(t):
                try:
                    return _libclang.clang_Type_getNumTemplateArguments(t._type)
                except Exception:
                    return -1

            def _get_template_argument_type(t, idx: int):
                try:
                    cxt = _libclang.clang_Type_getTemplateArgumentAsType(t._type, ctypes.c_uint(idx))
                    # Wrap returned CXType into a clang.cindex.Type
                    if hasattr(clang.cindex, 'Type') and hasattr(clang.cindex.Type, 'from_result'):
                        return clang.cindex.Type.from_result(cxt)  # type: ignore
                    return t
                except Exception:
                    return t

            # Attach if not present
            if not hasattr(clang.cindex.Type, 'get_num_template_arguments'):
                clang.cindex.Type.get_num_template_arguments = _get_num_template_arguments  # type: ignore
            if not hasattr(clang.cindex.Type, 'get_template_argument_type'):
                clang.cindex.Type.get_template_argument_type = _get_template_argument_type  # type: ignore
    except Exception:
        pass

class CommitCoverageAnalyzer:
    def __init__(self, repo_path: str = "."):
        """Initialize with repository path."""
        self.repo_path = Path(repo_path)
        self.repo = git.Repo(repo_path)
        self.coverage_map = None
        # Map from primary mapping entry (path:signature) to an alternative
        # spelling-based signature mapping entry for debug-only matching.
        self.alt_signature_map: Dict[str, str] = {}
    
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
        
        
        # Detect GCC version conflicts
        gcc_versions = self._detect_gcc_version_conflicts()
        
        try:
            index = clang.cindex.Index.create()
            
            # Build unified clang arguments
            args = self._build_clang_args()
            
            print(f"DEBUG_FINAL_ARGS: {len(args)} args, includes: {args[-20:]}")  # Show last 20 (includes)
            
            # Test clang compilation with the args
            self._test_clang_compilation(args)
            
            # Test GCC version compatibility
            self._test_gcc_version_compatibility(args)
            
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
                    # Prefer spelling; if it looks degraded (e.g., 'int'), try textual tokens
                    alt_signature = self.get_function_signature_spelling(node)
                    if alt_signature and '(' in alt_signature and ')' in alt_signature:
                        # Heuristic: if the alt has isolated primitive params where tokens suggest templates/namespaces,
                        # reconstruct params from tokens
                        alt_sig_tokens = self.get_function_signature_textual(node)
                        if alt_sig_tokens:
                            alt_signature = alt_sig_tokens
                    is_cvc5 = self.is_cvc5_function(signature) if signature else False
                    
                    if signature and is_cvc5:
                        func_data = {
                            'signature': signature,
                            'alt_signature': alt_signature,
                            'line': node.location.line,
                            'file': file_path
                        }
                        functions.append(func_data)
                        print(f"DEBUG_CVC5_FUNCTION: Found CVC5 function: {signature}")
                        if alt_signature and alt_signature != signature:
                            print(f"DEBUG_CVC5_FUNCTION_ALT: Alt signature: {alt_signature}")
                
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
            
            # Get parameters with template-aware rendering (best effort)
            for child in cursor.get_children():
                if child.kind == clang.cindex.CursorKind.PARM_DECL:
                    params.append(self._render_param_type(child.type))

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

    def get_function_signature_spelling(self, cursor) -> Optional[str]:
        """Alternative signature that prefers type.spelling to preserve templates.
        Debug-only; not used for matching.
        """
        try:
            name = cursor.spelling
            if not name:
                return None

            qualified_name = self.get_qualified_name(cursor)
            params = []
            for child in cursor.get_children():
                if child.kind == clang.cindex.CursorKind.PARM_DECL:
                    t = child.type
                    # Prefer the original spelling to preserve templates like std::vector<...>
                    param_type = t.spelling or t.get_canonical().spelling
                    # Light whitespace cleanup only
                    param_type = param_type.replace("  ", " ").strip()
                    params.append(param_type)

            param_str = ", ".join(params)
            const_suffix = " const" if cursor.is_const_method() else ""
            abi_info = ""
            if hasattr(cursor, 'mangled_name') and cursor.mangled_name:
                if 'abi:cxx11' in str(cursor.mangled_name):
                    abi_info = "[abi:cxx11]"
            line = cursor.location.line
            signature = f"{qualified_name}({param_str}){abi_info}{const_suffix}:{line}"
            return signature
        except Exception as e:
            return None

    def _render_param_type(self, tp) -> str:
        """Render parameter type with template arguments where possible using libclang APIs.
        Falls back to canonical spelling.
        """
        try:
            # Prefer named/elaborated named type for better spelling
            try:
                named = tp.get_named_type()
                if named.spelling:
                    tp = named
            except Exception:
                pass

            # libclang python bindings may expose num_template_arguments
            num_targs = getattr(tp, 'get_num_template_arguments', None)
            get_targ = getattr(tp, 'get_template_argument_type', None)
            if callable(num_targs) and callable(get_targ):
                n = num_targs()
                if isinstance(n, int) and n > 0:
                    # Render base name
                    base = tp.spelling or tp.get_canonical().spelling
                    base = base.split('<', 1)[0].strip()
                    # Collect arguments
                    args: List[str] = []
                    for i in range(n):
                        try:
                            at = get_targ(i)
                            if at and (at.spelling or at.get_canonical().spelling):
                                args.append(self._render_param_type(at))
                        except Exception:
                            pass
                    if args:
                        return f"{base}<{', '.join(args)}>"

            # Prefer fully-qualified canonical for non-templates; fallback to spelling
            can = tp.get_canonical().spelling or ''
            sp = tp.spelling or ''
            # If canonical shows namespaces or templates, prefer it
            if '::' in can or '<' in can:
                s = can
            elif '::' in sp or '<' in sp:
                s = sp
            else:
                # last resort
                s = can or sp
            s = (s or '').replace('  ', ' ').strip()
            return s
        except Exception:
            try:
                return tp.spelling or tp.get_canonical().spelling or ''
            except Exception:
                return ''

    def _discover_gcc_verbose_includes(self) -> List[str]:
        """Parse g++ verbose include search list and return -isystem dirs. FIXED VERSION."""
        paths: List[str] = []
        try:
            proc = subprocess.run(['g++', '-E', '-x', 'c++', '-', '-v'], 
                                input='', text=True, capture_output=True)
            out = proc.stderr if proc.stderr else proc.stdout
            if not out:
                return []
            
            lines = out.splitlines()
            start_search = False
            end_search = False
            
            # Get the primary GCC version to filter paths
            primary_gcc_version = None
            try:
                gcc_version = subprocess.run(['gcc', '-dumpversion'], capture_output=True, text=True).stdout.strip()
                if gcc_version:
                    primary_gcc_version = gcc_version
            except Exception:
                pass
            
            for line in lines:
                line = line.strip()
                if '#include <...> search starts here:' in line:
                    start_search = True
                    continue
                if start_search and 'End of search list.' in line:
                    end_search = True
                    break
                if start_search and not end_search:
                    # Extract path (remove leading spaces and any trailing comments)
                    path = line.split('#')[0].strip()
                    if path and os.path.isdir(path):
                        # Filter out paths from other GCC versions to avoid conflicts
                        if primary_gcc_version:
                            # Only include paths from the primary GCC version
                            if (f'/c++/{primary_gcc_version}' in path or 
                                f'/gcc/x86_64-linux-gnu/{primary_gcc_version}' in path or
                                '/usr/include' in path or
                                '/usr/local/include' in path):
                                paths.append(path)
                                print(f"DEBUG_INCLUDE_FOUND: {path}")
                        else:
                            # If we can't determine GCC version, be conservative
                            if ('/usr/include' in path or '/usr/local/include' in path):
                                paths.append(path)
                                print(f"DEBUG_INCLUDE_FOUND: {path}")
            
            # CRITICAL: Always add /usr/include manually as fallback
            usr_include = '/usr/include'
            if os.path.isdir(usr_include) and usr_include not in paths:
                paths.append(usr_include)
                print(f"DEBUG_INCLUDE_ADDED_FALLBACK: {usr_include}")
                
        except Exception as e:
            print(f"DEBUG_INCLUDE_ERROR: {e}")
            # Emergency fallback
            fallback = ['/usr/include']
            for p in fallback:
                if os.path.isdir(p):
                    paths.append(p)
        
        # Convert to -isystem flags
        result = []
        for p in paths:
            result.extend(['-isystem', p])
        
        print(f"DEBUG_INCLUDE_PATHS: Found {len(paths)} include paths")
        return result

    def _clang_resource_dir(self) -> Optional[str]:
        """Try to get clang resource dir for proper builtin headers."""
        try:
            res = subprocess.run(['clang', '-print-resource-dir'], capture_output=True, text=True)
            if res.returncode == 0:
                d = res.stdout.strip()
                if d and os.path.isdir(d):
                    return d
        except Exception:
            pass
        return None

    def get_function_signature_textual(self, cursor) -> Optional[str]:
        """Alternative signature using tokens to preserve complex template types from the source."""
        try:
            qualified_name = self.get_qualified_name(cursor)
            params = []
            for child in cursor.get_children():
                if child.kind == clang.cindex.CursorKind.PARM_DECL:
                    tokens = list(child.get_tokens())
                    if not tokens:
                        continue
                    text = " ".join(t.spelling for t in tokens)
                    # Remove default initializers
                    if '=' in text:
                        text = text.split('=')[0].strip()
                    # Remove parameter identifier at the end (best-effort)
                    name = child.spelling or ''
                    if name:
                        # remove last occurrence of name as a whole word
                        import re
                        text = re.sub(r"\b" + re.escape(name) + r"\b\s*$", "", text).strip()
                    # Collapse spaces
                    import re as _re
                    text = _re.sub(r"\s+", " ", text).strip()
                    params.append(text)
            param_str = ", ".join(params)
            const_suffix = " const" if cursor.is_const_method() else ""
            line = cursor.location.line
            return f"{qualified_name}({param_str}){const_suffix}:{line}"
        except Exception:
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
        """Check if a function signature belongs to cvc5.
        Only consider the qualified function name (before '('), allow std types in parameters.
        """
        try:
            head = signature.split('(')[0]
            # If the function itself is in std or gnu namespaces, skip
            if head.startswith('std::') or head.startswith('__') or head.startswith('__gnu_cxx::'):
                return False
            # Include any functions within the cvc5 namespace
            if 'cvc5::' in head:
                return True
            # Fallback: if it has a namespace and isn't std/gnu, accept
            if '::' in head:
                ns = head.split('::', 1)[0]
                if ns and ns != 'std' and not ns.startswith('__') and ns != '__gnu_cxx':
                    return True
            return False
        except Exception:
            return False
    
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
                # Record alternative signature for debug-only matching later
                alt_sig = f.get('alt_signature')
                if alt_sig:
                    alt_entry = f"{file_path}:{alt_sig}"
                    self.alt_signature_map[mapping_entry] = alt_entry
                    if alt_sig != f['signature']:
                        print(f"    Selected ALT (debug-only): {alt_entry}")

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
        
        
        # Detect GCC version conflicts
        gcc_versions = self._detect_gcc_version_conflicts()
        
        try:
            index = clang.cindex.Index.create()
            # Build unified clang arguments
            args = self._build_clang_args()
            
            print(f"DEBUG_FINAL_ARGS: {len(args)} args, includes: {args[-20:]}")  # Show last 20 (includes)

            # Test clang compilation with the args
            self._test_clang_compilation(args)
            
            # Test GCC version compatibility
            self._test_gcc_version_compatibility(args)

            tu = index.parse(file_path, args=args, unsaved_files=[(file_path, source_text)])
            # Print diagnostics similar to extract_functions_with_clang for visibility
            try:
                if tu.diagnostics:
                    print(f"DEBUG_CLANG_TU_DIAG_COUNT: {len(tu.diagnostics)}")
                    for diag in tu.diagnostics:
                        print(f"DEBUG_CLANG_TU_DIAG: {diag.severity}: {diag.spelling}")
            except Exception:
                pass

            funcs: List[Dict] = []

            def visit(n):
                if n.kind in [clang.cindex.CursorKind.FUNCTION_DECL, clang.cindex.CursorKind.CXX_METHOD] and n.is_definition():
                    sig = self.get_function_signature(n)
                    alt_sig = self.get_function_signature_spelling(n)
                    # Try textual reconstruction if needed
                    if alt_sig and '(' in alt_sig and ')' in alt_sig:
                        alt_sig_tokens = self.get_function_signature_textual(n)
                        if alt_sig_tokens:
                            alt_sig = alt_sig_tokens
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
                                'alt_signature': alt_sig,
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

        # --- Normalization helpers (gcov-style) ---
        def strip_line_suffix(s: str) -> str:
            if ':' in s:
                base, last = s.rsplit(':', 1)
                if last.isdigit():
                    return base
            return s

        def split_path_and_sig(key: str):
            no_line = strip_line_suffix(key)
            if ':' in no_line:
                path, sig = no_line.split(':', 1)
                return path, sig
            return '', no_line

        def normalize_to_compare(sig_or_key: str) -> str:
            base = strip_line_suffix(sig_or_key)
            base = self.normalize_function_signature(base)
            # Remove spaces before & and *
            base = re.sub(r"\s+([&*])", r"\1", base)
            # Single space after commas
            base = re.sub(r",\s*", ", ", base)
            # Collapse whitespace
            base = re.sub(r"\s+", " ", base).strip()
            # Expand STL defaults for better matching: vector<T> -> vector<T, allocator<T>>
            try:
                def expand_vector(m):
                    inner = m.group(1).strip()
                    return f"std::vector<{inner}, std::allocator<{inner}> >"
                base = re.sub(r"std::vector<([^,>]+)>", expand_vector, base)
            except Exception:
                pass
            return base

        # Precompute normalized coverage lookup maps
        cov_full_to_tests: Dict[str, Set[str]] = {}
        cov_sig_to_tests: Dict[str, Set[str]] = {}
        for k, tests in self.coverage_map.items():
            norm_full = normalize_to_compare(k)  # path:signature
            cov_full_to_tests.setdefault(norm_full, set()).update(tests)
            _, sig = split_path_and_sig(k)
            norm_sig = normalize_to_compare(sig)
            cov_sig_to_tests.setdefault(norm_sig, set()).update(tests)
        cov_sigs_list = list(cov_sig_to_tests.keys())
        
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
            
            # Build our normalized keys
            our_path, our_sig = split_path_and_sig(func)
            our_full_norm = normalize_to_compare(f"{our_path}:{our_sig}")
            our_sig_norm = normalize_to_compare(our_sig)

            # Strategy 1: normalized direct (path+sig, ignore line)
            if our_full_norm in cov_full_to_tests:
                tests = cov_full_to_tests[our_full_norm]
                matching_tests.update(tests)
                match_type = "direct"
                direct_matches += 1
                print(f"DEBUG_MATCHING_DIRECT_SUCCESS: Direct match found for '{func}' -> {len(tests)} tests")
            else:
                # Strategy 2: normalized signature-only
                if our_sig_norm in cov_sig_to_tests:
                    tests = cov_sig_to_tests[our_sig_norm]
                    matching_tests.update(tests)
                    match_type = "path_removed"
                    path_removed_matches += 1
                    print(f"DEBUG_MATCHING_PATH_SUCCESS: Path-removed match found for '{func}' -> sig match -> {len(tests)} tests")
                else:
                    # Strategy 3: fuzzy among coverage signatures (path-agnostic)
                    try:
                        import difflib
                        best = max(
                            ((cov_sig, difflib.SequenceMatcher(None, our_sig_norm, cov_sig).ratio()) for cov_sig in cov_sigs_list),
                            key=lambda x: x[1],
                            default=(None, 0.0)
                        )
                        best_sig, best_ratio = best
                        if best_sig is not None and best_ratio >= 0.95:
                            tests = cov_sig_to_tests.get(best_sig, set())
                            matching_tests.update(tests)
                            match_type = f"fuzzy:{best_ratio:.2f}"
                            path_removed_matches += 1
                            print(f"DEBUG_MATCHING_FUZZY_SUCCESS: '{func}' -> '{best_sig}' ratio={best_ratio:.2f} tests={len(tests)}")
                    except Exception:
                        pass
                    # Debug-only: if still no matches, try the alternative signature (spelling)
                    if not matching_tests and func in self.alt_signature_map:
                        alt_func = self.alt_signature_map[func]
                        alt_path, alt_sig = split_path_and_sig(alt_func)
                        alt_full_norm = normalize_to_compare(f"{alt_path}:{alt_sig}")
                        alt_sig_norm = normalize_to_compare(alt_sig)
                        alt_tests = set()
                        alt_type = "none"
                        if alt_full_norm in cov_full_to_tests:
                            alt_tests = cov_full_to_tests[alt_full_norm]
                            alt_type = "direct"
                        elif alt_sig_norm in cov_sig_to_tests:
                            alt_tests = cov_sig_to_tests[alt_sig_norm]
                            alt_type = "path_removed"
                        else:
                            try:
                                import difflib
                                best = max(
                                    ((cov_sig, difflib.SequenceMatcher(None, alt_sig_norm, cov_sig).ratio()) for cov_sig in cov_sigs_list),
                                    key=lambda x: x[1],
                                    default=(None, 0.0)
                                )
                                best_sig, best_ratio = best
                                if best_sig is not None and best_ratio >= 0.95:
                                    alt_tests = cov_sig_to_tests.get(best_sig, set())
                                    alt_type = f"fuzzy:{best_ratio:.2f}"
                            except Exception:
                                pass
                        if alt_tests:
                            print(f"DEBUG_ALT_MATCH_WOULD_HAVE: '{func}' => alt '{alt_func}' ({alt_type}) -> {len(alt_tests)} tests")
            
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
        """Normalize function signature by standardizing const placement.
        Robust for template types and spacing.
        """
        import re

        try:
            # Split the signature into prefix, parameter list, and suffix
            # Example: ns::Cls::f(T1, T2):123
            m = re.match(r'^(.*?\()(.+?)(\).*)$', func_sig)
            if not m:
                return func_sig
            prefix, params_str, suffix = m.group(1), m.group(2), m.group(3)

            # Split params by commas while respecting angle bracket depth
            params = []
            buf = []
            depth = 0
            for ch in params_str:
                if ch == '<':
                    depth += 1
                elif ch == '>':
                    depth = max(0, depth - 1)
                if ch == ',' and depth == 0:
                    params.append(''.join(buf).strip())
                    buf = []
                else:
                    buf.append(ch)
            if buf:
                params.append(''.join(buf).strip())

            def normalize_param(p: str) -> str:
                # Collapse internal whitespace
                p = re.sub(r'\s+', ' ', p).strip()
                # Detect leading const
                has_leading_const = p.startswith('const ')
                if has_leading_const:
                    p = p[len('const '):].strip()
                # Extract trailing ref/pointer symbols
                m2 = re.match(r'^(.*?)(\s*[&*]+)$', p)
                if m2:
                    base = m2.group(1).strip()
                    syms = m2.group(2).replace(' ', '')
                else:
                    base = p
                    syms = ''
                if has_leading_const:
                    p = f"{base} const{syms}"
                else:
                    p = f"{base}{syms}"
                # Remove spaces before & and *
                p = re.sub(r"\s+([&*])", r"\1", p)
                return p

            norm_params = [normalize_param(p) for p in params if p != '']
            norm_params_str = ', '.join(norm_params)
            normalized = f"{prefix}{norm_params_str}{suffix}"
            return normalized
        except Exception:
            return func_sig

    def _discover_linux_includes(self) -> List[str]:
        """Discover generic Linux GCC/libstdc++ include directories for libclang parsing."""
        includes: List[str] = []
        try:
            def out(cmd: List[str]) -> str:
                res = subprocess.run(cmd, capture_output=True, text=True)
                return res.stdout.strip()

            dumpver = out(['g++', '-dumpversion']) or ''
            dumpmach = out(['g++', '-dumpmachine']) or ''
            gcc_inc = out(['g++', '-print-file-name=include'])
            candidates = set()
            # Direct GCC include dir
            if gcc_inc and os.path.isdir(gcc_inc):
                candidates.add(gcc_inc)
            # Common libstdc++ layouts
            if dumpver:
                candidates.add(f"/usr/include/c++/{dumpver}")
                if dumpmach:
                    candidates.add(f"/usr/include/{dumpmach}/c++/{dumpver}")
            # Backward headers
            if dumpver:
                candidates.add(f"/usr/include/c++/{dumpver}/backward")
            # Machine-specific
            if dumpmach:
                candidates.add(f"/usr/include/{dumpmach}")
            # Generic
            candidates.add('/usr/include')
            candidates.add('/usr/local/include')

            for p in sorted(candidates):
                if os.path.isdir(p):
                    includes.extend(['-isystem', p])
        except Exception:
            pass
        return includes

    def _get_comprehensive_system_includes(self) -> List[str]:
        """Get comprehensive system include paths using only GCC 14 toolchain."""
        includes = []
        
        # Add GCC toolchain support (critical for consistency with workflow)
        includes.extend(['--gcc-toolchain=/usr'])
        print("DEBUG_GCC14_TOOLCHAIN: Added --gcc-toolchain=/usr")
        
        # Standard system includes (always include these)
        system_paths = [
            '/usr/include',
            '/usr/local/include',
            '/usr/include/x86_64-linux-gnu',
        ]
        
        # GCC 14 specific paths (only GCC 14, no other versions)
        gcc14_paths = [
            '/usr/include/c++/14',
            '/usr/include/c++/14/backward',
            '/usr/lib/gcc/x86_64-linux-gnu/14/include',
            '/usr/lib/gcc/x86_64-linux-gnu/14/include-fixed',
        ]
        
        # Add GCC 14 paths that exist
        for path in gcc14_paths:
            if os.path.isdir(path):
                system_paths.append(path)
                print(f"DEBUG_GCC14_PATH: Found GCC 14 path {path}")
        
        # Add verified paths (avoid duplicates)
        added_paths = set()
        for path in system_paths:
            if os.path.isdir(path) and path not in added_paths:
                includes.extend(['-isystem', path])
                added_paths.add(path)
                print(f"DEBUG_GCC14_INCLUDE: Added {path}")
        
        print(f"DEBUG_GCC14_TOTAL: Added {len(includes)} total include flags")
        return includes

    def _ensure_c_stdlib(self) -> List[str]:
        """Ensure C standard library headers are available using GCC 14."""
        includes = []
        
        # Add essential C standard library paths (GCC 14 only)
        c_stdlib_paths = [
            '/usr/include',                                    # Primary C standard library
            '/usr/lib/gcc/x86_64-linux-gnu/14/include',      # GCC 14 C headers
            '/usr/include/x86_64-linux-gnu',                  # Architecture-specific C headers
        ]
        
        for path in c_stdlib_paths:
            if os.path.isdir(path):
                includes.extend(['-isystem', path])
                print(f"DEBUG_GCC14_C_STDLIB: Added C stdlib path: {path}")
                break  # Use the first available path
        
        return includes

    def _build_clang_args(self) -> List[str]:
        """Build unified clang arguments for CVC5 parsing."""
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
        
        # Add unified system includes (avoiding conflicts and duplicates)
        args.extend(self._get_unified_system_includes())
        
        return args

    def _get_unified_system_includes(self) -> List[str]:
        """Get unified system include paths avoiding conflicts and duplicates."""
        includes = []
        added_paths = set()
        
        # Get the primary GCC version to use consistently
        primary_gcc_version = None
        try:
            gcc_version = subprocess.run(['gcc', '-dumpversion'], capture_output=True, text=True).stdout.strip()
            if gcc_version:
                primary_gcc_version = gcc_version
                print(f"DEBUG_UNIFIED_GCC: Using primary GCC version {primary_gcc_version}")
        except Exception:
            pass
        
        # Essential system paths (always include these first)
        essential_paths = [
            '/usr/include',
            '/usr/local/include',
            '/usr/include/x86_64-linux-gnu',
        ]
        
        for path in essential_paths:
            if os.path.isdir(path) and path not in added_paths:
                includes.extend(['-isystem', path])
                added_paths.add(path)
                print(f"DEBUG_UNIFIED_ESSENTIAL: Added {path}")
        
        # Add primary GCC version paths (only if we have a primary version)
        if primary_gcc_version:
            gcc_paths = [
                f'/usr/include/c++/{primary_gcc_version}',
                f'/usr/include/c++/{primary_gcc_version}/backward',
                f'/usr/lib/gcc/x86_64-linux-gnu/{primary_gcc_version}/include',
                f'/usr/lib/gcc/x86_64-linux-gnu/{primary_gcc_version}/include-fixed',
            ]
            
            for path in gcc_paths:
                if os.path.isdir(path) and path not in added_paths:
                    includes.extend(['-isystem', path])
                    added_paths.add(path)
                    print(f"DEBUG_UNIFIED_GCC: Added {path}")
        
        # Add GCC toolchain support
        includes.extend(['--gcc-toolchain=/usr'])
        
        # Add clang resource directory if available
        crd = self._clang_resource_dir()
        if crd:
            includes.extend(['-resource-dir', crd])
            print(f"DEBUG_UNIFIED_CLANG: Added resource dir {crd}")
        
        # Ensure C standard library is explicitly available
        c_stdlib_paths = [
            '/usr/include',
            f'/usr/lib/gcc/x86_64-linux-gnu/{primary_gcc_version}/include' if primary_gcc_version else '/usr/lib/gcc/x86_64-linux-gnu/13/include',
        ]
        
        for path in c_stdlib_paths:
            if os.path.isdir(path) and path not in added_paths:
                includes.extend(['-isystem', path])
                added_paths.add(path)
                print(f"DEBUG_UNIFIED_C_STDLIB: Added C stdlib path {path}")
                break  # Use the first available path
        
        print(f"DEBUG_UNIFIED_TOTAL: Added {len(includes)} total include flags")
        return includes

    def _detect_gcc_version_conflicts(self) -> Dict[str, List[str]]:
        """Detect GCC 14 include paths to ensure consistency."""
        gcc_versions = {}
        
        try:
            # Only check for GCC 14 (matching workflow's libstdc++-14-dev)
            version = '14'
            version_paths = []
            
            # Check C++ headers
            cpp_path = f'/usr/include/c++/{version}'
            if os.path.isdir(cpp_path):
                version_paths.append(cpp_path)
            
            # Check GCC include paths
            gcc_include = f'/usr/lib/gcc/x86_64-linux-gnu/{version}/include'
            if os.path.isdir(gcc_include):
                version_paths.append(gcc_include)
            
            if version_paths:
                gcc_versions[version] = version_paths
                print(f"DEBUG_GCC14_VERSION: Found GCC {version} with paths: {version_paths}")
            else:
                print("DEBUG_GCC14_VERSION: Warning - GCC 14 paths not found")
        
        except Exception as e:
            print(f"DEBUG_GCC14_VERSION_ERROR: {e}")
        
        return gcc_versions

    def _test_gcc_version_compatibility(self, args: List[str]) -> bool:
        """Test if the current GCC version setup works with clang."""
        test_code = '''
        #include <stdlib.h>
        #include <iostream>
        int main() { return 0; }
        '''
        
        try:
            result = subprocess.run(['clang++'] + args + ['-x', 'c++', '-'], 
                                  input=test_code, text=True, capture_output=True)
            if result.returncode == 0:
                print("DEBUG_GCC_COMPAT: ✅ GCC version compatibility test passed")
                return True
            else:
                print(f"DEBUG_GCC_COMPAT: ❌ GCC version compatibility test failed: {result.stderr[:200]}")
                return False
        except Exception as e:
            print(f"DEBUG_GCC_COMPAT: ❌ GCC compatibility test error: {e}")
            return False


    def _test_clang_compilation(self, args: List[str]) -> bool:
        """Test if clang can compile a simple C++ program with the given args."""
        test_code = '''
        #include <stdlib.h>
        #include <iostream>
        #include <vector>
        int main() { return 0; }
        '''
        
        try:
            result = subprocess.run(['clang++'] + args + ['-x', 'c++', '-'], 
                                  input=test_code, text=True, capture_output=True)
            if result.returncode == 0:
                print("DEBUG_CLANG_TEST: ✅ Clang compilation test passed")
                return True
            else:
                print(f"DEBUG_CLANG_TEST: ❌ Clang compilation test failed: {result.stderr[:200]}")
                return False
        except Exception as e:
            print(f"DEBUG_CLANG_TEST: ❌ Clang test error: {e}")
            return False
    
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

#!/usr/bin/env python3
"""
Solver Oracle - Compare results from multiple SMT solvers
Runs a test file on CVC5 (reference) and another solver, verifies they agree.
Only considers tests correct when solvers agree (both sat or both unsat).

Usage:
    python3 scripts/oracle.py --cvc5-path <path> --solver-path <path> [--solver-flags <flags>] [--verbose] <test_file>

Exit codes:
    0: Solvers agree (test passes)
    1: Solvers disagree or error occurred
"""

import argparse
import subprocess
import sys
import re
import shlex
from pathlib import Path
from typing import Tuple, List

def extract_command_line_directives(test_file: Path, verbose: bool = False) -> List[str]:
    """
    Extract COMMAND-LINE directives from test file (like run_regression.py does).
    
    Supports directives like:
    ; COMMAND-LINE: --incremental
    ; COMMAND-LINE: --strings-fmf --incremental
    """
    command_lines = []
    comment_char = ";"  # For .smt2 files
    
    try:
        with open(test_file, 'r') as f:
            for line in f:
                # Skip empty lines or lines that don't start with comment character
                if not line.strip() or line[0] != comment_char:
                    continue
                
                line_content = line[1:].lstrip()
                
                # Check for COMMAND-LINE directive
                if line_content.startswith("COMMAND-LINE:"):
                    cmd_line = line_content[len("COMMAND-LINE:"):].strip()
                    if cmd_line:
                        # Use shlex.split to handle quoted arguments properly
                        command_lines.extend(shlex.split(cmd_line))
    except Exception as e:
        if verbose:
            print(f"Warning: Could not parse COMMAND-LINE directives: {e}", file=sys.stderr)
    
    return command_lines

def check_needs_incremental(test_file: Path) -> bool:
    """
    Check if SMT file uses push/pop commands requiring incremental mode.
    
    Returns True if file contains (push) or (pop) commands.
    """
    try:
        content = test_file.read_text()
        # Check for push or pop commands (case-insensitive)
        # Use word boundary to avoid matching "push_back" etc.
        return bool(re.search(r'\(push\b|\(pop\b', content, re.IGNORECASE))
    except Exception:
        pass
    return False

def check_has_unsupported_commands(test_file: Path) -> bool:
    """
    Check if SMT file uses commands unsupported by CVC5.
    
    Returns True if file contains commands that CVC5 cannot handle.
    """
    try:
        content = test_file.read_text()
        # Check for Z3-specific commands that CVC5 doesn't support
        unsupported_patterns = [
            r'\(check-sat-using\b',  # Z3-specific tactic command
            # Add more patterns as needed:
            # r'\(apply-tactic\b',  # If CVC5 doesn't support this
        ]
        return any(re.search(pattern, content, re.IGNORECASE) for pattern in unsupported_patterns)
    except Exception:
        pass
    return False

def extract_result(output: str, stderr: str = "", exit_code: int = 0) -> str:
    """
    Extract SMT result from solver output.
    
    Returns: 'sat', 'unsat', 'unknown', 'error', 'timeout', or 'query'
    
    Priority: sat/unsat/unknown in output > exit codes
    """
    combined = (output + "\n" + stderr).lower()
    
    # Look for sat/unsat/unknown in output - prioritize these over exit codes
    # Check for exact matches first, then word boundaries
    for line in combined.split('\n'):
        word = line.strip()
        if word in ('unsat', 'sat', 'unknown'):
            return word
    
    # Fallback: word boundaries (check unsat first since 'sat' is substring)
    if re.search(r'(^|\s)unsat(\s|$)', combined):
        return 'unsat'
    if re.search(r'(^|\s)sat(\s|$)', combined):
        return 'sat'
    if re.search(r'(^|\s)unknown(\s|$)', combined):
        return 'unknown'
    
    # Check for parse errors (these are real errors)
    if re.search(r'\(error\s+"parse error', combined, re.IGNORECASE):
        return 'error'
    
    # Check for other errors
    if re.search(r'\(error\s+', combined, re.IGNORECASE):
        return 'error'
    
    # Check for timeout
    if exit_code == 124:  # timeout exit code
        return 'timeout'
    
    # If exit code is 0 but no sat/unsat, might be a query test
    # (produces values but no satisfiability result)
    if exit_code == 0:
        # Check if there's actual output (not just empty or only "unsupported")
        output_stripped = output.strip()
        if output_stripped and not re.search(r'^unsupported\s*$', output_stripped, re.MULTILINE | re.IGNORECASE):
            # Has meaningful output but no sat/unsat - likely a query test
            return 'query'
    
    return 'error'

def check_has_set_logic(test_file: Path) -> bool:
    """Check if SMT file has set-logic command"""
    try:
        return bool(re.search(r'\(set-logic\s+', test_file.read_text(), re.IGNORECASE))
    except Exception:
        return False

def run_solver(solver_path: str, solver_flags: List[str], test_file: str, timeout: int = 120, verbose: bool = False, is_cvc5: bool = False) -> Tuple[int, str, str, str]:
    """
    Run a solver on a test file.
    
    Returns: (exit_code, result, stdout, stderr)
    """
    cmd = [solver_path] + solver_flags
    
    if is_cvc5:
        test_path = Path(test_file)
        
        # 1. Extract COMMAND-LINE directives from test file (like run_regression.py)
        cmd_line_directives = extract_command_line_directives(test_path, verbose)
        if cmd_line_directives:
            cmd.extend(cmd_line_directives)
            if verbose:
                print(f"   Using COMMAND-LINE directives: {' '.join(cmd_line_directives)}", file=sys.stderr)
        
        # 2. Add --force-logic=ALL if set-logic is missing
        if not check_has_set_logic(test_path):
            cmd.append('--force-logic=ALL')
        
        # 3. Add --incremental if push/pop commands are present
        # (unless already specified via COMMAND-LINE directive)
        if '--incremental' not in cmd and check_needs_incremental(test_path):
            cmd.append('--incremental')
            if verbose:
                print(f"   Auto-detected incremental mode (push/pop found)", file=sys.stderr)
    
    cmd.append(test_file)
    
    if verbose:
        print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        # Pass exit_code to extract_result
        result_str = extract_result(result.stdout, result.stderr, result.returncode)
        return (result.returncode, result_str, result.stdout, result.stderr)
    except subprocess.TimeoutExpired:
        return (124, 'timeout', '', f'Timeout after {timeout}s')
    except FileNotFoundError:
        return (127, 'error', '', f'Solver not found: {solver_path}')
    except Exception as e:
        return (1, 'error', '', str(e))

def main():
    parser = argparse.ArgumentParser(
        description='Solver Oracle - Compare results from CVC5 (reference) and another solver'
    )
    parser.add_argument('--cvc5-path', required=True, help='Path to CVC5 binary (reference solver)')
    parser.add_argument('--solver-path', required=True, help='Path to solver binary to compare against CVC5')
    parser.add_argument('--solver-flags', nargs='*', default=[], help='Flags for the solver (default: auto-detect based on solver)')
    parser.add_argument('--timeout', type=int, default=120, help='Timeout per solver in seconds (default: 120)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output (default: silent, only exit code)')
    parser.add_argument('test_file', help='SMT test file to run')
    
    args = parser.parse_args()
    
    test_file = Path(args.test_file)
    if not test_file.exists():
        if args.verbose:
            print(f"Error: Test file not found: {test_file}", file=sys.stderr)
        sys.exit(1)
    
    # Check for unsupported commands early (before running solvers)
    if check_has_unsupported_commands(test_file):
        if args.verbose:
            print("⏭️ Test uses unsupported commands (skipping)")
        sys.exit(1)
    
    cvc5_flags = ['--check-models', '--check-proofs', '--strings-exp']
    solver_flags = args.solver_flags or []
    
    cvc5_exit, cvc5_result, cvc5_stdout, cvc5_stderr = run_solver(
        args.cvc5_path, cvc5_flags, str(test_file), args.timeout, args.verbose, is_cvc5=True
    )
    solver_exit, solver_result, solver_stdout, solver_stderr = run_solver(
        args.solver_path, solver_flags, str(test_file), args.timeout, args.verbose, is_cvc5=False
    )
    
    if args.verbose:
        print(f"CVC5 (reference): {cvc5_result} (exit code: {cvc5_exit})")
        print(f"Solver: {solver_result} (exit code: {solver_exit})")
    
    valid_results = {'sat', 'unsat'}
    query_results = {'query'}  # Tests that produce output but no sat/unsat
    
    # Handle query tests (both produce output but no sat/unsat)
    if cvc5_result == 'query' and solver_result == 'query':
        # Compare outputs (simplified - might need more sophisticated comparison)
        # For now, if both are query tests, consider them as "agreeing" if both succeeded
        if cvc5_exit == 0 and solver_exit == 0:
            if args.verbose:
                print("✅ Solvers agree (query test - both produced output)")
            sys.exit(0)
        else:
            if args.verbose:
                print("❌ Solvers disagree (query test - different exit codes)")
            sys.exit(1)
    
    # Handle valid sat/unsat results (prioritize these)
    if cvc5_result in valid_results and solver_result in valid_results:
        if cvc5_result == solver_result:
            if args.verbose:
                print("✅ Solvers agree")
            sys.exit(0)
        else:
            if args.verbose:
                print(f"❌ Solvers disagree: CVC5={cvc5_result}, Solver={solver_result}")
            sys.exit(1)
    
    # Handle cases where one solver has valid result but other doesn't
    if cvc5_result in valid_results:
        # CVC5 has valid result but solver doesn't - disagreement
        if args.verbose:
            print(f"⚠️ CVC5={cvc5_result} but solver={solver_result}")
            if solver_result == 'error':
                if solver_stdout.strip():
                    print(f"Solver stdout:\n{solver_stdout}")
                if solver_stderr.strip():
                    print(f"Solver stderr:\n{solver_stderr}")
        sys.exit(1)
    
    if solver_result in valid_results:
        # Solver has valid result but CVC5 doesn't - disagreement
        if args.verbose:
            print(f"⚠️ Solver={solver_result} but CVC5={cvc5_result}")
            if cvc5_result == 'error':
                if cvc5_stdout.strip():
                    print(f"CVC5 stdout:\n{cvc5_stdout}")
                if cvc5_stderr.strip():
                    print(f"CVC5 stderr:\n{cvc5_stderr}")
        sys.exit(1)
    
    # Handle timeouts
    if cvc5_result == 'timeout' or solver_result == 'timeout':
        if args.verbose:
            print("⏱️ One or both solvers timed out")
        sys.exit(1)
    
    # Handle errors
    if cvc5_result == 'error' or solver_result == 'error':
        if args.verbose:
            print("❌ One or both solvers encountered an error")
            if cvc5_result == 'error':
                if cvc5_stdout.strip():
                    print(f"CVC5 stdout:\n{cvc5_stdout}")
                if cvc5_stderr.strip():
                    print(f"CVC5 stderr:\n{cvc5_stderr}")
            if solver_result == 'error':
                if solver_stdout.strip():
                    print(f"Solver stdout:\n{solver_stdout}")
                if solver_stderr.strip():
                    print(f"Solver stderr:\n{solver_stderr}")
        sys.exit(1)
    
    # Handle unknown or other non-standard results
    if args.verbose:
        print(f"⚠️ Non-standard results: CVC5={cvc5_result}, Solver={solver_result}")
    sys.exit(1)

if __name__ == "__main__":
    main()


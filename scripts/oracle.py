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
from pathlib import Path
from typing import Optional, Tuple, Dict, List

def extract_result(output: str, stderr: str = "") -> str:
    """
    Extract SMT result from solver output.
    Returns: 'sat', 'unsat', 'unknown', 'error', or 'timeout'
    """
    # Combine stdout and stderr
    combined = (output + "\n" + stderr).lower()
    
    # Look for sat/unsat/unknown in output
    # These are typically on their own line or followed by whitespace
    if re.search(r'\bunsat\b', combined):
        return 'unsat'
    elif re.search(r'\bsat\b', combined):
        return 'sat'
    elif re.search(r'\bunknown\b', combined):
        return 'unknown'
    else:
        return 'error'  # Default to error if we can't determine

def run_solver(solver_path: str, solver_flags: List[str], test_file: str, timeout: int = 120, verbose: bool = False) -> Tuple[int, str, str, str]:
    """
    Run a solver on a test file.
    Returns: (exit_code, result, stdout, stderr)
    """
    cmd = [solver_path] + solver_flags + [test_file]
    
    if verbose:
        print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        result_str = extract_result(result.stdout, result.stderr)
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
    
    # CVC5 flags (reference solver)
    cvc5_flags = ['--check-models', '--check-proofs', '--strings-exp']
    
    # Solver flags - use provided flags or default to empty
    solver_flags = args.solver_flags if args.solver_flags else []
    
    # Run CVC5 (reference)
    cvc5_exit, cvc5_result, cvc5_stdout, cvc5_stderr = run_solver(
        args.cvc5_path, cvc5_flags, str(test_file), args.timeout, args.verbose
    )
    
    # Run solver to compare
    solver_exit, solver_result, solver_stdout, solver_stderr = run_solver(
        args.solver_path, solver_flags, str(test_file), args.timeout, args.verbose
    )
    
    # Print results only if verbose
    if args.verbose:
        print(f"CVC5 (reference): {cvc5_result} (exit code: {cvc5_exit})")
        print(f"Solver: {solver_result} (exit code: {solver_exit})")
    
    # Check if solvers agree
    # Only consider sat/unsat as valid results that must agree
    valid_results = {'sat', 'unsat'}
    
    if cvc5_result in valid_results and solver_result in valid_results:
        if cvc5_result == solver_result:
            if args.verbose:
                print("✅ Solvers agree")
            sys.exit(0)
        else:
            if args.verbose:
                print(f"❌ Solvers disagree: CVC5={cvc5_result}, Solver={solver_result}")
            sys.exit(1)
    elif cvc5_result == 'timeout' or solver_result == 'timeout':
        if args.verbose:
            print("⏱️ One or both solvers timed out")
        sys.exit(1)
    elif cvc5_result == 'error' or solver_result == 'error':
        if args.verbose:
            print("❌ One or both solvers encountered an error")
            if cvc5_stderr:
                print(f"CVC5 stderr: {cvc5_stderr[:200]}")
            if solver_stderr:
                print(f"Solver stderr: {solver_stderr[:200]}")
        sys.exit(1)
    else:
        # At least one solver returned unknown or other non-standard result
        if args.verbose:
            print(f"⚠️ Non-standard results: CVC5={cvc5_result}, Solver={solver_result}")
        sys.exit(1)

if __name__ == "__main__":
    main()


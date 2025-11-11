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
from typing import Tuple, List

def extract_result(output: str, stderr: str = "", exit_code: int = 0) -> str:
    """
    Extract SMT result from solver output. Prioritizes output over exit codes.
    Exit codes are ignored - solvers may exit with non-zero codes due to warnings
    but still produce valid results (sat/unsat) in their output.
    Only uses exit_code=124 to detect timeouts when no result is found in output.
    """
    combined = (output + "\n" + stderr).lower()
    
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
    
    # Only use exit code for timeout detection when no result found in output
    return 'timeout' if exit_code == 124 else 'error'

def check_has_set_logic(test_file: Path) -> bool:
    """Check if SMT file has set-logic command"""
    try:
        return bool(re.search(r'\(set-logic\s+', test_file.read_text(), re.IGNORECASE))
    except Exception:
        return False

def run_solver(solver_path: str, solver_flags: List[str], test_file: str, timeout: int = 120, verbose: bool = False, is_cvc5: bool = False) -> Tuple[int, str, str, str]:
    """Run a solver on a test file. Returns: (exit_code, result, stdout, stderr)"""
    cmd = [solver_path] + solver_flags
    if is_cvc5 and not check_has_set_logic(Path(test_file)):
        cmd.append('--force-logic=ALL')
    cmd.append(test_file)
    
    if verbose:
        print(f"Running: {' '.join(cmd)}", file=sys.stderr)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (result.returncode, extract_result(result.stdout, result.stderr, result.returncode), result.stdout, result.stderr)
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
    
    # Both solvers produced valid results - check agreement
    if cvc5_result in valid_results and solver_result in valid_results:
        if cvc5_result == solver_result:
            if args.verbose:
                print("✅ Solvers agree")
            sys.exit(0)
        if args.verbose:
            print(f"❌ Solvers disagree: CVC5={cvc5_result}, Solver={solver_result}")
        sys.exit(1)
    
    # One solver has valid result, other doesn't - disagreement
    if cvc5_result in valid_results or solver_result in valid_results:
        if args.verbose:
            print(f"⚠️ CVC5={cvc5_result}, Solver={solver_result}")
            # Show all output for the failing solver
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
    
    # Handle timeouts and errors
    if 'timeout' in (cvc5_result, solver_result):
        if args.verbose:
            print("⏱️ One or both solvers timed out")
        sys.exit(1)
    
    if 'error' in (cvc5_result, solver_result):
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
    
    # Non-standard results
    if args.verbose:
        print(f"⚠️ Non-standard results: CVC5={cvc5_result}, Solver={solver_result}")
    sys.exit(1)

if __name__ == "__main__":
    main()


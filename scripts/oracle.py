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

# Ignore list for errors that should be treated as "skip this test"
# (similar to ignore_list in the reference script)
IGNORE_PATTERNS = [
    r'\(error\s+"parse error',  # Parse errors
    r'parse error',
    r'unsupported',
    r'unexpected char',
    r'failed to open file',
    r'Cannot get model',
    r'Unimplemented code encountered',
]

def should_ignore_error(stdout: str, stderr: str) -> bool:
    """Check if error output should be ignored (parse errors, unsupported features, etc.)"""
    combined = stdout + " " + stderr
    return any(re.search(pattern, combined, re.IGNORECASE) for pattern in IGNORE_PATTERNS)

def check_has_unsupported_commands(test_file: Path) -> bool:
    """Check if SMT file uses commands unsupported by CVC5."""
    try:
        content = test_file.read_text()
        # Check for Z3-specific commands that CVC5 doesn't support
        unsupported_patterns = [
            r'\(check-sat-using\b',  # Z3-specific tactic command
        ]
        return any(re.search(pattern, content, re.IGNORECASE) for pattern in unsupported_patterns)
    except Exception:
        return False

def extract_result(output: str, stderr: str = "", exit_code: int = 0) -> str:
    """
    Extract SMT result from solver output. Prioritizes output over exit codes.
    Returns: 'sat', 'unsat', 'unknown', 'error', or 'timeout'
    
    Uses regex to match 'sat', 'unsat', or 'unknown' on their own lines (like grep_result).
    Handles multiple exit codes for timeouts:
    - 124 = subprocess timeout
    - 137 = timeout command signal (SIGKILL)
    - 143 = SIGTERM (process killed, often by timeout)
    """
    # Check for timeout (124 = subprocess timeout, 137 = SIGKILL, 143 = SIGTERM)
    if exit_code == 124 or exit_code == 137 or exit_code == 143:
        return 'timeout'
    
    # Look for sat/unsat/unknown on their own lines (case-insensitive)
    # Use MULTILINE flag to match start/end of lines
    if re.search("^unsat$", output, flags=re.MULTILINE | re.IGNORECASE):
        return 'unsat'
    elif re.search("^sat$", output, flags=re.MULTILINE | re.IGNORECASE):
        return 'sat'
    elif re.search("^unknown$", output, flags=re.MULTILINE | re.IGNORECASE):
        return 'unknown'
    
    return 'error'

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
    
    # Both solvers produced valid results - check agreement
    if cvc5_result in valid_results and solver_result in valid_results:
        if cvc5_result == solver_result:
            if args.verbose:
                print("✅ Solvers agree")
            sys.exit(0)
        if args.verbose:
            print(f"❌ Solvers disagree: CVC5={cvc5_result}, Solver={solver_result}")
        sys.exit(1)
    
    # Handle UNKNOWN: treat as "don't care" - if one is unknown, they can still match
    # (like the reference script's SolverResult.equals() method)
    if cvc5_result == 'unknown' and solver_result in valid_results:
        if args.verbose:
            print("✅ Solvers agree (CVC5=unknown, treating as match)")
        sys.exit(0)
    if solver_result == 'unknown' and cvc5_result in valid_results:
        if args.verbose:
            print("✅ Solvers agree (Solver=unknown, treating as match)")
        sys.exit(0)
    if cvc5_result == 'unknown' and solver_result == 'unknown':
        if args.verbose:
            print("✅ Solvers agree (both unknown)")
        sys.exit(0)
    
    # One solver has valid result, other doesn't - disagreement
    if cvc5_result in valid_results or solver_result in valid_results:
        if args.verbose:
            print(f"⚠️ CVC5={cvc5_result}, Solver={solver_result}")
            if cvc5_result == 'error' and (cvc5_stdout.strip() or cvc5_stderr.strip()):
                if cvc5_stdout.strip():
                    print(f"CVC5 stdout:\n{cvc5_stdout}")
                if cvc5_stderr.strip():
                    print(f"CVC5 stderr:\n{cvc5_stderr}")
            if solver_result == 'error' and (solver_stdout.strip() or solver_stderr.strip()):
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
        # Check if errors should be ignored (parse errors, unsupported features, etc.)
        cvc5_should_ignore = cvc5_result == 'error' and should_ignore_error(cvc5_stdout, cvc5_stderr)
        solver_should_ignore = solver_result == 'error' and should_ignore_error(solver_stdout, solver_stderr)
        
        if cvc5_should_ignore or solver_should_ignore:
            if args.verbose:
                if cvc5_should_ignore:
                    print("⚠️ CVC5 error (ignored - parse error/unsupported)")
                if solver_should_ignore:
                    print("⚠️ Solver error (ignored - parse error/unsupported)")
            # Treat ignored errors as skip (exit 1, but with clear message)
            sys.exit(1)
        
        if args.verbose:
            print("❌ One or both solvers encountered an error")
            if cvc5_result == 'error' and (cvc5_stdout.strip() or cvc5_stderr.strip()):
                if cvc5_stdout.strip():
                    print(f"CVC5 stdout:\n{cvc5_stdout}")
                if cvc5_stderr.strip():
                    print(f"CVC5 stderr:\n{cvc5_stderr}")
            if solver_result == 'error' and (solver_stdout.strip() or solver_stderr.strip()):
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



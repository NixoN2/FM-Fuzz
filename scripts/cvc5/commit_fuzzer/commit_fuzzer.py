#!/usr/bin/env python3
"""
Commit Fuzzer with Arc Coverage Measurement
Runs typefuzz on a test and measures arc (branch) coverage.
"""

import os
import sys
import json
import subprocess
import argparse
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, List

# Number of cores to use for fastcov coverage extraction
FASTCOV_JOBS = 4

class CommitFuzzer:
    def __init__(self, build_dir: str = "build", tests_root: str = "test/regress/cli"):
        self.build_dir = Path(build_dir)
        self.tests_root = Path(tests_root)
        self.binary_path = self.build_dir / "bin" / "cvc5"
        
    def reset_coverage_counters(self):
        """Reset coverage counters using fastcov --zerocounters for isolation"""
        # Clear existing .gcda files before resetting (same as coverage_mapper.py)
        for gcda in self.build_dir.rglob("*.gcda"):
            gcda.unlink()
        
        # Reset coverage counters using fastcov (ignore errors, same as coverage_mapper.py)
        subprocess.run([
            "fastcov", "--zerocounters", "--search-directory", str(self.build_dir),
            "--exclude", "/usr/include/*", "--exclude", "*/deps/*"
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)
    
    def run_typefuzz_single_iteration(self, input_file: Path, bugs_folder: Path, 
                                      scratch_folder: Path, log_folder: Path) -> bool:
        """Run typefuzz with exactly 1 iteration on the input file (seed or mutant)"""
        if not input_file.exists():
            print(f"Error: Input file not found: {input_file}")
            return False
        
        # Ensure folders exist
        bugs_folder.mkdir(parents=True, exist_ok=True)
        scratch_folder.mkdir(parents=True, exist_ok=True)
        log_folder.mkdir(parents=True, exist_ok=True)
        
        # Build typefuzz command with 1 iteration
        # Note: Flags must come before positional arguments (SOLVER_CLIS and PATH_TO_SEEDS)
        # Use -m 1 to ensure every iteration is tested (default modulo is 2, which skips iteration 1)
        cmd = [
            "typefuzz",
            "-i", "1",  # Single iteration
            "--keep-mutants",  # KEEP MUTANTS - critical for chaining iterations
            # "-q",  # Quiet mode
            "--bugs", str(bugs_folder),
            "--scratch", str(scratch_folder),
            "--logfolder", str(log_folder),
            "z3;./build/bin/cvc5",  # Positional: SOLVER_CLIS
            str(input_file)  # Positional: PATH_TO_SEEDS
        ]
        
        try:
            # Capture stderr to see what's wrong, but keep stdout quiet
            result = subprocess.run(
                cmd,
                cwd=self.build_dir.parent,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            if result.returncode != 0:
                print(f"✗ typefuzz failed (exit code {result.returncode})", end=" ", flush=True)
                if result.stderr:
                    # Show first line of error if available
                    error_first_line = result.stderr.strip().split('\n')[0]
                    if error_first_line:
                        print(f"- {error_first_line[:100]}")
                else:
                    print("")
            return result.returncode == 0
        except FileNotFoundError:
            print(f"✗ typefuzz command not found", file=sys.stderr)
            return False
        except Exception as e:
            print(f"✗ Error running typefuzz: {e}", file=sys.stderr)
            return False
    
    def find_latest_mutant(self, scratch_folder: Path) -> Optional[Path]:
        """Find the most recently modified .smt2 file in the scratch folder"""
        if not scratch_folder.exists():
            return None
        
        mutants = list(scratch_folder.rglob("*.smt2"))
        if not mutants:
            return None
        
        # Return the most recently modified file
        return max(mutants, key=lambda p: p.stat().st_mtime)
    
    def cleanup_old_mutants(self, scratch_folder: Path, current_mutant: Path, keep_recent: int = 5):
        """
        Clean up old mutant files to avoid disk overflow.
        Keeps the current mutant and the N most recent mutants, deletes the rest.
        """
        if not scratch_folder.exists():
            return
        
        mutants = list(scratch_folder.rglob("*.smt2"))
        if len(mutants) <= keep_recent:
            return  # Not enough mutants to clean up
        
        # Sort by modification time (newest first)
        mutants_sorted = sorted(mutants, key=lambda p: p.stat().st_mtime, reverse=True)
        
        # Keep current mutant and keep_recent most recent
        to_keep = {current_mutant}
        to_keep.update(mutants_sorted[:keep_recent])
        
        # Delete old mutants
        deleted_count = 0
        for mutant in mutants_sorted[keep_recent:]:
            if mutant not in to_keep:
                try:
                    mutant.unlink()
                    deleted_count += 1
                except Exception:
                    pass
        
        if deleted_count > 0:
            print(f"  (cleaned up {deleted_count} old mutants)", end="", flush=True)
    
    def extract_coverage_data(self, output_file: Path, jobs: int = 1) -> Optional[Dict]:
        """Extract coverage data using fastcov and return JSON data"""
        # Run fastcov to generate coverage JSON
        result = subprocess.run([
            "fastcov", "--gcov", "gcov", "--search-directory", str(self.build_dir),
            "--output", str(output_file),
            "--exclude", "/usr/include/*",
            "--exclude", "*/deps/*",
            "--jobs", str(jobs)
        ], cwd=self.build_dir.parent, capture_output=True, text=True, check=False)
        
        if result.returncode != 0:
            print(f"Error running fastcov: {result.stderr}", file=sys.stderr)
            return None

        # Load and return the JSON data
        try:
            with open(output_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error parsing fastcov JSON: {e}", file=sys.stderr)
            return None

    def count_arcs_from_fastcov(self, coverage_data: Dict, cvc5_only: bool = True) -> Tuple[int, int]:
        """
        Count covered arcs and total arcs from fastcov JSON.
        Returns: (covered_arcs, total_arcs)
        
        Note: fastcov reports branch coverage, which is equivalent to arc coverage.
        Fastcov JSON structure: sources[file_path][line_number]['branches'] = [branch_info, ...]
        """
        covered_arcs = 0
        total_arcs = 0
        
        if 'sources' not in coverage_data:
            return 0, 0
        
        for file_path, file_data in coverage_data['sources'].items():
            # Filter to cvc5 source files only
            if cvc5_only and not self.is_cvc5_source_file(file_path):
                continue
            
            # Fastcov stores branches per line number
            # Also check for branches at root level (like functions are stored)
            def count_branches(branches):
                """Helper to count branches from various formats"""
                local_covered = 0
                local_total = 0
                
                if isinstance(branches, list):
                    for branch in branches:
                        local_total += 1
                        if isinstance(branch, dict):
                            count = branch.get('count', 0)
                            if count > 0:
                                local_covered += 1
                        elif isinstance(branch, (int, float)) and branch > 0:
                            local_covered += 1
                elif isinstance(branches, dict):
                    for branch_info in branches.values():
                        local_total += 1
                        if isinstance(branch_info, dict):
                            count = branch_info.get('count', 0)
                            if count > 0:
                                local_covered += 1
                        elif isinstance(branch_info, (int, float)) and branch_info > 0:
                            local_covered += 1
                
                return local_covered, local_total
            
            if isinstance(file_data, dict):
                # Check root level branches (like functions)
                if '' in file_data and isinstance(file_data[''], dict):
                    if 'branches' in file_data['']:
                        cov, tot = count_branches(file_data['']['branches'])
                        covered_arcs += cov
                        total_arcs += tot
                
                # Check branches per line number
                for line_num, line_data in file_data.items():
                    if line_num == '':  # Already handled above
                        continue
                    if isinstance(line_data, dict) and 'branches' in line_data:
                        cov, tot = count_branches(line_data['branches'])
                        covered_arcs += cov
                        total_arcs += tot
        
        return covered_arcs, total_arcs
    
    def is_cvc5_source_file(self, file_path: str) -> bool:
        """Check if a file path belongs to the cvc5 project"""
        has_src_dir = 'src/' in file_path
        
        excluded_patterns = [
            '/usr/include/', '/usr/lib/', '/System/', '/Library/',
            '/Applications/', '/opt/', '/deps/', '/build/deps/',
            '/build/src/', '/build/', '/include/', '/lib/',
            '/bin/', '/share/', 'CMakeFiles/', 'cmake/', 'Makefile'
        ]
        
        has_excluded_pattern = any(exclude in file_path for exclude in excluded_patterns)
        
        return has_src_dir and not has_excluded_pattern
    
    
    def run(self, test_name: str, timeout: Optional[int] = 300, iterations: int = 2147483647,
            keep_mutants: int = 5):
        """
        Run fuzzer on a test with coverage recorded after each mutant.
        Each mutant becomes the seed for the next iteration.
        
        Args:
            test_name: Test file to start with
            timeout: Total timeout in seconds (None = no timeout, run until iterations exhausted)
            iterations: Maximum number of iterations to run
            keep_mutants: Number of recent mutants to keep (for disk cleanup)
        """
        import time
        
        test_path = self.tests_root / test_name
        
        if not test_path.exists():
            print(f"Error: Test file not found: {test_path}")
            sys.exit(1)
        
        print(f"Running fuzzer on: {test_name}")
        print(f"Test path: {test_path}")
        if timeout:
            print(f"Total timeout: {timeout}s, Max iterations: {iterations}")
        else:
            print(f"No timeout, Max iterations: {iterations}")
        
        # Create temporary directories for isolation
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            bugs_folder = temp_path / "bugs"
            scratch_folder = temp_path / "scratch"
            log_folder = temp_path / "logs"
            
            # Step 1: Run typefuzz iteratively, using each mutant as input for next iteration
            print(f"\n[1/1] Running typefuzz with coverage recording after each mutant...")
            print("="*60)
            
            coverage_samples: List[Tuple[int, int, float]] = []  # (iteration, arcs, elapsed_time)
            start_time = time.time()
            
            # Start with the original test file
            current_input = test_path
            
            # Run iterations one at a time
            for iteration in range(1, iterations + 1):
                # Check total timeout (if set)
                if timeout:
                    elapsed_total = time.time() - start_time
                    if elapsed_total >= timeout:
                        print(f"\nTotal timeout ({timeout}s) reached at iteration {iteration}")
                        break
                
                # Reset coverage BEFORE each iteration to measure coverage from this iteration only
                # This ensures we measure coverage from the current mutant/test, not accumulated
                self.reset_coverage_counters()
                
                # Run single iteration on current input (seed or previous mutant)
                print(f"Iteration {iteration}/{iterations}...", end=" ", flush=True)
                
                success = self.run_typefuzz_single_iteration(
                    current_input, bugs_folder, scratch_folder, log_folder
                )
                
                if not success:
                    print("✗ typefuzz failed, stopping")
                    break
                
                # Record coverage after this iteration
                fastcov_temp = temp_path / f"coverage_iter_{iteration}.json"
                coverage_data = self.extract_coverage_data(fastcov_temp, jobs=FASTCOV_JOBS)
                
                elapsed_total = time.time() - start_time if timeout else 0
                
                if coverage_data:
                    covered_arcs, _ = self.count_arcs_from_fastcov(coverage_data)
                    coverage_samples.append((iteration, covered_arcs, elapsed_total))
                    time_str = f" (total: {elapsed_total:.1f}s)" if timeout else ""
                    print(f"✓ Arcs: {covered_arcs:,}{time_str}")
                    sys.stdout.flush()
                else:
                    print("✗ Failed to extract coverage")
                    sys.stdout.flush()
                
                # Find the latest mutant to use as input for next iteration
                # Note: With -k flag, mutants are kept. If mutation failed, no file is created.
                latest_mutant = self.find_latest_mutant(scratch_folder)
                if latest_mutant:
                    # Clean up old mutants before using the new one
                    if iteration > 1:  # Don't clean up on first iteration
                        self.cleanup_old_mutants(scratch_folder, latest_mutant, keep_mutants)
                    
                    current_input = latest_mutant
                else:
                    # No mutant found - mutation may have failed or no mutant was generated
                    print("✗ No mutant found (mutation may have failed), stopping")
                    break
                
                # Clean up temp coverage file
                try:
                    fastcov_temp.unlink()
                except:
                    pass
                
                # Check if we've exceeded total timeout (if set)
                if timeout:
                    elapsed_total = time.time() - start_time
                    if elapsed_total >= timeout:
                        print(f"\nTotal timeout ({timeout}s) reached")
                        break
            
            print("="*60)
            
            # Report results
            if coverage_samples:
                print("\n" + "="*60)
                print("ARC COVERAGE RESULTS")
                print("="*60)
                final_arcs = coverage_samples[-1][1]
                print(f"Final arcs covered: {final_arcs:,}")
                
                print(f"\nCoverage after each mutant (total {len(coverage_samples)} iterations):")
                print(f"{'Iter':<8} {'Arcs':<12} {'Time (s)':<12} {'New Arcs':<12}")
                print("-" * 48)
                prev_arcs = 0
                for iter_num, arcs, elapsed in coverage_samples:
                    new_arcs = arcs - prev_arcs
                    print(f"{iter_num:<8} {arcs:<12,} {elapsed:<12.1f} {new_arcs:<12,}")
                    prev_arcs = arcs
                print("="*60)
            else:
                print("\nNo coverage samples recorded")
    

def main():
    parser = argparse.ArgumentParser(
        description='Run typefuzz on a test and measure arc coverage'
    )
    parser.add_argument('test', help='Test name (relative to tests-root)')
    parser.add_argument('--build-dir', default='build', help='Build directory (default: build)')
    parser.add_argument('--tests-root', default='test/regress/cli', 
                       help='Root directory for tests (default: test/regress/cli)')
    parser.add_argument('--timeout', type=int, default=300,
                       help='Total timeout for all fuzzing in seconds. Use 0 for no timeout (default: 300)')
    parser.add_argument('-i', '--iterations', type=int, default=2147483647,
                       help='Maximum number of iterations (mutants) to generate (default: 2147483647)')
    parser.add_argument('--keep-mutants', type=int, default=5,
                       help='Number of recent mutants to keep on disk (default: 5)')
    
    args = parser.parse_args()
    
    fuzzer = CommitFuzzer(build_dir=args.build_dir, tests_root=args.tests_root)
    fuzzer.run(
        test_name=args.test,
        timeout=args.timeout if args.timeout > 0 else None,
        iterations=args.iterations,
        keep_mutants=args.keep_mutants
    )


if __name__ == "__main__":
    main()


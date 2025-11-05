#!/usr/bin/env python3
"""
Simple commit fuzzer that runs typefuzz on tests with multiple solvers.
Robust implementation that always succeeds and manages parallel execution.
"""

import argparse
import gc
import json
import multiprocessing
import os
import psutil
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple


class SimpleCommitFuzzer:
    """Robust fuzzer that manages parallel execution with shared queue."""
    
    # Typefuzz exit codes
    EXIT_CODE_BUGS_FOUND = 10
    EXIT_CODE_UNSUPPORTED = 3
    EXIT_CODE_SUCCESS = 0
    
    # Resource monitoring thresholds
    RESOURCE_CONFIG = {
        'cpu_warning': 80.0,          # %
        'cpu_critical': 90.0,         # %
        'memory_warning': 80.0,       # %
        'memory_critical': 90.0,       # %
        'process_warning': 200,        # count
        'process_critical': 240,       # count
        'check_interval': 5,           # seconds
        'pause_duration': 10,          # seconds when critical
        'max_process_memory_mb': 500,  # Kill if process exceeds this
    }
    
    def __init__(
        self,
        tests: List[str],
        tests_root: str,
        bugs_folder: str = "bugs",
        num_workers: int = 4,
        iterations: int = 2147483647,
        time_remaining: Optional[int] = None,
        job_start_time: Optional[float] = None,
        stop_buffer_minutes: int = 5,
        z3_old_path: Optional[str] = None,
        cvc4_path: Optional[str] = None,
        cvc5_path: str = "./build/bin/cvc5",
        job_id: Optional[str] = None,
    ):
        self.tests = tests
        self.tests_root = Path(tests_root)
        self.bugs_folder = Path(bugs_folder)
        self.num_workers = num_workers
        self.iterations = iterations
        self.job_id = job_id
        self.start_time = time.time()
        
        # Compute remaining time if job_start_time is provided
        if job_start_time is not None:
            self.time_remaining = self._compute_time_remaining(job_start_time, stop_buffer_minutes)
            print(f"[DEBUG] Job start time: {job_start_time} ({time.ctime(job_start_time)})")
            print(f"[DEBUG] Script start time: {self.start_time} ({time.ctime(self.start_time)})")
            build_time = self.start_time - job_start_time
            print(f"[DEBUG] Build time: {build_time:.1f}s ({build_time / 60:.1f} minutes)")
            print(f"[DEBUG] Stop buffer: {stop_buffer_minutes} minutes")
            print(f"[DEBUG] Computed remaining time: {self.time_remaining}s ({self.time_remaining / 60:.1f} minutes)")
        elif time_remaining is not None:
            # Use provided time_remaining (backward compatibility)
            self.time_remaining = time_remaining
            print(f"[DEBUG] Using provided time_remaining: {time_remaining}s ({time_remaining / 60:.1f} minutes)")
        else:
            # No timeout
            self.time_remaining = None
            print("[DEBUG] No timeout set (running indefinitely)")
        
        # Solver paths
        self.z3_new = "z3"  # From PATH
        self.z3_old_path = Path(z3_old_path) if z3_old_path else None
        self.cvc4_path = Path(cvc4_path) if cvc4_path else None
        self.cvc5_path = Path(cvc5_path)
        
        # Validate solver paths
        self._validate_solvers()
        
        # Set resource limits to prevent runner communication loss
        self._set_resource_limits()
        
        # Create bugs folder
        self.bugs_folder.mkdir(parents=True, exist_ok=True)
        
        # Shared queue and locks
        self.test_queue = multiprocessing.Queue()
        self.bugs_lock = multiprocessing.Lock()
        self.shutdown_event = multiprocessing.Event()
        
        # Resource monitoring state (shared across processes)
        self.resource_state = multiprocessing.Manager().dict({
            'cpu_percent': [0.0] * psutil.cpu_count(),  # Per core
            'memory_percent': 0.0,
            'process_count': 0,
            'status': 'normal',  # normal, warning, critical
            'paused': False,
            'last_update': time.time(),
        })
        self.resource_lock = multiprocessing.Lock()
        
        # Statistics
        self.stats = multiprocessing.Manager().dict({
            'tests_processed': 0,
            'bugs_found': 0,
            'tests_removed_unsupported': 0,
            'tests_removed_timeout': 0,
            'tests_requeued': 0,
        })
    
    def _validate_solvers(self):
        """Validate that all solver paths exist."""
        if not shutil.which(self.z3_new):
            raise ValueError(f"z3 (new) not found in PATH")
        
        if self.z3_old_path and not self.z3_old_path.exists():
            raise ValueError(f"z3-old not found at: {self.z3_old_path}")
        
        if self.cvc4_path and not self.cvc4_path.exists():
            raise ValueError(f"cvc4 not found at: {self.cvc4_path}")
        
        if not self.cvc5_path.exists():
            raise ValueError(f"cvc5 not found at: {self.cvc5_path}")
    
    def _set_resource_limits(self):
        """
        Set resource limits to prevent runner communication loss.
        
        Updated for GitHub Actions: 4 cores, 16GB RAM
        Limits are more generous but still protective.
        """
        try:
            # Limit number of processes (ulimit -u)
            # 4 workers × ~50 processes per worker = ~200 processes
            # 256-512 gives us headroom
            resource.setrlimit(resource.RLIMIT_NPROC, (512, 512))
            print("[DEBUG] Set process limit: 512")
        except (ValueError, OSError) as e:
            print(f"[WARN] Failed to set process limit: {e}", file=sys.stderr)
        
        try:
            # Limit file descriptors (ulimit -n)
            # 4 workers × ~500 FDs per worker = ~2000 FDs
            # 2048 gives us headroom
            resource.setrlimit(resource.RLIMIT_NOFILE, (2048, 2048))
            print("[DEBUG] Set file descriptor limit: 2048")
        except (ValueError, OSError) as e:
            print(f"[WARN] Failed to set file descriptor limit: {e}", file=sys.stderr)
        
        try:
            # Limit virtual memory (ulimit -v) - 12GB in KB for 16GB total
            # More generous since we have 16GB, but still protective
            memory_limit_kb = 12 * 1024 * 1024  # 12GB
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit_kb, memory_limit_kb))
            print("[DEBUG] Set virtual memory limit: 12GB (16GB total available)")
        except (ValueError, OSError) as e:
            print(f"[WARN] Failed to set memory limit: {e}", file=sys.stderr)
        
        # Get current limits for debugging
        try:
            nproc = resource.getrlimit(resource.RLIMIT_NPROC)
            nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
            as_mem = resource.getrlimit(resource.RLIMIT_AS)
            print(f"[DEBUG] Current limits - processes: {nproc}, fds: {nofile}, memory: {as_mem[0] // (1024*1024)}GB")
        except Exception:
            pass
    
    def _monitor_resources(self):
        """Background thread to monitor CPU, memory, and process count."""
        while not self.shutdown_event.is_set():
            try:
                # Check CPU usage (per core)
                cpu_percent = psutil.cpu_percent(interval=1, percpu=True)
                
                # Check memory usage
                memory = psutil.virtual_memory()
                memory_percent = memory.percent
                
                # Check process count
                process_count = len(psutil.pids())
                
                # Determine status
                max_cpu = max(cpu_percent) if cpu_percent else 0.0
                status = 'normal'
                
                if (max_cpu >= self.RESOURCE_CONFIG['cpu_critical'] or 
                    memory_percent >= self.RESOURCE_CONFIG['memory_critical'] or
                    process_count >= self.RESOURCE_CONFIG['process_critical']):
                    status = 'critical'
                elif (max_cpu >= self.RESOURCE_CONFIG['cpu_warning'] or 
                      memory_percent >= self.RESOURCE_CONFIG['memory_warning'] or
                      process_count >= self.RESOURCE_CONFIG['process_warning']):
                    status = 'warning'
                
                # Update shared state
                with self.resource_lock:
                    self.resource_state['cpu_percent'] = cpu_percent
                    self.resource_state['memory_percent'] = memory_percent
                    self.resource_state['process_count'] = process_count
                    self.resource_state['status'] = status
                    self.resource_state['last_update'] = time.time()
                
                # Take action if needed
                if status == 'critical':
                    self._handle_critical_resources()
                elif status == 'warning':
                    self._handle_warning_resources()
                
                # Sleep until next check
                time.sleep(self.RESOURCE_CONFIG['check_interval'])
                
            except Exception as e:
                print(f"[WARN] Error in resource monitoring: {e}", file=sys.stderr)
                time.sleep(self.RESOURCE_CONFIG['check_interval'])
    
    def _handle_warning_resources(self):
        """Handle warning-level resource usage."""
        # Force cleanup of temp files
        try:
            gc.collect()  # Python garbage collection
        except Exception:
            pass
    
    def _handle_critical_resources(self):
        """Handle critical-level resource usage - take aggressive action."""
        print(f"[RESOURCE] Critical resource usage detected - taking action", file=sys.stderr)
        
        # Set paused flag
        with self.resource_lock:
            self.resource_state['paused'] = True
        
        # Force cleanup
        try:
            gc.collect()
        except Exception:
            pass
        
        # Kill processes consuming excessive memory
        try:
            main_pid = os.getpid()
            worker_pids = set()
            if hasattr(self, 'workers'):
                for w in self.workers:
                    try:
                        worker_pids.add(w.pid)
                    except (AttributeError, ValueError):
                        pass
            
            for proc in psutil.process_iter(['pid', 'name', 'memory_info', 'ppid']):
                try:
                    proc_info = proc.info
                    # Check if this is one of our processes
                    if proc_info['ppid'] == main_pid or proc_info['ppid'] in worker_pids:
                        rss_mb = proc_info['memory_info'].rss / (1024 * 1024)
                        if rss_mb > self.RESOURCE_CONFIG['max_process_memory_mb']:
                            print(f"[RESOURCE] Killing process {proc_info['pid']} ({proc_info['name']}) using {rss_mb:.1f}MB", file=sys.stderr)
                            proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                    pass
        except Exception as e:
            print(f"[WARN] Error killing processes: {e}", file=sys.stderr)
        
        # Wait a bit for resources to free up
        time.sleep(self.RESOURCE_CONFIG['pause_duration'])
        
        # Unpause
        with self.resource_lock:
            self.resource_state['paused'] = False
    
    def _check_resource_state(self) -> str:
        """Check current resource state - returns 'normal', 'warning', or 'critical'."""
        with self.resource_lock:
            return self.resource_state.get('status', 'normal')
    
    def _is_paused(self) -> bool:
        """Check if workers should pause due to resource constraints."""
        with self.resource_lock:
            return self.resource_state.get('paused', False)
    
    def _get_solver_clis(self) -> str:
        """Build solver CLIs string for typefuzz."""
        solvers = [self.z3_new]
        if self.z3_old_path:
            solvers.append(str(self.z3_old_path))
        solvers.append(str(self.cvc5_path))
        if self.cvc4_path:
            solvers.append(str(self.cvc4_path))
        return ";".join(solvers)
    
    def _compute_time_remaining(self, job_start_time: float, stop_buffer_minutes: int) -> int:
        """
        Compute remaining time for fuzzing.
        
        Logic:
        - Job runs at most 6 hours (21600 seconds)
        - Build time = script_start - job_start
        - Remaining = 6 hours - build_time - stop_buffer
        - Minimum 10 minutes
        
        Args:
            job_start_time: Unix timestamp when job started
            stop_buffer_minutes: Minutes before timeout to stop (default 5)
        
        Returns:
            Remaining time in seconds (at least 600 seconds = 10 minutes)
        """
        GITHUB_TIMEOUT = 21600  # 6 hours in seconds
        MIN_REMAINING = 600  # 10 minutes minimum
        
        build_time = self.start_time - job_start_time
        stop_buffer_seconds = stop_buffer_minutes * 60
        
        # Remaining = 6 hours - build_time - stop_buffer
        remaining = GITHUB_TIMEOUT - build_time - stop_buffer_seconds
        
        # Ensure minimum of 10 minutes
        if remaining < MIN_REMAINING:
            print(f"[DEBUG] Computed remaining time ({remaining}s) is less than minimum ({MIN_REMAINING}s), using {MIN_REMAINING}s")
            remaining = MIN_REMAINING
        
        return int(remaining)
    
    def _get_time_remaining(self) -> float:
        """Calculate remaining time in seconds."""
        if self.time_remaining is None:
            return float('inf')
        return max(0.0, self.time_remaining - (time.time() - self.start_time))
    
    def _is_time_expired(self) -> bool:
        """Check if time has expired."""
        return self.time_remaining is not None and self._get_time_remaining() <= 0
    
    def _collect_bug_files(self, folder: Path) -> List[Path]:
        """Collect bug files from a folder."""
        if not folder.exists():
            return []
        return list(folder.glob("*.smt2")) + list(folder.glob("*.smt"))
    
    def _run_typefuzz(
        self,
        test_name: str,
        worker_id: int,
        per_test_timeout: Optional[float] = None,
    ) -> Tuple[int, List[Path], float]:
        """
        Run typefuzz on a single test.
        
        Returns:
            (exit_code, bug_files, runtime)
        """
        test_path = self.tests_root / test_name
        if not test_path.exists():
            print(f"[WORKER {worker_id}] Error: Test file not found: {test_path}", file=sys.stderr)
            return (1, [], 0.0)
        
        # Per-worker folders
        bugs_folder = self.bugs_folder / f"worker_{worker_id}"
        scratch_folder = Path(f"scratch_{worker_id}")
        log_folder = Path(f"logs_{worker_id}")
        
        # Clean up and create folders
        for folder in [scratch_folder, log_folder]:
            shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
        bugs_folder.mkdir(parents=True, exist_ok=True)
        
        solver_clis = self._get_solver_clis()
        
        # Build typefuzz command
        cmd = [
            "typefuzz",
            "-i", str(self.iterations),
            "--timeout", "15",
            "--bugs", str(bugs_folder),
            "--scratch", str(scratch_folder),
            "--logfolder", str(log_folder),
            solver_clis,
            str(test_path),
        ]
        
        print(f"[WORKER {worker_id}] Running typefuzz on: {test_name} (timeout: {per_test_timeout}s)" if per_test_timeout else f"[WORKER {worker_id}] Running typefuzz on: {test_name}")
        
        start_time = time.time()
        
        try:
            # Set resource limits for subprocess to prevent resource exhaustion
            # Use preexec_fn to set limits in child process
            def set_subprocess_limits():
                try:
                    # Limit child process to reasonable defaults
                    # Each typefuzz process manages 4 solvers internally
                    # ~50 processes per typefuzz (typefuzz + 4 solvers + their children)
                    # More generous limits for 16GB RAM system
                    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))  # 128 processes per test
                    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))  # 512 FDs per test
                    # Memory limit: 3-4GB per test (more generous for 16GB system)
                    memory_limit_kb = 4 * 1024 * 1024  # 4GB per test
                    resource.setrlimit(resource.RLIMIT_AS, (memory_limit_kb, memory_limit_kb))
                except Exception:
                    pass  # Ignore errors setting limits
            
            # Run with timeout if specified
            if per_test_timeout and per_test_timeout > 0:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=per_test_timeout,
                    preexec_fn=set_subprocess_limits,
                )
            else:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    preexec_fn=set_subprocess_limits,
                )
            
            exit_code = result.returncode
            runtime = time.time() - start_time
            bug_files = self._collect_bug_files(bugs_folder)
            return (exit_code, bug_files, runtime)
            
        except subprocess.TimeoutExpired:
            # Timeout is expected - just return without logging (reduce noise)
            runtime = time.time() - start_time
            return (124, [], runtime)  # 124 is timeout exit code
        except Exception as e:
            # Errors should not stop fuzzing - silently continue (reduce noise)
            # Only critical errors that need attention should be logged
            runtime = time.time() - start_time
            return (1, [], runtime)
        finally:
            # Always cleanup temp folders (keep bugs folder)
            for folder in [scratch_folder, log_folder]:
                shutil.rmtree(folder, ignore_errors=True)
    
    def _handle_exit_code(
        self,
        test_name: str,
        exit_code: int,
        bug_files: List[Path],
        runtime: float,
        worker_id: int,
    ) -> str:
        """
        Handle typefuzz exit code and return action.
        
        Returns:
            'requeue' - add test back to queue end (bugs found)
            'remove' - remove test from queue permanently (unsupported or 32 timeouts)
            'continue' - continue with next test (other cases)
        """
        if exit_code == self.EXIT_CODE_BUGS_FOUND:
            # Bugs found - move to main bugs folder and requeue
            if bug_files:
                print(f"[WORKER {worker_id}] ✓ Exit code 10: Found {len(bug_files)} bug(s) on {test_name}")
                with self.bugs_lock:
                    for bug_file in bug_files:
                        try:
                            dest = self.bugs_folder / bug_file.name
                            shutil.move(str(bug_file), str(dest))
                            self.stats['bugs_found'] += 1
                        except Exception as e:
                            print(f"[WORKER {worker_id}] Warning: Failed to move bug file {bug_file}: {e}", file=sys.stderr)
            else:
                print(f"[WORKER {worker_id}] Warning: Exit code 10 but no bugs found for {test_name}", file=sys.stderr)
            return 'requeue'
        
        elif exit_code == self.EXIT_CODE_UNSUPPORTED:
            # Unsupported operation - remove from queue
            print(f"[WORKER {worker_id}] ⚠ Exit code 3: {test_name} (unsupported operation - removing)")
            self.stats['tests_removed_unsupported'] += 1
            return 'remove'
        
        elif exit_code == self.EXIT_CODE_SUCCESS:
            # Exit code 0 with no bugs = typefuzz stopped after 32 timeouts - remove from queue
            if not bug_files:
                print(f"[WORKER {worker_id}] Exit code 0: No bugs found on {test_name} (runtime: {runtime:.1f}s) - removing (32 timeouts)")
                self.stats['tests_removed_timeout'] += 1
                return 'remove'
            else:
                # Exit code 0 with bugs (shouldn't happen, but handle it)
                print(f"[WORKER {worker_id}] Exit code 0: {test_name} (runtime: {runtime:.1f}s) - bugs found, requeuing")
                return 'requeue'
        
        else:
            # Other exit codes (including errors/timeouts) - continue silently
            # No need to log every error/timeout to reduce noise
            return 'continue'
    
    def _worker_process(self, worker_id: int):
        """Worker process that processes tests from the queue."""
        print(f"[WORKER {worker_id}] Started")
        
        while not self.shutdown_event.is_set():
            try:
                # Check resource state - pause if critical
                if self._is_paused():
                    resource_status = self._check_resource_state()
                    print(f"[WORKER {worker_id}] Paused due to {resource_status} resource usage", file=sys.stderr)
                    time.sleep(self.RESOURCE_CONFIG['pause_duration'])
                    continue
                
                # Get test from queue with timeout to check shutdown
                try:
                    test_name = self.test_queue.get(timeout=1.0)
                except Exception:
                    # Timeout or empty queue - check if we should continue
                    if self.shutdown_event.is_set() or self._is_time_expired():
                        break
                    continue
                
                # Check if we're out of time before starting test
                if self._is_time_expired():
                    try:
                        self.test_queue.put(test_name)
                    except Exception:
                        pass
                    break
                
                # Check resource state before starting test - add small delay if warning
                resource_status = self._check_resource_state()
                if resource_status == 'warning':
                    time.sleep(2)  # Small delay to let resources recover
                elif resource_status == 'critical':
                    # Put test back and wait
                    try:
                        self.test_queue.put(test_name)
                    except Exception:
                        pass
                    time.sleep(self.RESOURCE_CONFIG['pause_duration'])
                    continue
                
                # Run typefuzz
                time_remaining = self._get_time_remaining()
                exit_code, bug_files, runtime = self._run_typefuzz(
                    test_name,
                    worker_id,
                    per_test_timeout=time_remaining if self.time_remaining and time_remaining > 0 else None,
                )
                
                # Handle exit code
                action = self._handle_exit_code(
                    test_name, exit_code, bug_files, runtime, worker_id
                )
                
                if action == 'requeue':
                    try:
                        self.test_queue.put(test_name)
                        self.stats['tests_requeued'] += 1
                    except Exception:
                        pass
                
                self.stats['tests_processed'] += 1
                
            except Exception as e:
                print(f"[WORKER {worker_id}] Error in worker: {e}", file=sys.stderr)
                continue
        
        print(f"[WORKER {worker_id}] Stopped")
    
    def run(self):
        """Run the fuzzer with parallel workers."""
        if not self.tests:
            print(f"No tests provided{' for job ' + self.job_id if self.job_id else ''}")
            return
        
        print(f"Running fuzzer on {len(self.tests)} test(s){' for job ' + self.job_id if self.job_id else ''}")
        print(f"Tests root: {self.tests_root}")
        print(f"Timeout: {self.time_remaining}s ({self.time_remaining // 60} minutes)" if self.time_remaining else "No timeout")
        print(f"Iterations per test: {self.iterations}")
        print(f"Workers: {self.num_workers}")
        print(f"Solvers: z3-new={self.z3_new}, z3-old={self.z3_old_path}, cvc5={self.cvc5_path}, cvc4={self.cvc4_path}")
        print()
        
        # Initialize queue with all tests
        for test in self.tests:
            self.test_queue.put(test)
        
        # Start workers
        workers = []
        for worker_id in range(1, self.num_workers + 1):
            worker = multiprocessing.Process(target=self._worker_process, args=(worker_id,))
            worker.start()
            workers.append(worker)
        
        # Store workers for resource monitoring
        self.workers = workers
        
        # Start resource monitoring thread
        monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        monitor_thread.start()
        print("[DEBUG] Resource monitoring started")
        
        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            print("\n⏰ Shutdown signal received, stopping workers...")
            self.shutdown_event.set()
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
        
        try:
            # Wait for workers or timeout
            if self.time_remaining:
                # Wait with timeout
                end_time = self.start_time + self.time_remaining
                while time.time() < end_time and any(w.is_alive() for w in workers):
                    time.sleep(1)
                if time.time() >= end_time:
                    print("⏰ Timeout reached, stopping workers...")
                    self.shutdown_event.set()
            else:
                # Wait indefinitely
                for worker in workers:
                    worker.join()
        except KeyboardInterrupt:
            print("\n⏰ Interrupted, stopping workers...")
            self.shutdown_event.set()
        
        # Wait for workers to finish
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                print(f"Warning: Worker {worker.ident} did not terminate, killing...")
                worker.terminate()
                worker.join(timeout=2)
                if worker.is_alive():
                    worker.kill()
        
        # Collect bugs from worker folders
        for worker_id in range(1, self.num_workers + 1):
            worker_bugs = self.bugs_folder / f"worker_{worker_id}"
            for bug_file in self._collect_bug_files(worker_bugs):
                try:
                    shutil.move(str(bug_file), str(self.bugs_folder / bug_file.name))
                except Exception:
                    pass
        
        # Print summary
        print()
        print("=" * 60)
        print(f"FINAL BUG SUMMARY{' FOR JOB ' + self.job_id if self.job_id else ''}")
        print("=" * 60)
        
        bug_files = self._collect_bug_files(self.bugs_folder)
        if bug_files:
            print(f"\nFound {len(bug_files)} bug(s):")
            for i, bug_file in enumerate(bug_files, 1):
                print(f"\nBug #{i}: {bug_file}")
                print("-" * 60)
                try:
                    with open(bug_file, 'r') as f:
                        print(f.read())
                except Exception as e:
                    print(f"Error reading bug file: {e}")
                print("-" * 60)
        else:
            print("No bugs found.")
        
        print()
        print("Statistics:")
        print(f"  Tests processed: {self.stats.get('tests_processed', 0)}")
        print(f"  Bugs found: {self.stats.get('bugs_found', 0)}")
        print(f"  Tests requeued (bugs found): {self.stats.get('tests_requeued', 0)}")
        print(f"  Tests removed (unsupported): {self.stats.get('tests_removed_unsupported', 0)}")
        print(f"  Tests removed (timeout): {self.stats.get('tests_removed_timeout', 0)}")
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Simple commit fuzzer that runs typefuzz on tests with multiple solvers"
    )
    parser.add_argument(
        "--tests-json",
        required=True,
        help="JSON array of test names (relative to --tests-root)",
    )
    parser.add_argument(
        "--job-id",
        help="Job identifier (optional, for logging)",
    )
    parser.add_argument(
        "--tests-root",
        default="test/regress/cli",
        help="Root directory for tests (default: test/regress/cli)",
    )
    parser.add_argument(
        "--time-remaining",
        type=int,
        help="Remaining time until job timeout in seconds (legacy, use --job-start-time instead)",
    )
    parser.add_argument(
        "--job-start-time",
        type=float,
        help="Unix timestamp when the job started (for automatic time calculation)",
    )
    parser.add_argument(
        "--stop-buffer-minutes",
        type=int,
        default=5,
        help="Minutes before timeout to stop (default: 5, can be set higher for testing)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=2147483647,
        help="Number of iterations per test (default: 2147483647)",
    )
    parser.add_argument(
        "--z3-old-path",
        required=True,
        help="Path to z3-4.8.7 binary",
    )
    parser.add_argument(
        "--cvc4-path",
        required=True,
        help="Path to cvc4-1.6 binary",
    )
    parser.add_argument(
        "--cvc5-path",
        default="./build/bin/cvc5",
        help="Path to cvc5 binary (default: ./build/bin/cvc5)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of worker processes (default: 4). Each worker runs typefuzz with 4 solvers, so 4 workers = ~16 concurrent solver processes",
    )
    parser.add_argument(
        "--bugs-folder",
        default="bugs",
        help="Folder to store bugs (default: bugs)",
    )
    
    args = parser.parse_args()
    
    # Parse tests JSON
    try:
        tests = json.loads(args.tests_json)
        if not isinstance(tests, list):
            raise ValueError("tests-json must be a JSON array")
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in --tests-json: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Create and run fuzzer
    try:
        fuzzer = SimpleCommitFuzzer(
            tests=tests,
            tests_root=args.tests_root,
            bugs_folder=args.bugs_folder,
            num_workers=args.workers,
            iterations=args.iterations,
            time_remaining=args.time_remaining,
            job_start_time=args.job_start_time,
            stop_buffer_minutes=args.stop_buffer_minutes,
            z3_old_path=args.z3_old_path,
            cvc4_path=args.cvc4_path,
            cvc5_path=args.cvc5_path,
            job_id=args.job_id,
        )
        fuzzer.run()
        # Always exit with success
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        # Still exit with success to not fail the workflow
        sys.exit(0)


if __name__ == "__main__":
    main()


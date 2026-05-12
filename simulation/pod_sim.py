"""
Pod Simulator
=============
Estimates the number of pods (compute units) needed to handle
a classroom's job submissions without excessive timeouts.

Sweeps from 1 → MAX_PODS and reports the minimum number of pods
that keeps the job drop rate at or below DROP_RATE_TARGET.

Replace the PARAMETERS section with real values once you have them.
Drop in your real CSV by setting CSV_PATH below.
"""

import csv
import copy
import time
import heapq
import random
from dataclasses import dataclass, field
from typing import Optional

# ─────────────────────────────────────────────
# PARAMETERS  ← edit these
# ─────────────────────────────────────────────

CSV_PATH               = "mock_submissions_300.csv"  # Set to None to use synthetic mock data.

MAX_PODS               = 8      # Sweep will try 1 pod up to this many, then stop.

DROP_RATE_TARGET       = 0.05   # Stop sweep at the first pod count where drop rate ≤ this.
                                # e.g. 0.05 = allow at most 5% of jobs to time out.

TIMEOUT_MS             = 500    # Max time (ms) a job may wait in queue before being dropped.

GRADESCOPE_OVERHEAD_MS = 10472.196608   # Delay (ms): student clicks submit → job visible to scheduler.
POD_CREATION_OVERHEAD  = 117.4208       # Additional delay (ms) for pod spin-up per job.
OVERHEAD_JITTER_MS     = 5              # ± random jitter on top of the combined overhead (ms).
                                        # Set to 0 for a fixed, deterministic overhead.

SPEED_MULTIPLIER       = float("inf")   # Sim speed. float('inf') = run instantly.
                                        # Lower values (e.g. 10) pace the sim to real time.

VERBOSE                = False   # True  = print every event (arrival / dispatch / finish).
                                 # False = silent per-run; only the summary table is shown.

# ── Mock data settings (only used when CSV_PATH is None) ──────────────────────
MOCK_NUM_JOBS     = 300         # Number of synthetic jobs to generate
MOCK_CLASS_WINDOW = 120 * 1000  # Spread submissions over this window (ms)
MOCK_JOB_MIN      = 30          # Min job duration in ms
MOCK_JOB_MAX      = 2000        # Max job duration in ms
RANDOM_SEED       = 42          # For reproducibility; set to None for random each run

# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass(order=True)
class Event:
    """A simulation event ordered by sim-time."""
    time: float
    kind: str           = field(compare=False)   # 'arrival' | 'completion'
    job_id: int         = field(compare=False)
    job_duration: float = field(compare=False, default=0.0)
    pod_id: Optional[int] = field(compare=False, default=None)


@dataclass
class Job:
    job_id: int
    submission_time: float      # When the student clicked submit
    arrival_time: float         # When the job enters the scheduler (submission + overhead)
    duration: float
    overhead: float = 0.0       # Actual overhead applied to this job (ms)
    start_time: Optional[float] = None
    finish_time: Optional[float] = None
    timed_out: bool = False
    pod_id: Optional[int] = None

    @property
    def wait_time(self) -> Optional[float]:
        """Time spent in the queue after arriving at the scheduler."""
        if self.start_time is None:
            return None
        return self.start_time - self.arrival_time

    @property
    def total_latency(self) -> Optional[float]:
        """End-to-end time from student submission to job completion."""
        if self.finish_time is None:
            return None
        return self.finish_time - self.submission_time


# ─────────────────────────────────────────────
# CSV / MOCK DATA LOADING
# ─────────────────────────────────────────────

def parse_time(s: str) -> float:
    """
    Convert a time string → milliseconds.
    Handles: HH:MM  |  HH:MM:SS  |  HH:MM:SS.mmm
    """
    parts = s.strip().split(":")
    if len(parts) == 2:
        h, m = parts
        total_sec = int(h) * 3600 + int(m) * 60
    elif len(parts) == 3:
        h, m, sec = parts
        total_sec = int(h) * 3600 + int(m) * 60 + float(sec)
    else:
        raise ValueError(f"Unrecognised time format: {s!r}")
    return total_sec * 1000


def apply_overhead(jobs: list[Job], overhead_ms: float, jitter_ms: float, seed) -> list[Job]:
    """
    Apply submission + pod-creation overhead to each job.
    submission_time is preserved; arrival_time = submission_time + overhead + jitter.
    """
    rng = random.Random(seed)
    for job in jobs:
        jitter           = rng.uniform(-jitter_ms, jitter_ms) if jitter_ms > 0 else 0.0
        actual_overhead  = max(0.0, overhead_ms + jitter)
        job.overhead     = actual_overhead
        job.arrival_time = job.submission_time + actual_overhead
    return jobs


def load_csv(path: str) -> list[Job]:
    """
    Load jobs from a CSV file.
    Expected columns (case-insensitive):
      submission_time  — HH:MM, HH:MM:SS, or HH:MM:SS.mmm
      job_time         — duration in milliseconds
    """
    jobs = []
    with open(path, newline="") as f:
        reader     = csv.DictReader(f)
        raw_fields = reader.fieldnames or []
        field_map  = {name.strip().lower().replace(" ", "_"): name for name in raw_fields}

        time_col     = field_map.get("submission_time") or field_map.get("time")
        duration_col = field_map.get("job_time") or field_map.get("duration")

        if not time_col or not duration_col:
            raise ValueError(
                f"Could not find expected columns. Got: {raw_fields}\n"
                "Need 'submission_time' (or 'time') and 'job_time' (or 'duration')."
            )

        for i, row in enumerate(reader):
            sub_time = parse_time(row[time_col])
            duration = float(row[duration_col])
            jobs.append(Job(job_id=i, submission_time=sub_time,
                            arrival_time=sub_time, duration=duration))

    # Re-zero so the first submission is at t=0
    if jobs:
        t0 = min(j.submission_time for j in jobs)
        for j in jobs:
            j.submission_time -= t0
            j.arrival_time    -= t0

    return jobs


def generate_mock_jobs() -> list[Job]:
    """Generate synthetic jobs when no CSV is provided."""
    rng  = random.Random(RANDOM_SEED)
    jobs = []
    for i in range(MOCK_NUM_JOBS):
        sub_time = rng.uniform(0, MOCK_CLASS_WINDOW)
        duration = rng.uniform(MOCK_JOB_MIN, MOCK_JOB_MAX)
        jobs.append(Job(job_id=i, submission_time=sub_time,
                        arrival_time=sub_time, duration=duration))
    jobs.sort(key=lambda j: j.submission_time)
    t0 = jobs[0].submission_time
    for j in jobs:
        j.submission_time -= t0
        j.arrival_time    -= t0
    return jobs


# ─────────────────────────────────────────────
# SIMULATOR
# ─────────────────────────────────────────────

def run_simulation(jobs: list[Job], num_pods: int, timeout: float,
                   speed: float, verbose: bool) -> list[Job]:
    """
    Discrete-event simulation over a deep copy of jobs.

    Always deep-copies the input so repeated calls with the same base_jobs
    list never bleed state between runs.

    All times are in milliseconds.
    Returns the copied + populated list of Job objects.
    """
    jobs = copy.deepcopy(jobs)

    event_queue: list[Event] = []
    pod_idle   = [True] * num_pods
    wait_queue: list[Job]    = []

    for job in jobs:
        heapq.heappush(event_queue, Event(
            time=job.arrival_time, kind="arrival",
            job_id=job.job_id, job_duration=job.duration,
        ))

    job_map        = {j.job_id: j for j in jobs}
    sim_start_wall = time.time()

    def wall_sleep(until: float):
        if speed == float("inf"):
            return
        delta = (sim_start_wall + until / speed) - time.time()
        if delta > 0:
            time.sleep(delta)

    def try_dispatch(current_time: float):
        while wait_queue:
            candidate   = wait_queue[0]
            wait_so_far = current_time - candidate.arrival_time

            if wait_so_far > timeout:
                wait_queue.pop(0)
                candidate.timed_out = True
                if verbose:
                    print(f"  [t={current_time:12.1f}ms] ✗ Job {candidate.job_id:3d} "
                          f"TIMED OUT (waited {wait_so_far:.1f}ms)")
                continue

            idle_pod = next((i for i, idle in enumerate(pod_idle) if idle), None)
            if idle_pod is None:
                break

            wait_queue.pop(0)
            pod_idle[idle_pod]    = False
            candidate.start_time  = current_time
            candidate.pod_id      = idle_pod
            finish                = current_time + candidate.duration
            candidate.finish_time = finish

            heapq.heappush(event_queue, Event(
                time=finish, kind="completion",
                job_id=candidate.job_id, pod_id=idle_pod,
            ))
            if verbose:
                print(f"  [t={current_time:12.1f}ms] → Job {candidate.job_id:3d} "
                      f"on Pod {idle_pod} "
                      f"(wait {wait_so_far:.1f}ms, dur {candidate.duration:.1f}ms, "
                      f"done t={finish:.1f}ms)")

    if verbose:
        print(f"\n{'='*64}")
        print(f"  pods={num_pods} | timeout={timeout}ms | speed={speed}x")
        print(f"{'='*64}\n")

    while event_queue:
        event    = heapq.heappop(event_queue)
        sim_time = event.time
        wall_sleep(sim_time)

        if event.kind == "arrival":
            job = job_map[event.job_id]
            if verbose:
                print(f"  [t={sim_time:12.1f}ms] ↓ Job {job.job_id:3d} arrived "
                      f"(dur {job.duration:.1f}ms)")
            wait_queue.append(job)
            try_dispatch(sim_time)

        elif event.kind == "completion":
            pod_id           = event.pod_id
            pod_idle[pod_id] = True
            if verbose:
                job = job_map[event.job_id]
                print(f"  [t={sim_time:12.1f}ms] ✓ Job {job.job_id:3d} "
                      f"finished on Pod {pod_id} (ran {job.duration:.1f}ms)")
            try_dispatch(sim_time)

    for leftover in wait_queue:
        leftover.timed_out = True

    return jobs


# ─────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────

def compute_stats(jobs: list[Job]) -> dict:
    """Return summary statistics for a completed simulation run."""
    completed  = [j for j in jobs if not j.timed_out]
    timed_out  = [j for j in jobs if j.timed_out]
    total      = len(jobs)

    wait_times = [j.wait_time     for j in completed if j.wait_time     is not None]
    latencies  = [j.total_latency for j in completed if j.total_latency is not None]
    overheads  = [j.overhead      for j in jobs]

    return {
        "total":        total,
        "completed":    len(completed),
        "timed_out":    len(timed_out),
        "drop_rate":    len(timed_out) / total if total else 0,
        "avg_wait":     sum(wait_times) / len(wait_times) if wait_times else 0,
        "max_wait":     max(wait_times)                   if wait_times else 0,
        "avg_latency":  sum(latencies)  / len(latencies)  if latencies  else 0,
        "avg_overhead": sum(overheads)  / len(overheads)  if overheads  else 0,
    }


def print_sweep_table(results: list[tuple[int, dict]], target: float):
    """Print a compact comparison table of all sweep results."""
    print(f"\n{'='*74}")
    print(f"  SWEEP RESULTS  (drop rate target ≤ {target*100:.1f}%)")
    print(f"{'='*74}")
    print(f"  {'Pods':>5}  {'Completed':>10}  {'Dropped':>8}  "
          f"{'Drop %':>7}  {'Avg wait':>9}  {'Max wait':>9}  {'Target':>8}")
    print(f"  {'-'*5}  {'-'*10}  {'-'*8}  {'-'*7}  {'-'*9}  {'-'*9}  {'-'*8}")
    for num_pods, s in results:
        meets = "✓  YES" if s["drop_rate"] <= target else "✗  no"
        print(f"  {num_pods:>5}  {s['completed']:>10}  {s['timed_out']:>8}  "
              f"  {s['drop_rate']*100:>5.1f}%  "
              f"{s['avg_wait']:>8.1f}ms  {s['max_wait']:>8.1f}ms  {meets:>8}")
    print(f"{'='*74}")


def print_final_report(num_pods: int, stats: dict, timeout: float,
                       overhead_ms: float, jitter_ms: float, target: float):
    """Detailed report for the recommended pod count."""
    print(f"\n{'='*64}")
    print(f"  RECOMMENDATION")
    print(f"{'='*64}")
    print(f"  Minimum pods to meet target : {num_pods}")
    print(f"  Drop rate target            : ≤ {target*100:.1f}%")
    print(f"  Actual drop rate            : {stats['drop_rate']*100:.2f}%")
    print(f"  Timeout threshold           : {timeout}ms")
    print(f"  Combined overhead           : {overhead_ms:.3f}ms ± {jitter_ms}ms jitter")
    print(f"  Avg actual overhead         : {stats['avg_overhead']:.2f}ms")
    print(f"  Total jobs                  : {stats['total']}")
    print(f"  Completed                   : {stats['completed']}  "
          f"({100*stats['completed']/stats['total']:.1f}%)")
    print(f"  Timed out / dropped         : {stats['timed_out']}  "
          f"({100*stats['timed_out']/stats['total']:.1f}%)")
    print(f"  Avg queue wait time         : {stats['avg_wait']:.2f}ms")
    print(f"  Max queue wait time         : {stats['max_wait']:.2f}ms")
    print(f"  Avg total latency           : {stats['avg_latency']:.2f}ms  (submit → done)")
    print(f"{'='*64}\n")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # ── Load data ────────────────────────────
    if CSV_PATH:
        print(f"Loading jobs from: {CSV_PATH}")
        base_jobs = load_csv(CSV_PATH)
    else:
        print("No CSV_PATH set — using synthetic mock data.")
        base_jobs = generate_mock_jobs()

    print(f"Loaded {len(base_jobs)} jobs.")

    # ── Apply overhead once to the base job list ──────────────────────────────
    # run_simulation deep-copies base_jobs on every call, so overhead only
    # needs to be applied once here — it will be preserved across all runs.
    total_overhead = GRADESCOPE_OVERHEAD_MS + POD_CREATION_OVERHEAD
    base_jobs = apply_overhead(base_jobs, total_overhead, OVERHEAD_JITTER_MS, seed=RANDOM_SEED)
    print(f"Overhead applied: {total_overhead:.3f}ms "
          f"({GRADESCOPE_OVERHEAD_MS}ms Gradescope + {POD_CREATION_OVERHEAD}ms pod creation) "
          f"± {OVERHEAD_JITTER_MS}ms jitter\n")

    # ── Sweep pod counts 1 → MAX_PODS ────────────────────────────────────────
    print(f"Sweeping 1 to {MAX_PODS} pod(s), target drop rate ≤ {DROP_RATE_TARGET*100:.1f}%\n")

    sweep_results  = []   # [(num_pods, stats), ...]
    recommendation = None

    for num_pods in range(1, MAX_PODS + 1):
        print(f"  [{num_pods}/{MAX_PODS}] Simulating {num_pods} pod(s)...", end="", flush=True)

        result_jobs = run_simulation(
            base_jobs, num_pods=num_pods, timeout=TIMEOUT_MS,
            speed=SPEED_MULTIPLIER, verbose=VERBOSE,
        )
        stats = compute_stats(result_jobs)
        sweep_results.append((num_pods, stats))

        print(f"  drop={stats['drop_rate']*100:.1f}%  "
              f"completed={stats['completed']}/{stats['total']}  "
              f"avg_wait={stats['avg_wait']:.1f}ms")

        if recommendation is None and stats["drop_rate"] <= DROP_RATE_TARGET:
            recommendation = (num_pods, stats)
            print(f"\n  ★  Target met at {num_pods} pod(s) — stopping sweep.\n")
            break

    # ── Summary table ─────────────────────────────────────────────────────────
    print_sweep_table(sweep_results, DROP_RATE_TARGET)

    # ── Final recommendation ──────────────────────────────────────────────────
    if recommendation:
        print_final_report(
            num_pods    = recommendation[0],
            stats       = recommendation[1],
            timeout     = TIMEOUT_MS,
            overhead_ms = total_overhead,
            jitter_ms   = OVERHEAD_JITTER_MS,
            target      = DROP_RATE_TARGET,
        )
    else:
        print(f"\n  ⚠  Target of ≤{DROP_RATE_TARGET*100:.1f}% drop rate NOT met within "
              f"{MAX_PODS} pods.")
        best_pods, best_stats = sweep_results[-1]
        print(f"     Best result: {best_stats['drop_rate']*100:.2f}% drop rate "
              f"at {best_pods} pod(s).")
        print(f"     Try: raise MAX_PODS, increase TIMEOUT_MS, "
              f"or relax DROP_RATE_TARGET.\n")

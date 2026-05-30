"""
================
Estimates the number of pods (compute units) needed to handle both:
  • Interactive development sessions  (HIGH priority)
  • Batch autograder job submissions   (LOW priority)

Sessions always jump ahead of batch jobs in the queue. Sessions never
time out — they wait until a pod is free. If a session's total duration
exceeds MAX_SESSION_HOURS, it runs for exactly that cap, then the
remaining time is re-queued back into the session queue (high priority).

Batch jobs time out after BATCH_TIMEOUT_MS if no pod becomes available.

Sweeps 1 → MAX_PODS using binary search and reports the minimum pod
count that keeps the batch drop rate at or below BATCH_DROP_TARGET.
(Sessions never drop, so there is no session drop target.)

─── Input CSVs ───────────────────────────────────────────────────────
Submissions CSV columns (required):
  student_id, attempt_number, submission_time, runtime_ms, score

  submission_time  : ISO-8601 datetime  (e.g. 2026-03-10T21:14:29-07:00)
  runtime_ms       : job duration in ms (blank or -1 → row is skipped)

Sessions CSV columns (required):
  timestamp        : ISO-8601 datetime  — when the session starts
  length_seconds   : session duration in seconds
  student_id       : carried through for reporting
"""

import csv
import copy
import heapq
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
# PARAMETERS  ← edit these
# ─────────────────────────────────────────────

SUBMISSIONS_CSV = "pa8_hours_pa8_runtime_n1101_synthetic.csv"   # ISO-8601 submission_time, runtime_ms
SESSIONS_CSV    = "../submission-model/interactive-dev-sessions/sessions.csv"      # ISO-8601 timestamp, length_seconds

MAX_PODS        = 200      # Sweep upper bound

# ── Drop-rate target (batch only; sessions never drop) ────────────────────────
BATCH_DROP_TARGET = 0.05   # ≤ 5 % of batch jobs may time out

# ── Batch timeout ─────────────────────────────────────────────────────────────
BATCH_TIMEOUT_MS  = 300000    # Batch job dropped after waiting this long in queue (ms)

# ── Interactive session cap ───────────────────────────────────────────────────
MAX_SESSION_HOURS = 6      # A session running longer than this is evicted at the cap;
                           # the remaining time re-enters the session queue immediately.

# ── Session wait target ───────────────────────────────────────────────────────
SESSION_WAIT_TARGET_MS  = 60_000 * 10  # p95 session wait must be ≤ this (ms); set to float('inf') to disable
SESSION_WAIT_PERCENTILE = 95       # percentile to check (95 = "95% of sessions wait less than target")

# ── Overhead (ms) ─────────────────────────────────────────────────────────────
GRADESCOPE_OVERHEAD_MS = 24373.76 # Gradescope Duration + Junkyard Duration + Waiting for Scheduler
POD_CREATION_OVERHEAD  =    2603.26 # This is the delay from when a pod is assigned until it's actually ready to run.
OVERHEAD_JITTER_MS     =      5        # ± jitter on top of combined overhead

SPEED_MULTIPLIER = float("inf")  # float('inf') = run instantly; 10 = 10× real-time
VERBOSE          = False          # True = print every event
RANDOM_SEED      = 42

# ─────────────────────────────────────────────
# DERIVED CONSTANT
# ─────────────────────────────────────────────

_SESSION_CAP_MS = MAX_SESSION_HOURS * 3_600_000   # converted once at import time


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass(order=True)
class Event:
    """Simulation event ordered by sim-time."""
    time:      float
    kind:      str           = field(compare=False)  # 'arrival' | 'completion'
    item_id:   int           = field(compare=False)
    item_type: str           = field(compare=False, default="batch")  # 'batch'|'session'
    duration:  float         = field(compare=False, default=0.0)
    pod_id:    Optional[int] = field(compare=False, default=None)


@dataclass
class BatchJob:
    job_id:          int
    student_id:      str
    submission_time: float          # Wall-clock epoch ms when student submitted
    arrival_time:    float          # = submission_time + overhead
    duration:        float          # ms
    overhead:        float = 0.0
    start_time:      Optional[float] = None
    finish_time:     Optional[float] = None
    timed_out:       bool = False
    pod_id:          Optional[int]  = None

    item_type: str = field(default="batch", init=False, repr=False)

    @property
    def wait_time(self) -> Optional[float]:
        if self.start_time is None:
            return None
        return self.start_time - self.arrival_time

    @property
    def total_latency(self) -> Optional[float]:
        if self.finish_time is None:
            return None
        return self.finish_time - self.submission_time


@dataclass
class Session:
    session_id:   int
    student_id:   str
    arrival_time: float          # When this slice entered the queue (ms, re-zeroed)
    duration:     float          # Remaining time for this slice (ms)
    # ── slice tracking ────────────────────────────────────────────────────────
    slice_index:  int   = 0      # 0 = original, 1+ = re-queued continuation slices
    original_id:  int   = -1     # session_id of the original Session (same for slices)
    # ── timing ────────────────────────────────────────────────────────────────
    actual_start: Optional[float] = None
    finish_time:  Optional[float] = None
    pod_id:       Optional[int]   = None

    item_type: str = field(default="session", init=False, repr=False)

    def __post_init__(self):
        if self.original_id == -1:
            self.original_id = self.session_id

    @property
    def wait_time(self) -> Optional[float]:
        if self.actual_start is None:
            return None
        return self.actual_start - self.arrival_time


# ─────────────────────────────────────────────
# CSV LOADING
# ─────────────────────────────────────────────

def _parse_iso(s: str) -> float:
    """Parse an ISO-8601 datetime string → epoch milliseconds."""
    s = s.strip()
    # Normalise ±HH:MM timezone offset to ±HHMM for strptime compatibility
    s = re.sub(r'([+-]\d{2}):(\d{2})$', r'\1\2', s)
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    return dt.timestamp() * 1000.0   # → ms

def parse_time(s: str) -> float:
    s = s.strip()
    if ":" not in s:
        return float(s) * 3_600_000   # hours → ms

    parts = s.split(":")
    if len(parts) == 2:
        h, m = parts
        total_sec = int(h) * 3600 + int(m) * 60
    elif len(parts) == 3:
        h, m, sec = parts
        total_sec = int(h) * 3600 + int(m) * 60 + float(sec)
    else:
        raise ValueError(f"Unrecognised time format: {s!r}")
    return total_sec * 1000   # ← was missing entirely


def load_submissions(path: str) -> list[BatchJob]:
    """
    Load batch jobs from the submissions CSV.
    Rows with missing or negative runtime_ms are skipped.
    """
    jobs = []
    skipped = 0
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fields = {k.strip().lower(): k for k in (reader.fieldnames or [])}

        for i, row in enumerate(reader):
            raw_rt = row.get(fields.get("runtime_ms", ""), "").strip()
            if not raw_rt:
                skipped += 1
                continue
            try:
                rt = float(raw_rt)
            except ValueError:
                skipped += 1
                continue
            if rt < 0:
                skipped += 1
                continue

            sub_ms = parse_time(row[fields["submission_time"]])
            sid    = row.get(fields.get("student_id", ""), "?").strip()

            jobs.append(BatchJob(
                job_id          = i,
                student_id      = sid,
                submission_time = sub_ms,
                arrival_time    = sub_ms,
                duration        = rt,
            ))

    if not jobs:
        raise ValueError("No valid jobs found in submissions CSV.")

    # Re-zeroing is handled independently by _rezero_jobs() in __main__.

    if skipped:
        print(f"  Skipped {skipped} submission row(s) with missing/negative runtime_ms.")
    return jobs


def load_sessions(path: str) -> list[Session]:
    """
    Load interactive sessions from the sessions CSV.
    Columns: timestamp (ISO-8601), length_seconds, student_id
    """
    sessions = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fields = {k.strip().lower(): k for k in (reader.fieldnames or [])}

        for i, row in enumerate(reader):
            ts_ms    = _parse_iso(row[fields["timestamp"]])
            length_s = float(row[fields["length_seconds"]])
            sid      = row.get(fields.get("student_id", ""), "?").strip()

            sessions.append(Session(
                session_id   = i,
                student_id   = sid,
                arrival_time = ts_ms,
                duration     = length_s * 1000.0,
            ))

    if not sessions:
        raise ValueError("No sessions found in sessions CSV.")
    return sessions


def _rezero_jobs(jobs: list[BatchJob]):
    """Re-zero batch jobs so the first submission is at t=0."""
    t0 = min(j.submission_time for j in jobs)
    for j in jobs:
        j.submission_time -= t0
        j.arrival_time    -= t0


def _rezero_sessions(sessions: list[Session]):
    """Re-zero sessions independently so the first session starts at t=0."""
    t0 = min(s.arrival_time for s in sessions)
    for s in sessions:
        s.arrival_time -= t0


def apply_batch_overhead(jobs: list[BatchJob], overhead_ms: float,
                          jitter_ms: float, seed) -> list[BatchJob]:
    rng = random.Random(seed)
    for job in jobs:
        jitter           = rng.uniform(-jitter_ms, jitter_ms) if jitter_ms > 0 else 0.0
        actual           = max(0.0, overhead_ms + jitter)
        job.overhead     = actual
        job.arrival_time = job.submission_time + actual
    return jobs


def print_data_diagnostics(jobs: list[BatchJob], sessions: list[Session]):
    """
    Print inter-arrival and duration distributions so you can choose
    sensible values for BATCH_TIMEOUT_MS and MAX_SESSION_HOURS.
    """
    import statistics

    durations   = sorted(j.duration for j in jobs)
    arrivals    = sorted(j.arrival_time for j in jobs)
    inter_arr   = [b - a for a, b in zip(arrivals, arrivals[1:])]
    ses_dur_h   = sorted(s.duration / 3_600_000 for s in sessions)

    def pct(data, p):
        if not data:
            return 0.0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    print(f"\n{'─'*60}")
    print(f"  DATA DIAGNOSTICS")
    print(f"{'─'*60}")
    print(f"  Batch jobs  ({len(jobs)} total)")
    print(f"    Duration  min={durations[0]:.1f}ms  "
          f"p50={pct(durations,50):.1f}ms  "
          f"p95={pct(durations,95):.1f}ms  "
          f"max={durations[-1]:.1f}ms")
    if inter_arr:
        inter_arr_s = sorted(inter_arr)
        print(f"    Inter-arrival  "
              f"min={inter_arr_s[0]:.0f}ms  "
              f"p50={pct(inter_arr_s,50):.0f}ms  "
              f"p95={pct(inter_arr_s,95):.0f}ms  "
              f"max={inter_arr_s[-1]:.0f}ms")
    print(f"    Suggested BATCH_TIMEOUT_MS: "
          f"try p95 inter-arrival ≈ {pct(sorted(inter_arr),95):.0f}ms "
          f"or a fixed SLA (e.g. 30000 = 30s)")
    print(f"\n  Sessions  ({len(sessions)} total)")
    print(f"    Duration  min={ses_dur_h[0]:.2f}h  "
          f"p50={pct(ses_dur_h,50):.2f}h  "
          f"p95={pct(ses_dur_h,95):.2f}h  "
          f"max={ses_dur_h[-1]:.2f}h")
    print(f"    Current MAX_SESSION_HOURS = {MAX_SESSION_HOURS}h")
    print(f"{'─'*60}\n")


# ─────────────────────────────────────────────
# SIMULATOR
# ─────────────────────────────────────────────

def run_simulation(
    base_jobs:     list[BatchJob],
    base_sessions: list[Session],
    num_pods:      int,
    batch_timeout: float,
    session_cap:   float,
    speed:         float,
    verbose:       bool,
) -> tuple[list[BatchJob], list[Session]]:
    """
    Discrete-event simulation.

    Priority (high → low):
      1. Interactive sessions  — never dropped; wait indefinitely
      2. Batch autograder jobs — dropped after batch_timeout ms in queue

    Session cap (session_cap ms):
      When a session is dispatched, it runs for min(remaining_duration, cap).
      If the session is cut short, a new Session slice is created and pushed
      to the FRONT of the session queue at the moment the pod is freed, so it
      immediately competes for the next available pod.

    Returns deep copies of jobs and all session slices (including continuations).
    """
    jobs     = copy.deepcopy(base_jobs)
    sessions = copy.deepcopy(base_sessions)

    event_queue:   list[Event]    = []
    pod_idle = [True] * num_pods

    session_queue: list[Session]  = []   # high-priority FIFO (prepend for continuations)
    batch_queue:   list[BatchJob] = []   # low-priority FIFO

    job_map     = {j.job_id:     j for j in jobs}
    session_map = {s.session_id: s for s in sessions}

    # Track all session slices created during the run (for stats)
    all_slices: list[Session] = list(sessions)
    next_slice_id = len(sessions)   # IDs for continuation slices

    for j in jobs:
        heapq.heappush(event_queue, Event(
            time=j.arrival_time, kind="arrival",
            item_id=j.job_id, item_type="batch", duration=j.duration,
        ))
    for s in sessions:
        heapq.heappush(event_queue, Event(
            time=s.arrival_time, kind="arrival",
            item_id=s.session_id, item_type="session", duration=s.duration,
        ))

    sim_start_wall = time.time()

    def wall_sleep(until: float):
        if speed == float("inf"):
            return
        delta = (sim_start_wall + until / speed) - time.time()
        if delta > 0:
            time.sleep(delta)

    def try_dispatch(current_time: float):
        # ── Expire timed-out batch jobs (sessions never time out) ──────────
        i = 0
        while i < len(batch_queue):
            j = batch_queue[i]
            if current_time - j.arrival_time > batch_timeout:
                batch_queue.pop(i)
                j.timed_out = True
                if verbose:
                    print(f"  [t={current_time:14.1f}ms] ✗ Job     {j.job_id:3d} "
                          f"(student {j.student_id}) TIMED OUT "
                          f"(waited {current_time - j.arrival_time:.1f}ms)")
            else:
                i += 1

        # ── Assign idle pods: sessions first, then batch ───────────────────
        for queue, label in ((session_queue, "session"), (batch_queue, "batch")):
            while queue:
                idle_pod = next((i for i, idle in enumerate(pod_idle) if idle), None)
                if idle_pod is None:
                    return   # No pod available for anyone right now

                item = queue.pop(0)
                pod_idle[idle_pod] = False

                if label == "session":
                    # Cap how long this slice runs
                    run_for = min(item.duration, session_cap)
                    finish  = current_time + run_for

                    item.actual_start = current_time
                    item.finish_time  = finish
                    item.pod_id       = idle_pod

                    if verbose:
                        capped = " [CAPPED]" if run_for < item.duration else ""
                        print(f"  [t={current_time:14.1f}ms] → Session {item.session_id:3d}"
                              f"(orig {item.original_id}, slice {item.slice_index}) "
                              f"student {item.student_id} on Pod {idle_pod} "
                              f"(wait {current_time - item.arrival_time:.1f}ms, "
                              f"run {run_for/3_600_000:.2f}h){capped}")

                    heapq.heappush(event_queue, Event(
                        time=finish, kind="completion",
                        item_id=item.session_id, item_type="session",
                        pod_id=idle_pod,
                    ))

                    # If capped, store remaining duration so the completion handler
                    # can create and re-queue a continuation slice.
                    item._remaining = item.duration - run_for   # type: ignore[attr-defined]

                else:
                    item.start_time  = current_time
                    item.finish_time = current_time + item.duration
                    item.pod_id      = idle_pod

                    if verbose:
                        print(f"  [t={current_time:14.1f}ms] → Job     {item.job_id:3d} "
                              f"student {item.student_id} on Pod {idle_pod} "
                              f"(wait {current_time - item.arrival_time:.1f}ms, "
                              f"dur {item.duration:.1f}ms)")

                    heapq.heappush(event_queue, Event(
                        time=item.finish_time, kind="completion",
                        item_id=item.job_id, item_type="batch",
                        pod_id=idle_pod,
                    ))

    if verbose:
        print(f"\n{'='*74}")
        print(f"  pods={num_pods}  batch_timeout={batch_timeout}ms  "
              f"session_cap={session_cap/3_600_000:.1f}h  speed={speed}x")
        print(f"{'='*74}\n")

    while event_queue:
        event    = heapq.heappop(event_queue)
        sim_time = event.time
        wall_sleep(sim_time)

        if event.kind == "arrival":
            if event.item_type == "session":
                s = session_map[event.item_id]
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ↓ Session {s.session_id:3d} "
                          f"student {s.student_id} arrived "
                          f"(dur {s.duration/3_600_000:.2f}h)")
                session_queue.append(s)
            else:
                j = job_map[event.item_id]
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ↓ Job     {j.job_id:3d} "
                          f"student {j.student_id} arrived "
                          f"(dur {j.duration:.1f}ms)")
                batch_queue.append(j)
            try_dispatch(sim_time)

        elif event.kind == "completion":
            pod_id           = event.pod_id
            pod_idle[pod_id] = True

            if event.item_type == "session":
                # Retrieve the Session object that just finished its slice
                finished_slice = session_map[event.item_id]
                remaining      = getattr(finished_slice, "_remaining", 0.0)

                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ✓ Session {finished_slice.session_id:3d} "
                          f"(orig {finished_slice.original_id}) finished slice on Pod {pod_id}"
                          + (f" → {remaining/3_600_000:.2f}h re-queued" if remaining > 0 else ""))

                if remaining > 0:
                    # Create a continuation slice and push to FRONT of session queue
                    # so it wins the next pod as soon as one is free.
                    nonlocal_id = next_slice_id
                    next_slice_id += 1   # closure mutation — works in Python 3

                    continuation = Session(
                        session_id   = nonlocal_id,
                        student_id   = finished_slice.student_id,
                        arrival_time = sim_time,          # re-queued right now
                        duration     = remaining,
                        slice_index  = finished_slice.slice_index + 1,
                        original_id  = finished_slice.original_id,
                    )
                    session_map[nonlocal_id] = continuation
                    all_slices.append(continuation)
                    session_queue.insert(0, continuation)   # HIGH priority: front of queue

            else:
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ✓ Job     {event.item_id:3d} "
                          f"finished on Pod {pod_id}")

            try_dispatch(sim_time)

    # Anything still queued at simulation end is recorded (not timed_out for sessions)
    for leftover in batch_queue:
        leftover.timed_out = True

    return jobs, all_slices


# ─────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────

def compute_batch_stats(jobs: list[BatchJob]) -> dict:
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


def compute_session_stats(slices: list[Session]) -> dict:
    """
    Stats are computed over original sessions (slice_index == 0), not individual slices.
    A session is 'completed' if its final slice has a finish_time.
    """
    # Group slices by original_id
    from collections import defaultdict
    groups: dict[int, list[Session]] = defaultdict(list)
    for s in slices:
        groups[s.original_id].append(s)

    total_sessions  = len(groups)
    completed_count = 0
    wait_times      = []
    total_slices    = len(slices)
    capped_slices   = sum(1 for s in slices if s.slice_index > 0)

    for orig_id, slist in groups.items():
        slist.sort(key=lambda x: x.slice_index)
        first = slist[0]
        last  = slist[-1]

        if last.finish_time is not None:
            completed_count += 1

        # Wait time = time first slice spent in queue before getting a pod
        if first.wait_time is not None:
            wait_times.append(first.wait_time)

    # compute p95 (or whatever percentile) of initial wait times
    wait_times_sorted = sorted(wait_times)
    def _pct(data, p):
        if not data: return 0.0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]
        

    return {
        "total":          total_sessions,
        "completed":      completed_count,
        "drop_rate":      0.0,          # sessions never drop
        "avg_wait":       sum(wait_times) / len(wait_times) if wait_times else 0,
        "max_wait":       max(wait_times)                   if wait_times else 0,
        "total_slices":   total_slices,
        "capped_slices":  capped_slices,
        "p95_wait": _pct(wait_times_sorted, SESSION_WAIT_PERCENTILE),
    }


def meets_target(b_stats: dict, s_stats: dict) -> bool:
    session_ok = s_stats["p95_wait"]  <= SESSION_WAIT_TARGET_MS
    submission_ok = b_stats["drop_rate"] <= BATCH_DROP_TARGET
    return session_ok and submission_ok


def print_sweep_table(results: list[tuple[int, dict, dict]]):
    print(f"\n{'='*90}")
    print(f"  SWEEP RESULTS  (batch drop target ≤ {BATCH_DROP_TARGET*100:.1f}%  |  "
          f"sessions never dropped & wait target p{SESSION_WAIT_PERCENTILE} ≤ {SESSION_WAIT_TARGET_MS/1000:.1f}s)")
    print(f"{'='*90}")
    print(f"  {'Pods':>5}  "
          f"{'B-done':>7}  {'B-drop':>7}  {'B-drop%':>8}  {'B-avgW':>9}  "
          f"{'Sessions':>9}  {'S-slices':>9}  {'S-avgW':>12}  {'S-p95W':>12}    "
          f"{'Target':>8}")
    print("  " + "-"*87)
    for num_pods, b, s in sorted(results, key=lambda r: r[0]):
        ok = "✓  YES" if meets_target(b, s) else "✗  no"
        print(f"  {num_pods:>5}  "
              f"{b['completed']:>7}  {b['timed_out']:>7}  {b['drop_rate']*100:>7.1f}%  "
              f"{b['avg_wait']:>8.1f}ms  "
              f"{s['total']:>9}  {s['total_slices']:>9}  "
              f"{s['avg_wait']:>10.1f}ms  "
              f"{s['p95_wait']:>10.1f}ms  "
              f"{ok:>8}")
    print(f"{'='*90}")


def print_final_report(num_pods: int, b: dict, s: dict, overhead_ms: float):
    print(f"\n{'='*70}")
    print(f"  RECOMMENDATION")
    print(f"{'='*70}")
    print(f"  Minimum pods to meet target   : {num_pods}")
    print()
    print(f"  ── Batch jobs ──────────────────────────────────────────────")
    print(f"  Drop rate target    : ≤ {BATCH_DROP_TARGET*100:.1f}%")
    print(f"  Actual drop rate    : {b['drop_rate']*100:.2f}%")
    print(f"  Timeout threshold   : {BATCH_TIMEOUT_MS}ms")
    print(f"  Combined overhead   : {overhead_ms:.3f}ms ± {OVERHEAD_JITTER_MS}ms jitter")
    print(f"  Avg actual overhead : {b['avg_overhead']:.2f}ms")
    print(f"  Total / completed / dropped : "
          f"{b['total']} / {b['completed']} / {b['timed_out']}")
    print(f"  Avg queue wait      : {b['avg_wait']:.2f}ms")
    print(f"  Max queue wait      : {b['max_wait']:.2f}ms")
    print(f"  Avg total latency   : {b['avg_latency']:.2f}ms  (submit → done)")
    print()
    print(f"  ── Interactive sessions ────────────────────────────────────")
    print(f"  Session cap         : {MAX_SESSION_HOURS}h  "
          f"(remainder re-queued at front of session queue)")
    print(f"  Sessions never dropped (wait indefinitely for a pod)")
    print(f"  Total sessions      : {s['total']}")
    print(f"  Completed           : {s['completed']}")
    print(f"  Total slices run    : {s['total_slices']}  "
          f"({s['capped_slices']} continuation slices from cap)")
    print(f"  Avg initial wait    : {s['avg_wait']:.2f}ms")
    print(f"  Max initial wait    : {s['max_wait']:.2f}ms")
    print(f"  p{SESSION_WAIT_PERCENTILE} wait target : ≤ {SESSION_WAIT_TARGET_MS/1000:.1f}s")
    print(f"  Actual p{SESSION_WAIT_PERCENTILE} wait  : {s['p95_wait']/1000:.2f}s")
    print(f"{'='*70}\n")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    sub_path = sys.argv[1] if len(sys.argv) > 1 else SUBMISSIONS_CSV
    ses_path = sys.argv[2] if len(sys.argv) > 2 else SESSIONS_CSV

    print(f"Loading submissions from : {sub_path}")
    base_jobs = load_submissions(sub_path)
    print(f"  → {len(base_jobs)} valid batch jobs loaded.")

    print(f"Loading sessions from    : {ses_path}")
    base_sessions = load_sessions(ses_path)
    print(f"  → {len(base_sessions)} sessions loaded.")

    _rezero_jobs(base_jobs)
    _rezero_sessions(base_sessions)

    total_overhead = GRADESCOPE_OVERHEAD_MS + POD_CREATION_OVERHEAD
    base_jobs = apply_batch_overhead(
        base_jobs, total_overhead, OVERHEAD_JITTER_MS, seed=RANDOM_SEED
    )
    print(f"\nBatch overhead applied   : {total_overhead:.3f}ms "
          f"({GRADESCOPE_OVERHEAD_MS}ms Gradescope + {POD_CREATION_OVERHEAD}ms pod spin-up) "
          f"± {OVERHEAD_JITTER_MS}ms jitter")
    print(f"Session cap              : {MAX_SESSION_HOURS}h  "
          f"(set MAX_SESSION_HOURS to change)")
    print(f"Sessions never time out  : they wait until a pod is free.\n")

    print_data_diagnostics(base_jobs, base_sessions)

    print(f"Binary searching 1 → {MAX_PODS} pods")
    print(f"  Target: batch drop ≤ {BATCH_DROP_TARGET*100:.1f}%\n")

    sweep_results:  list[tuple[int, dict, dict]] = []
    recommendation = None
    session_cap_ms = MAX_SESSION_HOURS * 3_600_000

    lo, hi = 1, MAX_PODS
    while lo < hi:
        mid = (lo + hi) // 2
        print(f"  [lo={lo:3d} hi={hi:3d}] Simulating {mid:3d} pods...", end="", flush=True)
        result_jobs, result_slices = run_simulation(
            base_jobs, base_sessions,
            num_pods=mid,
            batch_timeout=BATCH_TIMEOUT_MS,
            session_cap=session_cap_ms,
            speed=SPEED_MULTIPLIER,
            verbose=VERBOSE,
        )
        b = compute_batch_stats(result_jobs)
        s = compute_session_stats(result_slices)
        sweep_results.append((mid, b, s))
        print(f"  batch={b['drop_rate']*100:.1f}% drop  "
                f"s_p95wait={s['p95_wait']/1000:.1f}s  "
                f"({'✓' if meets_target(b, s) else '✗'})")

        if meets_target(b, s):
            hi = mid
        else:
            lo = mid + 1

    print(f"\n  [final] Simulating {lo:3d} pods...", end="", flush=True)
    result_jobs, result_slices = run_simulation(
        base_jobs, base_sessions,
        num_pods=lo,
        batch_timeout=BATCH_TIMEOUT_MS,
        session_cap=session_cap_ms,
        speed=SPEED_MULTIPLIER,
        verbose=VERBOSE,
    )
    b = compute_batch_stats(result_jobs)
    s = compute_session_stats(result_slices)
    sweep_results.append((lo, b, s))
    print(f"  batch={b['drop_rate']*100:.1f}% drop  session_slices={s['total_slices']}")

    if meets_target(b, s):
        recommendation = (lo, b, s)
        print(f"\n  ★  Minimum pods to meet target: {lo}\n")
    else:
        print(f"\n  ✗  Could not meet target within {MAX_PODS} pods.\n")

    print_sweep_table(sweep_results)

    if recommendation:
        print_final_report(
            num_pods    = recommendation[0],
            b           = recommendation[1],
            s           = recommendation[2],
            overhead_ms = total_overhead,
        )
    else:
        print(f"\n  ⚠  Batch drop target NOT met within {MAX_PODS} pods.")
        best_pods, best_b, best_s = min(sweep_results, key=lambda r: r[1]["drop_rate"])
        print(f"     Best result: {best_b['drop_rate']*100:.2f}% drop rate "
              f"at {best_pods} pods.")
        print(f"     Try: raise MAX_PODS, increase BATCH_TIMEOUT_MS, "
              f"or relax BATCH_DROP_TARGET.\n")
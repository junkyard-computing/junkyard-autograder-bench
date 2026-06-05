"""
Estimates the number of pods (compute units) needed to handle both:
  • Interactive development sessions  (HIGH priority)
  • Batch autograder job submissions   (LOW priority)

Sessions always jump ahead of batch jobs in the queue. Sessions never
time out — they wait until a pod is free. If a session's total duration
exceeds MAX_SESSION_HOURS, it runs for exactly that cap, then the
remaining time is re-queued back into the session queue (high priority).

Batch jobs time out after BATCH_TIMEOUT_MS if no pod becomes available.

Sweeps 1 → MAX_PODS using binary search and reports the minimum pod
count that keeps BOTH targets:
  • batch drop rate       ≤ BATCH_DROP_TARGET
  • session p95 wait time ≤ SESSION_WAIT_TARGET_MS

─── Input CSVs ───────────────────────────────────────────────────────
Submissions CSV columns (required):
  submission_time  : hours_since_first (float hours from first submission)
  runtime_ms       : job duration in ms (blank or -1 → row is skipped)
  student_id       : optional

Sessions CSV columns (required):
  timestamp        : ISO-8601 datetime  — when the session starts
  length_seconds   : session duration in seconds
  student_id       : optional

─── Cluster config JSON ──────────────────────────────────────────────
Optional JSON file at CLUSTER_CONFIG_JSON with named cluster entries.
Each entry must contain:
  gradescope_overhead_ms  : float
  pod_creation_overhead   : float

Example:
  {
      "cluster1": {
          "gradescope_overhead_ms": 24373.76,
          "pod_creation_overhead":  2603.26
      },
      "prod": {
          "gradescope_overhead_ms": 18000.0,
          "pod_creation_overhead":  1500.0
      }
  }

Usage:
  python pod_simulator_interactive.py <submissions_csv> <sessions_csv> [cluster_name]

  cluster_name — key in CLUSTER_CONFIG_JSON to use for overhead values.
                 If omitted, the hardcoded GRADESCOPE_OVERHEAD_MS /
                 POD_CREATION_OVERHEAD constants below are used instead.
"""

import csv
import copy
import heapq
import json
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# ─────────────────────────────────────────────
# PARAMETERS  ← edit these
# ─────────────────────────────────────────────

SUBMISSIONS_CSV      = "pa8_hours_pa8_runtime_n1101_synthetic.csv"
SESSIONS_CSV         = "../submission-model/interactive-dev-sessions/sessions.csv"
CLUSTER_CONFIG_JSON  = "cluster_metrics.json"   # Path to overhead config; see module docstring.

SESSION_START    = "2026-03-09T15:26:49.338103-07:00"
SUBMISSION_START = "2026-03-09T18:21:14.338103-07:00"

MAX_PODS = 200   # Sweep upper bound

# ── Batch jobs ────────────────────────────────────────────────────────────────
BATCH_DROP_TARGET = 0.05     # ≤ 5% of batch jobs may time out
BATCH_TIMEOUT_MS  = 300000   # Job dropped if it waits longer than this in queue (ms)

# ── Interactive sessions ──────────────────────────────────────────────────────
MAX_SESSION_HOURS       = 6        # Session evicted after this; remainder re-queued
SESSION_WAIT_TARGET_MS  = 100000   # p95 session queue wait must be ≤ this (ms)
SESSION_WAIT_PERCENTILE = 99       # percentile used for the wait target

# ── Default overhead (ms) — used when no cluster name is passed via CLI ───────
# Strawhat defaults:
GRADESCOPE_OVERHEAD          =   350.7783672  # Gradescope submission delay
CLUSTER_DURATION_OVERHEAD    =  2286.2676     # Cluster processing delay
SCHEDULER_WAIT_OVERHEAD      = 14973.46385    # Scheduler queue wait delay
POD_CREATION_OVERHEAD        =  2603.26       # Pod spin-up delay after assignment
OVERHEAD_JITTER_MS           =     5          # ± random jitter on combined overhead

SPEED_MULTIPLIER = float("inf")  # float('inf') = instant; e.g. 10 = 10× real-time
VERBOSE          = False
RANDOM_SEED      = 42


# ─────────────────────────────────────────────
# CLUSTER CONFIG LOADER
# ─────────────────────────────────────────────

def load_cluster_overhead(json_path: str, cluster_name: str) -> tuple[float, float, float, float]:
    """
    Load overhead values for a named cluster from a JSON config file.

    Returns (gradescope_overhead, cluster_duration_overhead, scheduler_wait_overhead,
             pod_creation_overhead).

    Raises FileNotFoundError if the JSON path doesn't exist.
    Raises KeyError if cluster_name is not found in the file.
    Raises ValueError if required fields are missing or non-numeric.
    """
    with open(json_path) as f:
        config = json.load(f)

    if cluster_name not in config:
        available = ", ".join(f"'{k}'" for k in config)
        raise KeyError(
            f"Cluster '{cluster_name}' not found in '{json_path}'. "
            f"Available: {available}"
        )

    entry = config[cluster_name]
    required = ("gradescope_overhead", "cluster_duration_overhead", "scheduler_wait_overhead",
                "pod_creation_overhead")
    for field_name in required:
        if field_name not in entry:
            raise ValueError(
                f"Cluster '{cluster_name}' in '{json_path}' "
                f"is missing required field '{field_name}'."
            )
        if not isinstance(entry[field_name], (int, float)):
            raise ValueError(
                f"Cluster '{cluster_name}.{field_name}' must be a number, "
                f"got {type(entry[field_name]).__name__!r}."
            )

    return (
        float(entry["gradescope_overhead"]),
        float(entry["cluster_duration_overhead"]),
        float(entry["scheduler_wait_overhead"]),
        float(entry["pod_creation_overhead"]),
    )


# ─────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────

@dataclass(order=True)
class Event:
    time:      float
    kind:      str           = field(compare=False)   # 'arrival' | 'completion'
    item_id:   int           = field(compare=False)
    item_type: str           = field(compare=False, default="batch")
    duration:  float         = field(compare=False, default=0.0)
    pod_id:    Optional[int] = field(compare=False, default=None)


@dataclass
class BatchJob:
    job_id:          int
    student_id:      str
    submission_time: float   # absolute ms on shared timeline
    arrival_time:    float   # = submission_time + overhead
    duration:        float   # ms
    overhead:        float = 0.0
    start_time:      Optional[float] = None
    finish_time:     Optional[float] = None
    timed_out:       bool = False
    pod_id:          Optional[int] = None
    item_type:       str = field(default="batch", init=False, repr=False)

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
    arrival_time: float   # absolute ms on shared timeline
    duration:     float   # ms (remaining for this slice)
    slice_index:  int   = 0
    original_id:  int   = -1
    actual_start: Optional[float] = None
    finish_time:  Optional[float] = None
    pod_id:       Optional[int]   = None
    item_type:    str = field(default="session", init=False, repr=False)

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
    """ISO-8601 datetime string → epoch milliseconds."""
    s = s.strip()
    s = re.sub(r'([+-]\d{2}):(\d{2})$', r'\1\2', s)
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z")
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    return dt.timestamp() * 1000.0


def _parse_hours(s: str) -> float:
    """
    hours_since_first string → milliseconds.
      plain float  → hours:  "1.5"       → 5_400_000 ms
      HH:MM        → hours+min
      HH:MM:SS     → hours+min+sec
    """
    s = s.strip()
    if ":" not in s:
        return float(s) * 3_600_000

    parts = s.split(":")
    if len(parts) == 2:
        h, m = parts
        total_sec = int(h) * 3600 + int(m) * 60
    elif len(parts) == 3:
        h, m, sec = parts
        total_sec = int(h) * 3600 + int(m) * 60 + float(sec)
    else:
        raise ValueError(f"Unrecognised time format: {s!r}")
    return total_sec * 1000


def load_submissions(path: str) -> list[BatchJob]:
    jobs    = []
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

            time_col = fields.get("hours_since_first") or fields.get("submission_time")
            if not time_col:
                raise ValueError("Submissions CSV needs 'hours_since_first' or 'submission_time'.")
            sub_ms = _parse_hours(row[time_col])
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
    if skipped:
        print(f"  Skipped {skipped} submission row(s) with missing/negative runtime_ms.")
    return jobs


def load_sessions(path: str) -> list[Session]:
    sessions = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fields = {k.strip().lower(): k for k in (reader.fieldnames or [])}

        for i, row in enumerate(reader):
            ts_ms    = _parse_iso(row[fields["timestamp"]])
            length_s = float(row[fields["length_seconds"]])
            sid      = row.get(fields.get("student_id", ""), f"s{i}").strip() or f"s{i}"

            sessions.append(Session(
                session_id   = i,
                student_id   = sid,
                arrival_time = ts_ms,
                duration     = length_s * 1000.0,
            ))

    if not sessions:
        raise ValueError("No sessions found in sessions CSV.")
    return sessions


def _align_timelines(jobs: list[BatchJob], sessions: list[Session]):
    session_t0 = min(s.arrival_time for s in sessions)
    for s in sessions:
        s.arrival_time -= session_t0

    FIRST_SUBMISSION_EPOCH_MS = _parse_iso(SUBMISSION_START)
    FIRST_SESSION_EPOCH_MS    = _parse_iso(SESSION_START)
    JOB_START_OFFSET_MS       = FIRST_SUBMISSION_EPOCH_MS - FIRST_SESSION_EPOCH_MS

    for j in jobs:
        j.submission_time = j.submission_time + JOB_START_OFFSET_MS
        j.arrival_time    = j.submission_time


def apply_batch_overhead(jobs: list[BatchJob], overhead_ms: float,
                         jitter_ms: float, seed) -> list[BatchJob]:
    rng = random.Random(seed)
    for job in jobs:
        jitter           = rng.uniform(-jitter_ms, jitter_ms) if jitter_ms > 0 else 0.0
        actual           = max(0.0, overhead_ms + jitter)
        job.overhead     = actual
        job.arrival_time = job.submission_time + actual
    return jobs


# ─────────────────────────────────────────────
# DIAGNOSTICS
# ─────────────────────────────────────────────

def print_data_diagnostics(jobs: list[BatchJob], sessions: list[Session]):
    durations = sorted(j.duration     for j in jobs)
    arrivals  = sorted(j.arrival_time for j in jobs)
    inter_arr = sorted(b - a for a, b in zip(arrivals, arrivals[1:]))
    ses_dur_h = sorted(s.duration / 3_600_000 for s in sessions)

    def pct(data, p):
        if not data: return 0.0
        return data[min(int(len(data) * p / 100), len(data) - 1)]

    print(f"\n{'─'*64}")
    print(f"  DATA DIAGNOSTICS")
    print(f"{'─'*64}")
    print(f"  Shared timeline: sessions t=0 is first session arrival;")
    print(f"    jobs are placed relative to that same origin.")
    print(f"    First job arrival : {min(j.arrival_time for j in jobs)/3_600_000:.3f}h")
    print(f"    Last  job arrival : {max(j.arrival_time for j in jobs)/3_600_000:.3f}h")
    print(f"    First session     : {min(s.arrival_time for s in sessions)/3_600_000:.3f}h")
    print(f"    Last  session     : {max(s.arrival_time for s in sessions)/3_600_000:.3f}h")
    print(f"\n  Batch jobs  ({len(jobs)} total)")
    print(f"    Duration   min={durations[0]:.1f}ms  p50={pct(durations,50):.1f}ms  "
          f"p95={pct(durations,95):.1f}ms  max={durations[-1]:.1f}ms")
    if inter_arr:
        print(f"    Inter-arr  min={inter_arr[0]:.0f}ms  p50={pct(inter_arr,50):.0f}ms  "
              f"p95={pct(inter_arr,95):.0f}ms  max={inter_arr[-1]:.0f}ms")
        print(f"    Suggested BATCH_TIMEOUT_MS ≈ p95 inter-arrival = "
              f"{pct(inter_arr,95):.0f}ms")
    print(f"\n  Sessions  ({len(sessions)} total)")
    print(f"    Duration   min={ses_dur_h[0]:.2f}h  p50={pct(ses_dur_h,50):.2f}h  "
          f"p95={pct(ses_dur_h,95):.2f}h  max={ses_dur_h[-1]:.2f}h")
    print(f"    MAX_SESSION_HOURS = {MAX_SESSION_HOURS}h")
    print(f"{'─'*64}\n")


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
    jobs     = copy.deepcopy(base_jobs)
    sessions = copy.deepcopy(base_sessions)

    event_queue:   list[Event]   = []
    pod_idle = [True] * num_pods

    session_queue: list[Session]  = []
    batch_queue:   list[BatchJob] = []

    job_map     = {j.job_id:     j for j in jobs}
    session_map = {s.session_id: s for s in sessions}

    all_slices:   list[Session] = list(sessions)
    next_slice_id = len(sessions)

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
        i = 0
        while i < len(batch_queue):
            j = batch_queue[i]
            if current_time - j.arrival_time > batch_timeout:
                batch_queue.pop(i)
                j.timed_out = True
                if verbose:
                    print(f"  [t={current_time:14.1f}ms] ✗ Job {j.job_id:3d} "
                          f"student {j.student_id} TIMED OUT "
                          f"(waited {current_time - j.arrival_time:.1f}ms)")
            else:
                i += 1

        for queue, label in ((session_queue, "session"), (batch_queue, "batch")):
            while queue:
                idle_pod = next((i for i, idle in enumerate(pod_idle) if idle), None)
                if idle_pod is None:
                    return

                item = queue.pop(0)
                pod_idle[idle_pod] = False

                if label == "session":
                    run_for = min(item.duration, session_cap)
                    finish  = current_time + run_for
                    item.actual_start = current_time
                    item.finish_time  = finish
                    item.pod_id       = idle_pod
                    item._remaining   = item.duration - run_for  # type: ignore[attr-defined]

                    if verbose:
                        capped = " [CAPPED]" if run_for < item.duration else ""
                        print(f"  [t={current_time:14.1f}ms] → Session {item.session_id:3d} "
                              f"(orig {item.original_id} slice {item.slice_index}) "
                              f"student {item.student_id} pod {idle_pod} "
                              f"wait={current_time - item.arrival_time:.1f}ms "
                              f"run={run_for/3_600_000:.2f}h{capped}")

                    heapq.heappush(event_queue, Event(
                        time=finish, kind="completion",
                        item_id=item.session_id, item_type="session", pod_id=idle_pod,
                    ))
                else:
                    item.start_time  = current_time
                    item.finish_time = current_time + item.duration
                    item.pod_id      = idle_pod

                    if verbose:
                        print(f"  [t={current_time:14.1f}ms] → Job {item.job_id:3d} "
                              f"student {item.student_id} pod {idle_pod} "
                              f"wait={current_time - item.arrival_time:.1f}ms "
                              f"dur={item.duration:.1f}ms")

                    heapq.heappush(event_queue, Event(
                        time=item.finish_time, kind="completion",
                        item_id=item.job_id, item_type="batch", pod_id=idle_pod,
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
                session_queue.append(s)
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ↓ Session {s.session_id:3d} "
                          f"student {s.student_id} arrived dur={s.duration/3_600_000:.2f}h")
            else:
                j = job_map[event.item_id]
                batch_queue.append(j)
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ↓ Job {j.job_id:3d} "
                          f"student {j.student_id} arrived dur={j.duration:.1f}ms")
            try_dispatch(sim_time)

        elif event.kind == "completion":
            pod_id           = event.pod_id
            pod_idle[pod_id] = True

            if event.item_type == "session":
                finished_slice = session_map[event.item_id]
                remaining      = getattr(finished_slice, "_remaining", 0.0)
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ✓ Session {finished_slice.session_id:3d} "
                          f"(orig {finished_slice.original_id}) done pod {pod_id}"
                          + (f" → {remaining/3_600_000:.2f}h re-queued" if remaining > 0 else ""))

                if remaining > 0:
                    nonlocal_id   = next_slice_id
                    next_slice_id += 1
                    continuation  = Session(
                        session_id   = nonlocal_id,
                        student_id   = finished_slice.student_id,
                        arrival_time = sim_time,
                        duration     = remaining,
                        slice_index  = finished_slice.slice_index + 1,
                        original_id  = finished_slice.original_id,
                    )
                    session_map[nonlocal_id] = continuation
                    all_slices.append(continuation)
                    session_queue.insert(0, continuation)
            else:
                if verbose:
                    print(f"  [t={sim_time:14.1f}ms] ✓ Job {event.item_id:3d} done pod {pod_id}")

            try_dispatch(sim_time)

    for leftover in batch_queue:
        leftover.timed_out = True

    return jobs, all_slices


# ─────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────

def _pct(data: list, p: float) -> float:
    if not data: return 0.0
    return data[min(int(len(data) * p / 100), len(data) - 1)]


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
    groups: dict[int, list[Session]] = defaultdict(list)
    for s in slices:
        groups[s.original_id].append(s)

    completed_count = 0
    wait_times      = []
    total_slices    = len(slices)
    capped_slices   = sum(1 for s in slices if s.slice_index > 0)

    for slist in groups.values():
        slist.sort(key=lambda x: x.slice_index)
        first = slist[0]
        last  = slist[-1]
        if last.finish_time is not None:
            completed_count += 1
        if first.wait_time is not None:
            wait_times.append(first.wait_time)

    wait_times_sorted = sorted(wait_times)
    return {
        "total":         len(groups),
        "completed":     completed_count,
        "avg_wait":      sum(wait_times) / len(wait_times) if wait_times else 0,
        "max_wait":      max(wait_times)                   if wait_times else 0,
        "p_wait":        _pct(wait_times_sorted, SESSION_WAIT_PERCENTILE),
        "total_slices":  total_slices,
        "capped_slices": capped_slices,
        "p50_wait": _pct(wait_times_sorted, 50),
        "p75_wait": _pct(wait_times_sorted, 75),
        "p90_wait": _pct(wait_times_sorted, 90),
        "p95_wait": _pct(wait_times_sorted, 95),
        "p99_wait": _pct(wait_times_sorted, 99),
    }


def meets_target(b: dict, s: dict) -> bool:
    return (b["drop_rate"] <= BATCH_DROP_TARGET and
            s["p_wait"]    <= SESSION_WAIT_TARGET_MS)


def print_sweep_table(results: list[tuple[int, dict, dict]]):
    p = SESSION_WAIT_PERCENTILE
    print(f"\n{'='*100}")
    print(f"  SWEEP RESULTS  "
          f"(batch drop ≤ {BATCH_DROP_TARGET*100:.1f}%  |  "
          f"session p{p} wait ≤ {SESSION_WAIT_TARGET_MS/1000:.0f}s)")
    print(f"{'='*100}")
    print(f"  {'Pods':>5}  "
          f"{'B-done':>7}  {'B-drop':>7}  {'B-drop%':>8}  {'B-avgW':>9}  "
          f"{'S-total':>7}  {'S-slices':>8}  {'S-avgW':>12}  "
          f"{'S-p'+str(p)+'W':>12}  {'Target':>8}")
    print("  " + "-"*97)
    for num_pods, b, s in sorted(results, key=lambda r: r[0]):
        ok = "✓  YES" if meets_target(b, s) else "✗  no"
        print(f"  {num_pods:>5}  "
              f"{b['completed']:>7}  {b['timed_out']:>7}  {b['drop_rate']*100:>7.1f}%  "
              f"{b['avg_wait']:>8.1f}ms  "
              f"{s['total']:>7}  {s['total_slices']:>8}  "
              f"{s['avg_wait']:>10.1f}ms  "
              f"{s['p_wait']:>10.1f}ms  "
              f"{ok:>8}")
    print(f"{'='*100}")


def print_final_report(num_pods: int, b: dict, s: dict,
                       overhead_ms: float, gs_overhead: float, cluster_dur_overhead: float,
                       sched_wait_overhead: float, pod_overhead: float,
                       overhead_source: str):
    p = SESSION_WAIT_PERCENTILE
    print(f"\n{'='*70}")
    print(f"  RECOMMENDATION")
    print(f"{'='*70}")
    print(f"  Minimum pods to meet both targets : {num_pods}")
    print()
    print(f"  ── Batch jobs ──────────────────────────────────────────────")
    print(f"  Drop rate target    : ≤ {BATCH_DROP_TARGET*100:.1f}%")
    print(f"  Actual drop rate    : {b['drop_rate']*100:.2f}%")
    print(f"  Timeout threshold   : {BATCH_TIMEOUT_MS}ms")
    print(f"  Overhead source     : {overhead_source}")
    print(
        f"  Combined overhead   : {overhead_ms:.2f}ms "
        f"({gs_overhead}ms gs + {cluster_dur_overhead}ms cluster + "
        f"{sched_wait_overhead}ms sched + {pod_overhead}ms pod) "
        f"± {OVERHEAD_JITTER_MS}ms jitter"
    )
    print(f"  Avg actual overhead : {b['avg_overhead']:.2f}ms")
    print(f"  Total / done / drop : {b['total']} / {b['completed']} / {b['timed_out']}")
    print(f"  Avg queue wait      : {b['avg_wait']:.2f}ms")
    print(f"  Max queue wait      : {b['max_wait']:.2f}ms")
    print(f"  Avg total latency   : {b['avg_latency']:.2f}ms  (submit → done)")
    print()
    print(f"  ── Interactive sessions ────────────────────────────────────")
    print(f"  Session cap         : {MAX_SESSION_HOURS}h  (remainder re-queued)")
    print(f"  Sessions never dropped (wait indefinitely)")
    print(f"  p{p} wait target     : ≤ {SESSION_WAIT_TARGET_MS/1000:.0f}s")
    print(f"  Actual p{p} wait     : {s['p_wait']/1000:.2f}s")
    print(f"  Total / completed   : {s['total']} / {s['completed']}")
    print(f"  Total slices run    : {s['total_slices']}  "
          f"({s['capped_slices']} continuation slices)")
    print(f"  Avg initial wait    : {s['avg_wait']:.2f}ms")
    print(f"  Max initial wait    : {s['max_wait']:.2f}ms")
    print(f"  Wait percentiles    : "
          f"p50={s['p50_wait']/1000:.1f}s  "
          f"p75={s['p75_wait']/1000:.1f}s  "
          f"p90={s['p90_wait']/1000:.1f}s  "
          f"p95={s['p95_wait']/1000:.1f}s  "
          f"p99={s['p99_wait']/1000:.1f}s")
    print(f"{'='*70}\n")


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # ── Parse CLI arguments ───────────────────────────────────────────────────
    # argv[1] = submissions CSV  (optional, falls back to SUBMISSIONS_CSV)
    # argv[2] = sessions CSV     (optional, falls back to SESSIONS_CSV)
    # argv[3] = cluster name     (optional, falls back to hardcoded constants)
    sub_path     = sys.argv[1] if len(sys.argv) > 1 else SUBMISSIONS_CSV
    ses_path     = sys.argv[2] if len(sys.argv) > 2 else SESSIONS_CSV
    cluster_name = sys.argv[3].strip() if len(sys.argv) > 3 else None

    # ── Resolve overhead values ───────────────────────────────────────────────
    if cluster_name:
        print(f"Loading cluster config from : {CLUSTER_CONFIG_JSON}  (cluster: '{cluster_name}')")
        gs_overhead, cluster_dur_overhead, sched_wait_overhead, pod_overhead = load_cluster_overhead(CLUSTER_CONFIG_JSON, cluster_name)
        overhead_source = f"{CLUSTER_CONFIG_JSON} → '{cluster_name}'"
        print(f"  gradescope_overhead       : {gs_overhead}")
        print(f"  cluster_duration_overhead : {cluster_dur_overhead}")
        print(f"  scheduler_wait_overhead   : {sched_wait_overhead}")
        print(f"  pod_creation_overhead     : {pod_overhead}")
    else:
        print("No cluster name provided — using hardcoded default overhead values (strawhat).")
        gs_overhead           = GRADESCOPE_OVERHEAD
        cluster_dur_overhead  = CLUSTER_DURATION_OVERHEAD
        sched_wait_overhead   = SCHEDULER_WAIT_OVERHEAD
        pod_overhead          = POD_CREATION_OVERHEAD
        overhead_source       = "hardcoded defaults (strawhat)"

    total_overhead = gs_overhead + cluster_dur_overhead + sched_wait_overhead + pod_overhead

    # ── Load data ─────────────────────────────────────────────────────────────
    print(f"\nLoading submissions from : {sub_path}")
    base_jobs = load_submissions(sub_path)
    print(f"  → {len(base_jobs)} valid batch jobs loaded.")

    print(f"Loading sessions from    : {ses_path}")
    base_sessions = load_sessions(ses_path)
    print(f"  → {len(base_sessions)} sessions loaded.")

    # Align both timelines to a shared origin before applying overhead
    _align_timelines(base_jobs, base_sessions)

    base_jobs = apply_batch_overhead(
        base_jobs, total_overhead, OVERHEAD_JITTER_MS, seed=RANDOM_SEED
    )
    print(
        f"\nOverhead applied : {total_overhead:.2f}ms "
        f"({gs_overhead}ms gradescope + {cluster_dur_overhead}ms cluster duration + "
        f"{sched_wait_overhead}ms scheduler wait + {pod_overhead}ms pod creation) "
        f"± {OVERHEAD_JITTER_MS}ms jitter"
    )
    print(f"Session cap      : {MAX_SESSION_HOURS}h  |  "
          f"Session p{SESSION_WAIT_PERCENTILE} wait target : "
          f"≤ {SESSION_WAIT_TARGET_MS/1000:.0f}s\n")

    print_data_diagnostics(base_jobs, base_sessions)

    print(f"Binary searching 1 → {MAX_PODS} pods")
    print(f"  Targets: batch drop ≤ {BATCH_DROP_TARGET*100:.1f}%  |  "
          f"session p{SESSION_WAIT_PERCENTILE} wait ≤ "
          f"{SESSION_WAIT_TARGET_MS/1000:.0f}s\n")

    sweep_results: list[tuple[int, dict, dict]] = []
    recommendation = None
    session_cap_ms = MAX_SESSION_HOURS * 3_600_000

    lo, hi = 1, MAX_PODS
    while lo < hi:
        mid = (lo + hi) // 2
        print(f"  [lo={lo:3d} hi={hi:3d}] Simulating {mid:3d} pods...", end="", flush=True)
        result_jobs, result_slices = run_simulation(
            base_jobs, base_sessions,
            num_pods=mid, batch_timeout=BATCH_TIMEOUT_MS,
            session_cap=session_cap_ms, speed=SPEED_MULTIPLIER, verbose=VERBOSE,
        )
        b = compute_batch_stats(result_jobs)
        s = compute_session_stats(result_slices)
        sweep_results.append((mid, b, s))
        print(f"  batch={b['drop_rate']*100:.1f}% drop  "
              f"s_p{SESSION_WAIT_PERCENTILE}wait={s['p_wait']/1000:.1f}s  "
              f"({'✓' if meets_target(b, s) else '✗'})")

        if meets_target(b, s):
            hi = mid
        else:
            lo = mid + 1

    print(f"\n  [final] Simulating {lo:3d} pods...", end="", flush=True)
    result_jobs, result_slices = run_simulation(
        base_jobs, base_sessions,
        num_pods=lo, batch_timeout=BATCH_TIMEOUT_MS,
        session_cap=session_cap_ms, speed=SPEED_MULTIPLIER, verbose=VERBOSE,
    )
    b = compute_batch_stats(result_jobs)
    s = compute_session_stats(result_slices)
    sweep_results.append((lo, b, s))
    print(f"  batch={b['drop_rate']*100:.1f}% drop  "
          f"s_p{SESSION_WAIT_PERCENTILE}wait={s['p_wait']/1000:.1f}s")

    if meets_target(b, s):
        recommendation = (lo, b, s)
        print(f"\n  ★  Minimum pods to meet both targets: {lo}\n")
    else:
        print(f"\n  ✗  Could not meet both targets within {MAX_PODS} pods.\n")

    print_sweep_table(sweep_results)

    if recommendation:
        print_final_report(
            num_pods              = recommendation[0],
            b                     = recommendation[1],
            s                     = recommendation[2],
            overhead_ms           = total_overhead,
            gs_overhead           = gs_overhead,
            cluster_dur_overhead  = cluster_dur_overhead,
            sched_wait_overhead   = sched_wait_overhead,
            pod_overhead          = pod_overhead,
            overhead_source       = overhead_source,
        )
    else:
        print(f"\n  ⚠  Targets NOT met within {MAX_PODS} pods.")
        best = min(sweep_results, key=lambda r: r[1]["drop_rate"] + r[2]["p_wait"] / 1e9)
        print(f"     Best: {best[1]['drop_rate']*100:.2f}% batch drop  "
              f"p{SESSION_WAIT_PERCENTILE} wait={best[2]['p_wait']/1000:.1f}s  "
              f"at {best[0]} pods.")
        print(f"     Try: raise MAX_PODS, adjust BATCH_TIMEOUT_MS, "
              f"or relax targets.\n")
#!/usr/bin/env python3
"""
Automation script that:
1. Calls ../pa_sample_generator.py 50 times with inputs: 7, 8, and iteration number
2. Calls ../pod_sim.py on each generated CSV
3. Extracts "Minimum pods to meet target" from each run
4. Appends results to results.txt
"""

import subprocess
import sys
import os
import re
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_SCRIPT = os.path.join(SCRIPT_DIR, ".", "pa_sample_generator.py")
SIM_SCRIPT = os.path.join(SCRIPT_DIR, "..", "pod_sim.py")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "results.txt")
STUDENT_SUBMISSION_RATE = 3 # Run generator every iteration (no skipping)

NUM_STUDENTS = 3000
PA_SUBMISSION = "7"
PA_RUNTIME = "8"


def run_generator(iteration: int) -> str:
    """Run pa_sample_generator.py with inputs 7, 8, and the iteration number.
    Returns the expected output CSV filename."""
    inputs = f"{PA_SUBMISSION}\n{PA_RUNTIME}\n{iteration}\n"
    csv_filename = f"pa{PA_SUBMISSION}_hours_pa{PA_RUNTIME}_runtime_n{iteration}_synthetic.csv"

    print(f"[{iteration // STUDENT_SUBMISSION_RATE}/{NUM_STUDENTS}] Generating {csv_filename} ...")

    result = subprocess.run(
        [sys.executable, GENERATOR_SCRIPT],
        input=inputs,
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
    )

    if result.returncode != 0:
        print(f"  WARNING: Generator exited with code {result.returncode}")
        print(f"  STDERR: {result.stderr.strip()}")

    print(f"  → Generated {csv_filename}")

    return csv_filename


def run_sim(csv_filename: str) -> int | None:
    """Run pod_sim.py on the given CSV and extract the minimum pods value."""
    print(f"  Running pod_sim.py on {csv_filename} ...")

    result = subprocess.run(
        [sys.executable, SIM_SCRIPT, csv_filename],
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
    )

    if result.returncode != 0:
        print(f"  WARNING: Sim exited with code {result.returncode}")
        print(f"  STDERR: {result.stderr.strip()}")

    # Delete CSV now that we have the output
    try:
        os.remove(csv_filename)
    except OSError as e:
        print(f"  WARNING: Could not delete {csv_filename}: {e}")


    output = result.stdout
    match = re.search(r"Minimum pods to meet target\s*:\s*(\d+)", output)
    if match:
        value = int(match.group(1))
        print(f"  → Minimum pods to meet target: {value}")
        return value
    else:
        print(f"  WARNING: Could not find 'Minimum pods to meet target' in output.")
        print(f"  STDOUT was:\n{output[:500]}")
        return None


def main():
    print(f"Starting simulation — {NUM_STUDENTS} students")
    print(f"Generator : {GENERATOR_SCRIPT}")
    print(f"Simulator : {SIM_SCRIPT}")
    print(f"Results   : {OUTPUT_FILE}")
    print("-" * 50)

    with open(OUTPUT_FILE, "a") as f:
        f.write(f"{'Num Students':<12} {'Min Nodes'}\n")
        f.write("-" * 25 + "\n")

    now = datetime.now()
    # Format as HH:MM:SS
    current_time = now.strftime("%H:%M:%S")
    print("Current Time =", current_time)

    STUDENT_STEP = 10  # sample every 10th student

    for students in range(1, NUM_STUDENTS + 1, STUDENT_STEP):
        i = students * STUDENT_SUBMISSION_RATE
        csv_filename = run_generator(i)
        min_pods = run_sim(csv_filename)

        with open(OUTPUT_FILE, "a") as f:
            value_str = str(min_pods) if min_pods is not None else "N/A"
            num_students = i // STUDENT_SUBMISSION_RATE
            f.write(f"{num_students:<12} {value_str}\n")
            
    now = datetime.now()
    # Format as HH:MM:SS
    current_time = now.strftime("%H:%M:%S")
    print("Current Time =", current_time)

    print("-" * 50)
    print(f"Done. Results written to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

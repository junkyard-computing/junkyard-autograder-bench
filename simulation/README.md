# Pod Simulator — Running Guide

Two separate tools live in this project:

1. **Interactive Dev Simulator** — a live, interactive session simulator
2. **Node Count Estimator** — models node requirements from 1 student up to a configurable max

---

## 1. Interactive Dev Simulator

Runs directly from the project root. No virtual environment needed.

```bash
python pod_simulator.py
```

### Session CSV Files

Each session has a corresponding submissions CSV file. If you swap out the session, make sure you also swap in the matching CSV before running.

| Session   | Submissions CSV             |
| --------- | --------------------------- |
| Session A | `submissions_session_a.csv` |
| Session B | `submissions_session_b.csv` |
| ...       |

Update the CSV reference in `pod_simulator.py` using the constants to point to the correct file before starting the simulator for that session.

---

## 2. Node Count Estimator

Estimates the number of nodes required to support 1 student through `NUM_STUDENTS` concurrently. Run from inside the `runner/` directory.

### Setup

```bash
# 1. Navigate to the runner directory
cd runner

# 2. Create a virtual environment
python -m venv venv

# 3. Activate it
source venv/bin/activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Run the estimator
python run_simulation.py
```

### Key Constants

Two values at the top of `run_simulation.py` control the simulation:

| Constant                  | Default | Description                                                                                |
| ------------------------- | ------- | ------------------------------------------------------------------------------------------ |
| `NUM_STUDENTS`            | `3000`  | Upper bound — simulates load from 1 student up to this number                              |
| `STUDENT_SUBMISSION_RATE` | `3`     | Average number of submissions per student — update this if you have a more accurate figure |

> **Note:** The node count produced here does **not** include the interactive dev node. That runs separately (see Part 1 above) and should be accounted for on top of this estimate.

---

## Quick Reference

| Task                      | Command                                            |
| ------------------------- | -------------------------------------------------- |
| Run interactive simulator | `python pod_simulator.py` (from project root)      |
| Activate runner venv      | `source runner/venv/bin/activate`                  |
| Run node count estimator  | `python run_simulation.py` (from inside `runner/`) |

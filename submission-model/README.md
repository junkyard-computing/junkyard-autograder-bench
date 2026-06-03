# submission-model

Tools and data for modeling autograder submission behavior in CSE 160, used to generate realistic synthetic workloads for benchmarking.

## Overview

The pipeline has Five stages (one requires data per student):

1. **Export** — scrape raw submission history from Gradescope
2. **Filter / assign IDs** — strip PII and assign anonymous student IDs (requires data per student)
3. **Analyze** — fit Gaussian Mixture Models to submission timing and runtime distributions
4. **Synthesize** — generate synthetic submissions and infer interactive dev sessions from them
5. **Dev Sessions** — generates inferred student development sessions from historical data (requires data per student)

---

## Directory structure

```
submission-model/
├── cse160-data/
│   ├── filtered-data/           # Anonymized per-PA CSVs (pa1–pa8)
│   │   ├── gs_history_export.py # Gradescope scraper (Playwright)
│   │   └── pa*_out_filtered.csv
│   ├── gmm-figures/             # GMM plots (timing + runtime per PA)
│   ├── synthetic-gmm-data/      # Synthetic CSVs at various sample sizes
│   ├── PA_submission_time_analysis.py  # GMM over submission timing (hours)
│   ├── PA_runtime_analysis.py          # GMM over autograder runtimes (log-space)
│   ├── pa_gmm.py                       # 2D GMM (time × runtime), 3D surface plot
│   └── pa_sample_generator.py          # Generates synthetic submission CSVs
├── id-assigner/                 # Rust CLI: assigns anonymized students with mock student IDs
├── interactive-dev-sessions/    # Rust CLI: infer dev sessions from historic student submissions
└── synthetic-data/              # See Google Drive (linked in README there)
```

---

## Stage 1 — Export from Gradescope

`cse160-data/filtered-data/gs_history_export.py` uses Playwright to log in and scrape every submission's full history for a given course/assignment.

**Dependencies:** `playwright`, Python 3.11+

**Usage:**
```bash
python gs_history_export.py <email> <course_id> <assignment_id> <out.csv>
# prompts for password, or pass it as a second positional arg
```

**Output columns:** `student_name`, `student_email`, `current_submission_id`, `submission_id`, `attempt_number`, `submission_time`, `runtime_ms`, `score`, `active`, URLs, `error`

---

## Stage 2 — Filter and assign anonymous IDs

**Please make sure to filter studnet data first in compliance with the Family Educational Rights and Privacy Act**

`id-assigner` is a Rust CLI that replaces student identity with a sequential integer ID. It relies on the Gradescope scraper grouping each student's submissions together consecutively.

```bash
cargo run --package id-assigner --bin id-assigner -- <input.csv> <output.csv>
```

**Output columns:** `student_id`, `attempt_number`, `submission_time`, `runtime_ms`, `score`

Note that this only works for this specific project because the gradescope scraper is set up this way

---

## Stage 3 — Fit GMMs

Three Python scripts analyze the filtered CSVs. All require `numpy`, `pandas`, `scikit-learn`, `matplotlib`.

### Submission timing (`PA_submission_time_analysis.py`)

Fits a GMM (BIC model selection) over hours-since-first-submission for a chosen PA. Saves a density plot to `gmm-figures/gmm_hours_PA<n>.png`.

```bash
python PA_submission_time_analysis.py
# prompts: Enter PA number
```

### Autograder runtime (`PA_runtime_analysis.py`)

Fits a GMM in log-space over valid runtimes (excludes pre-exec failures; treats `-1` timeouts as censored at 837,290 ms). Saves a plot to `gmm-figures/gmm_runtime_PA<n>.png`.

```bash
python PA_runtime_analysis.py
# prompts: Enter PA number (3,4,5,7,8)
```

### 2D joint model (`pa_gmm.py`)

Fits a 2-component GMM over (hours since first submission, log runtime) and renders a 3D surface plot.

```bash
python pa_gmm.py <pa_csv> <out_file>   # save plot
python pa_gmm.py <pa_csv>              # display interactively
```

---

## Stage 4 — Generate synthetic data

### Synthetic submissions (`pa_sample_generator.py`)

Fits independent timing and runtime GMMs (same BIC selection as above), then draws `n` samples. Timing and runtime can come from different PAs.

```bash
python pa_sample_generator.py
# prompts: PA for timing, PA for runtimes, number of samples
```

**Output columns:** `attempt_number`, `hours_since_first`, `runtime_ms`, `score`, `active`

Pre-generated files live in `synthetic-gmm-data/`, named `pa<hours>_hours_pa<runtime>_runtime_n<N>_synthetic.csv`.

### Interactive dev sessions (`interactive-dev-sessions`)

Converts a submission CSV into inferred dev session records. Groups each student's submissions into bursts (gap > `--burst-hours`), then places a session start some hours before the first submission in each burst, sampled from a Gaussian.

```bash
cargo run -- --input submissions.csv --output sessions.csv
cargo run -- --input submissions.csv --output sessions.csv --pre-hours 3 --burst-hours 6
```

| Flag | Default | Description |
|---|---|---|
| `--input, -i` | required | Submission CSV with `student_id` and `submission_time` (RFC 3339) |
| `--output, -o` | required | Output session CSV |
| `--pre-hours` | `3` | Hours before first submission to place session start |
| `--burst-hours` | `6` | Gap threshold that starts a new burst |

**Input columns (required):** `student_id`, `submission_time`  
**Input columns (also read):** `attempt_number`, `runtime_ms`, `score`

**Output columns:** `timestamp`, `length_seconds`, `student_id`

> Repeated runs produce slightly different session boundaries because session offsets are sampled stochastically.

---

## Stage 5 — Generate synthetic data

Generates inferred student interactive development sessions on cluster. This step requires mock student ID and submission data per student.

For details, see [README](interactive-dev-sessions/README.md)

---

## Runtime notes

- `runtime_ms = -1` means the autograder timed out (capped at 837,290 ms in analysis)
- `runtime_ms` blank/NaN means a pre-execution failure (excluded from runtime GMMs)
- `active = true` marks the submission the student chose as their final grade

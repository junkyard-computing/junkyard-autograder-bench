# Interactive Dev Session Generator

Converts a CSV of student submissions into interactive development session bursts.

It basically just takes timestamped submissions, groups them by student, splits them into bursts when the gap between
consecutive submissions is large enough, and writes one session per burst.

## Project structure

- `src/main.rs` — program entrypoint
- `src/args.rs` — CLI argument parsing
- `src/models.rs` — CSV row and session data structures
- `src/io.rs` — input/output helpers
- `src/session.rs` — burst detection and session synthesis

## Input CSV

The program expects a CSV with at least these columns:

- `student_id`
- `submission_time`

The current code also deserializes these additional fields for compatibility with the original dataset:

- `attempt_number`
- `runtime_ms`
- `score`

`submission_time` must be in RFC 3339 format, for example:

```text
2024-12-01T14:30:00-08:00
```

## Output CSV

The output file contains these columns:

- `timestamp` — inferred session start time in RFC 3339 format
- `length_seconds` — inferred session length as a compact decimal string
- `student_id` — student identifier

## How it works

For each student:

1. Their submission timestamps are sorted.
2. A new burst starts when the gap between two submissions exceeds the configured threshold.
3. For each burst, the session start is placed some hours before the first submission in that burst.
4. The session end is extended slightly past the last submission for students lingering to check for autograder score.

The timing offsets are sampled from Gaussian distributions with the variance constants defined in `src/session.rs`.

## Build

```bash
cargo build --release
```

## Run

```bash
cargo run -- --input input.csv --output output.csv
```

### CLI options

- `--input, -i` — path to the submission CSV
- `--output, -o` — path to the generated session CSV
- `--pre-hours` — hours before the first submission to place the inferred session start, default: `3`
- `--burst-hours` — maximum gap between submissions before a new burst is started, default: `6`

## Example

```bash
cargo run --   --input submissions.csv   --output sessions.csv   (Optional) --pre-hours 3   (Optional) --burst-hours 6
```

## Notes

- The code uses a random draw when creating each session, so repeated runs can produce slightly different inferred session boundaries.
- If the input contains malformed timestamps, the run will fail with a parse error.

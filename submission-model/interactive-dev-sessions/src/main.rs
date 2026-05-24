use chrono::{DateTime, Duration, FixedOffset};
use clap::Parser;
use csv::{ReaderBuilder, WriterBuilder};
use rand::Rng;
use rand_distr::{Distribution, Normal};
use serde::Deserialize;
use std::collections::BTreeMap;
use std::error::Error;
use std::path::PathBuf;

/// Convert submission into inferred student interactive session usage.
///
/// Based on paper: Karsai, M., & Jo, H.-H. (2024). Measuring and Modeling Bursty Human Phenomena.
/// arXiv. https://doi.org/10.48550/arXiv.2412.13617

#[derive(Parser, Debug)]
#[command(author, version, about)]
struct Args {
    /// Input CSV with columns:
    /// student_id,attempt_number,submission_time,runtime_ms,score
    /// use the ID assigner that is also in the repo if missing student ID
    #[arg(short, long)]
    input: PathBuf,

    /// Output CSV path.
    #[arg(short, long)]
    output: PathBuf,

    /// Hours before the first submission to place the session start
    /// This is what we assume to be the average minimum time students will spend on assignment
    #[arg(long, default_value_t = 2)]
    pre_hours: i64,

    /// If the gap between two consecutive submissions exceeds this many hours,
    /// start a new burst.
    #[arg(long, default_value_t = 3)]
    burst_hours: i64,
}

#[derive(Debug, Deserialize)]
struct SubmissionRow {
    student_id: String,
    attempt_number: Option<u32>,
    submission_time: String,
    runtime_ms: Option<f64>,
    score: Option<f64>,
}

fn gaussian_gen<R: Rng + ?Sized>(rng: &mut R, mean: i64, variance: f64) -> i64 {
    assert!(variance >= 0.0, "Variance is negative");

    if variance == 0.0 {
        return mean;
    }

    let std_dev = variance.sqrt();
    let normal = Normal::new(mean as f64, std_dev).unwrap();
    let value = normal.sample(rng);
    value.round() as i64
}

#[derive(Debug, Clone)]
struct SessionRow {
    student_id: String,
    timestamp: DateTime<FixedOffset>,
    length_seconds: f64,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();

    let mut rdr = ReaderBuilder::new()
        .trim(csv::Trim::All)
        .from_path(&args.input)?;

    // Group submissions by student.
    let mut by_student: BTreeMap<String, Vec<DateTime<FixedOffset>>> = BTreeMap::new();

    for result in rdr.deserialize::<SubmissionRow>() {
        let row = result?;
        let ts = DateTime::parse_from_rfc3339(&row.submission_time)?;
        by_student.entry(row.student_id).or_default().push(ts);
    }

    let pre = args.pre_hours;
    let burst_threshold = Duration::hours(args.burst_hours);

    let mut sessions: Vec<SessionRow> = Vec::new();

    for (student_id, mut times) in by_student {
        if times.is_empty() {
            continue;
        }
        times.sort();
        let mut burst_start_idx = 0usize;
        for i in 1..times.len() {
            let gap = times[i] - times[i - 1];
            if gap > burst_threshold {
                sessions.push(make_session(
                    &student_id,
                    &times,
                    burst_start_idx,
                    i - 1,
                    pre,
                )?);
                burst_start_idx = i;
            }
        }

        sessions.push(make_session(
            &student_id,
            &times,
            burst_start_idx,
            times.len() - 1,
            pre,
        )?);
    }

    // Sort by timestamp
    sessions.sort_by(|a, b| {
        a.timestamp
            .cmp(&b.timestamp)
            .then_with(|| a.student_id.cmp(&b.student_id))
    });

    let mut wtr = WriterBuilder::new().from_path(&args.output)?;
    wtr.write_record(["timestamp", "length_seconds", "student_id"])?;

    for session in sessions {
        wtr.write_record([
            session.timestamp.to_rfc3339(),
            format_duration_seconds(session.length_seconds),
            session.student_id,
        ])?;
    }

    wtr.flush()?;
    Ok(())
}

fn make_session(
    student_id: &str,
    times: &[DateTime<FixedOffset>],
    start_idx: usize,
    end_idx: usize,
    pre: i64,
) -> Result<SessionRow, Box<dyn Error>> {
    let mut rng = rand::thread_rng();
    let first = times[start_idx];
    let last = times[end_idx];
    let timestamp = first - Duration::hours(gaussian_gen(&mut rng, pre, 1.0));

    // Session length is from the inferred start time to the last submission in the burst.
    let length = last - timestamp;
    let length_seconds = length
        .to_std()
        .map(|d| d.as_secs_f64())
        .unwrap_or_else(|_| 0.0);

    Ok(SessionRow {
        student_id: student_id.to_string(),
        timestamp,
        length_seconds,
    })
}

fn format_duration_seconds(secs: f64) -> String {
    // Keep a compact decimal representation, but avoid scientific notation.
    if secs.fract() == 0.0 {
        format!("{:.0}", secs)
    } else {
        let s = format!("{:.6}", secs);
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

/*
If you want the session length to be exactly 2 hours before the first submission, change

    let length = last - timestamp;

to:

    let length = Duration::hours(2);

*/

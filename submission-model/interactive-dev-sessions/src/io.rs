use crate::models::{SessionRow, SubmissionRow};
use chrono::{DateTime, FixedOffset};
use csv::{ReaderBuilder, WriterBuilder};
use std::collections::BTreeMap;
use std::error::Error;
use std::path::Path;

pub fn read_submission_times(
    input: &Path,
) -> Result<BTreeMap<String, Vec<DateTime<FixedOffset>>>, Box<dyn Error>> {
    let mut rdr = ReaderBuilder::new().trim(csv::Trim::All).from_path(input)?;

    let mut by_student: BTreeMap<String, Vec<DateTime<FixedOffset>>> = BTreeMap::new();

    for result in rdr.deserialize::<SubmissionRow>() {
        let row = result?;
        let ts = DateTime::parse_from_rfc3339(&row.submission_time)?;
        by_student.entry(row.student_id).or_default().push(ts);
    }

    Ok(by_student)
}

pub fn write_sessions(output: &Path, sessions: &[SessionRow]) -> Result<(), Box<dyn Error>> {
    let mut wtr = WriterBuilder::new().from_path(output)?;
    wtr.write_record(["timestamp", "length_seconds", "student_id"])?;

    for session in sessions {
        wtr.write_record([
            session.timestamp.to_rfc3339(),
            format_duration_seconds(session.length_seconds),
            session.student_id.clone(),
        ])?;
    }

    wtr.flush()?;
    Ok(())
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

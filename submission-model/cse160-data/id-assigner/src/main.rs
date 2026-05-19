use std::env;
use std::error::Error;
use std::fs::File;
use std::io::{BufReader, BufWriter};

use csv::{ReaderBuilder, WriterBuilder};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Submission {
    attempt_number: u32,
    submission_time: String,
    runtime_ms: Option<f64>,
    score: Option<f64>,
}

fn main() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = env::args().collect();

    if args.len() != 3 {
        eprintln!("Usage: {} <input.csv> <output.csv>", args[0]);
        std::process::exit(1);
    }

    let input_path = &args[1];
    let output_path = &args[2];

    let input_file = File::open(input_path)?;
    let output_file = File::create(output_path)?;

    let mut reader = ReaderBuilder::new()
        .trim(csv::Trim::All)
        .from_reader(BufReader::new(input_file));

    let mut writer = WriterBuilder::new()
        .from_writer(BufWriter::new(output_file));

    // Write header with the new column.
    writer.write_record([
        "student_id",
        "attempt_number",
        "submission_time",
        "runtime_ms",
        "score",
    ])?;

    let mut current_student_id: u32 = 1;
    let mut saw_first_row = false;

    for result in reader.deserialize::<Submission>() {
        let row = result?;

        // A new student starts whenever attempt_number resets to 1,
        // except for the very first row.
        if saw_first_row && row.attempt_number == 1 {
            current_student_id += 1;
        }
        saw_first_row = true;

        writer.write_record([
            current_student_id.to_string(),
            row.attempt_number.to_string(),
            row.submission_time,
            row.runtime_ms.map_or_else(String::new, |v| v.to_string()),
            row.score.map_or_else(String::new, |v| v.to_string()),
        ])?;
    }

    writer.flush()?;
    Ok(())
}

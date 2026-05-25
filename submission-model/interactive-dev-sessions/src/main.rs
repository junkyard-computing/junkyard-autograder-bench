mod args;
mod io;
mod models;
mod session;

use std::error::Error;

use args::Args;
use clap::Parser;
use io::{read_submission_times, write_sessions};
use session::build_sessions;

fn main() -> Result<(), Box<dyn Error>> {
    let args = Args::parse();

    let by_student = read_submission_times(&args.input)?;
    let sessions = build_sessions(&by_student, args.pre_hours, args.burst_hours)?;

    write_sessions(&args.output, &sessions)?;

    Ok(())
}

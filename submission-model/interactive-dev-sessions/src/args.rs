use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(author, version, about)]
pub struct Args {
    /// Input CSV with columns:
    /// student_id,attempt_number,submission_time,runtime_ms,score
    /// use the ID assigner that is also in the repo if missing student ID
    #[arg(short, long)]
    pub input: PathBuf,

    /// Output CSV path.
    #[arg(short, long)]
    pub output: PathBuf,

    /// Hours before the first submission to place the session start
    /// This is what we assume to be the average minimum time students will spend on assignment
    #[arg(long, default_value_t = 3)]
    pub pre_hours: i64,

    /// If the gap between two consecutive submissions exceeds this many hours,
    /// start a new burst.
    #[arg(long, default_value_t = 6)]
    pub burst_hours: i64,
}

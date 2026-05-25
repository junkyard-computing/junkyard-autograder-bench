use chrono::{DateTime, FixedOffset};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
pub struct SubmissionRow {
    pub student_id: String,
    pub attempt_number: Option<u32>,
    pub submission_time: String,
    pub runtime_ms: Option<f64>,
    pub score: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct SessionRow {
    pub student_id: String,
    pub timestamp: DateTime<FixedOffset>,
    pub length_seconds: f64,
}

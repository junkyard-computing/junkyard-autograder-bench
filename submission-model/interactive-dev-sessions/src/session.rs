use crate::models::SessionRow;
use chrono::{DateTime, Duration, FixedOffset};
use rand::Rng;
use rand_distr::{Distribution, Normal};
use std::error::Error;

static PRE_VARIANCE: f64 = 1.0;
static POST_VARIANCE: f64 = 0.1;

fn gaussian_gen<R: Rng + ?Sized>(rng: &mut R, mean: i64, variance: f64) -> f64 {
    assert!(variance >= 0.0, "Variance is negative");

    if variance == 0.0 {
        return mean as f64;
    }

    let std_dev = variance.sqrt();
    let normal = Normal::new(mean as f64, std_dev).unwrap();
    let mut value = normal.sample(rng);

    if value < 0.0 {
        value = 0.0;
    }

    value
}

pub fn build_sessions(
    by_student: &std::collections::BTreeMap<String, Vec<DateTime<FixedOffset>>>,
    pre_hours: i64,
    burst_hours: i64,
) -> Result<Vec<SessionRow>, Box<dyn Error>> {
    let burst_threshold = Duration::hours(burst_hours);
    let mut sessions: Vec<SessionRow> = Vec::new();

    for (student_id, times) in by_student {
        if times.is_empty() {
            continue;
        }

        let mut times = times.clone();
        times.sort();

        let mut burst_start_idx = 0usize;
        for i in 1..times.len() {
            let gap = times[i] - times[i - 1];
            if gap > burst_threshold {
                sessions.push(make_session(
                    student_id,
                    &times,
                    burst_start_idx,
                    i - 1,
                    pre_hours,
                )?);
                burst_start_idx = i;
            }
        }

        sessions.push(make_session(
            student_id,
            &times,
            burst_start_idx,
            times.len() - 1,
            pre_hours,
        )?);
    }

    sessions.sort_by(|a, b| {
        a.timestamp
            .cmp(&b.timestamp)
            .then_with(|| a.student_id.cmp(&b.student_id))
    });

    Ok(sessions)
}

fn make_session(
    student_id: &str,
    times: &[DateTime<FixedOffset>],
    start_idx: usize,
    end_idx: usize,
    pre_hours: i64,
) -> Result<SessionRow, Box<dyn Error>> {
    let mut rng = rand::thread_rng();
    let first = times[start_idx];
    let last = times[end_idx];

    let pre_seconds = gaussian_gen(&mut rng, pre_hours, PRE_VARIANCE) * 3600.0;
    let timestamp = first - Duration::seconds(pre_seconds as i64);

    let post_seconds = (gaussian_gen(&mut rng, 0, POST_VARIANCE) * 3600.0).max(0.0);
    let posttime = Duration::seconds(post_seconds as i64);

    let length = last - timestamp + posttime;
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

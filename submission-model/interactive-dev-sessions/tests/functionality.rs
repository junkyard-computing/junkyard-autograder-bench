use chrono::{DateTime, FixedOffset};
use std::collections::BTreeMap;
use std::fs;
use tempfile::NamedTempFile;

use interactive_dev_sessions::io::{read_submission_times, write_sessions};
use interactive_dev_sessions::models::SessionRow;
use interactive_dev_sessions::session::build_sessions;

fn dt(s: &str) -> DateTime<FixedOffset> {
    DateTime::parse_from_rfc3339(s).expect("valid RFC3339 timestamp")
}

#[test]
fn read_submission_times_groups_rows_by_student() {
    let csv = r#"student_id,attempt_number,submission_time,runtime_ms,score
s2,1,2024-01-02T10:00:00-08:00,1000,0.8
s1,1,2024-01-01T09:00:00-08:00,900,0.9
s2,2,2024-01-02T11:30:00-08:00,1200,0.7
"#;

    let file = NamedTempFile::new().unwrap();
    fs::write(file.path(), csv).unwrap();

    let map = read_submission_times(file.path()).unwrap();

    assert_eq!(map.len(), 2);
    assert_eq!(map["s1"], vec![dt("2024-01-01T09:00:00-08:00")]);
    assert_eq!(map["s2"], vec![
        dt("2024-01-02T10:00:00-08:00"),
        dt("2024-01-02T11:30:00-08:00"),
    ]);
}

#[test]
fn build_sessions_splits_bursts_and_sorts_final_output() {
    let mut by_student: BTreeMap<String, Vec<DateTime<FixedOffset>>> = BTreeMap::new();
    by_student.insert(
        "s1".to_string(),
        vec![
            dt("2024-01-01T23:00:00-08:00"),
            dt("2024-01-02T00:15:00-08:00"),
            dt("2024-01-03T23:00:00-08:00"),
        ],
    );
    by_student.insert(
        "s2".to_string(),
        vec![dt("2024-01-02T12:00:00-08:00")],
    );

    let sessions = build_sessions(&by_student, 3, 6).unwrap();

    assert_eq!(sessions.len(), 3);
    assert_eq!(sessions[0].student_id, "s1");
    assert_eq!(sessions[1].student_id, "s2");
    assert_eq!(sessions[2].student_id, "s1");
    assert!(sessions.iter().all(|s| s.length_seconds > 0.0));

    assert!(sessions[0].timestamp < sessions[1].timestamp);
    assert!(sessions[1].timestamp < sessions[2].timestamp);
}

#[test]
fn write_sessions_emits_expected_csv_format() {
    let file = NamedTempFile::new().unwrap();
    let sessions = vec![
        SessionRow {
            student_id: "s1".to_string(),
            timestamp: dt("2024-01-01T07:00:00-08:00"),
            length_seconds: 120.0,
        },
        SessionRow {
            student_id: "s2".to_string(),
            timestamp: dt("2024-01-02T07:00:00-08:00"),
            length_seconds: 12.3456789,
        },
    ];

    write_sessions(file.path(), &sessions).unwrap();
    let contents = fs::read_to_string(file.path()).unwrap();

    let expected = concat!(
        "timestamp,length_seconds,student_id\n",
        "2024-01-01T07:00:00-08:00,120,s1\n",
        "2024-01-02T07:00:00-08:00,12.345679,s2\n",
    );

    assert_eq!(contents, expected);
}

#![allow(
    clippy::panic_in_result_fn,
    reason = "Test assertions fail the test; Result propagates fixture I/O errors."
)]

use std::io::{self, Write};

struct FailingWriter;
impl Write for FailingWriter {
    fn write(&mut self, _bytes: &[u8]) -> io::Result<usize> {
        Err(io::ErrorKind::BrokenPipe.into())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn library_propagates_write_errors_without_cli_setup() {
    let result = biggie::gen_random_lines(FailingWriter, 2);
    assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe));
}

#[test]
fn library_can_run_repeatedly_without_initialization() -> io::Result<()> {
    for lines in [0, 1, 3] {
        let mut output = Vec::new();
        biggie::gen_random_lines(&mut output, lines)?;
        assert_eq!(
            String::from_utf8(output)
                .map_err(io::Error::other)?
                .lines()
                .count(),
            usize::try_from(lines).map_err(io::Error::other)?
        );
    }
    Ok(())
}

struct FailsAfterPrefix {
    accepted: Vec<u8>,
}

impl Write for FailsAfterPrefix {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let count = bytes.len().min(3_usize.saturating_sub(self.accepted.len()));
        if count == 0 {
            return Err(io::ErrorKind::BrokenPipe.into());
        }
        self.accepted.extend(bytes.iter().take(count).copied());
        Ok(count)
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[test]
fn partial_write_preserves_bytes_and_does_not_report_completed_generation()
-> Result<(), Box<dyn std::error::Error>> {
    use tracing::level_filters::LevelFilter;
    use tracing_subscriber::{fmt::format::FmtSpan, util::SubscriberInitExt};

    let dir = assert_fs::TempDir::new()?;
    let log = dir.path().join("events.jsonl");
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(LevelFilter::TRACE)
        .with_span_events(FmtSpan::CLOSE)
        .json()
        .with_writer(std::fs::File::create(&log)?)
        .finish();
    let mut writer = FailsAfterPrefix {
        accepted: Vec::new(),
    };
    let guard = subscriber.set_default();
    let result = biggie::gen_random_lines(&mut writer, 2);
    drop(guard);
    assert!(result.is_err_and(|error| error.kind() == io::ErrorKind::BrokenPipe));
    assert_eq!(writer.accepted.len(), 3);
    let records: Vec<serde_json::Value> = std::fs::read_to_string(log)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert!(records.iter().any(
        |record| record["span"]["name"] == "generate" && record["fields"]["message"] == "close"
    ));
    for record in records {
        assert!(record.pointer("/fields/lines_written").is_none());
        assert!(record.pointer("/span/lines_written").is_none());
        assert_ne!(
            record.pointer("/fields/message"),
            Some(&serde_json::json!("generation complete"))
        );
    }
    Ok(())
}

#[test]
fn filtered_generation_does_not_update_the_callers_parent_span()
-> Result<(), Box<dyn std::error::Error>> {
    use tracing::level_filters::LevelFilter;
    use tracing_subscriber::fmt::format::FmtSpan;

    let dir = assert_fs::TempDir::new()?;
    let log = dir.path().join("host.jsonl");
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(LevelFilter::INFO)
        .with_span_events(FmtSpan::CLOSE)
        .json()
        .with_writer(std::fs::File::create(&log)?)
        .finish();
    tracing::subscriber::with_default(subscriber, || {
        let _parent = tracing::info_span!("host", lines_written = 99).entered();
        biggie::gen_random_lines(Vec::new(), 2)
    })?;
    let records: Vec<serde_json::Value> = std::fs::read_to_string(log)?
        .lines()
        .map(serde_json::from_str)
        .collect::<Result<_, _>>()?;
    assert_eq!(records.len(), 1);
    for record in records {
        assert_eq!(record["span"]["name"], "host");
        assert_eq!(record["span"]["lines_written"], 99);
    }
    Ok(())
}

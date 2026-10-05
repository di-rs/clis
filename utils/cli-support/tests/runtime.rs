#![allow(
    clippy::panic_in_result_fn,
    reason = "Test assertions fail the test; Result propagates fixture I/O errors."
)]

use cli_support::{Config, Destination, Format, Runtime};
use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
};
use tracing::{dispatcher::with_default, level_filters::LevelFilter};

type Result = std::result::Result<(), Box<dyn std::error::Error>>;

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);
impl Write for Capture {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0
            .lock()
            .map_err(|_| io::Error::other("capture poisoned"))?
            .extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
impl Capture {
    fn text(&self) -> std::result::Result<String, Box<dyn std::error::Error>> {
        Ok(String::from_utf8(
            self.0.lock().map_err(|_| "capture poisoned")?.clone(),
        )?)
    }
}

fn events() {
    tracing::error!("error event");
    tracing::warn!("warn event");
    tracing::info!("info event");
    tracing::debug!("debug event");
    tracing::trace!("trace event");
}

#[test]
fn all_thresholds_filter_events_and_repeated_scopes_work() -> Result {
    for (level, expected) in [
        (LevelFilter::OFF, 0),
        (LevelFilter::ERROR, 1),
        (LevelFilter::WARN, 2),
        (LevelFilter::INFO, 3),
        (LevelFilter::DEBUG, 4),
        (LevelFilter::TRACE, 5),
    ] {
        let capture = Capture::default();
        let runtime = Runtime::with_writer(
            &Config {
                level,
                ..Config::default()
            },
            capture.clone(),
        );
        with_default(runtime.dispatch(), events);
        runtime.finish()?;
        assert_eq!(capture.text()?.lines().count(), expected);
    }
    Ok(())
}

#[test]
fn json_preserves_fields_and_escapes_messages() -> Result {
    let capture = Capture::default();
    let runtime = Runtime::with_writer(
        &Config {
            level: LevelFilter::INFO,
            format: Format::Json,
            ..Config::default()
        },
        capture.clone(),
    );
    with_default(runtime.dispatch(), || {
        let span = tracing::info_span!("operation", count = 3);
        let _entered = span.enter();
        tracing::info!(answer = 42, "Unicode 🦀\nmessage");
    });
    runtime.finish()?;
    let text = capture.text()?;
    assert_eq!(text.lines().count(), 1);
    let json: serde_json::Value = serde_json::from_str(&text)?;
    assert_eq!(json.pointer("/fields/answer"), Some(&serde_json::json!(42)));
    assert_eq!(
        json.pointer("/fields/message"),
        Some(&serde_json::json!("Unicode 🦀\nmessage"))
    );
    Ok(())
}

#[test]
fn timings_work_with_logging_off_and_record_final_counts() -> Result {
    for format in [Format::Text, Format::Json] {
        let capture = Capture::default();
        let runtime = Runtime::with_writer(
            &Config {
                timings: true,
                format,
                ..Config::default()
            },
            capture.clone(),
        );
        with_default(runtime.dispatch(), || {
            events();
            let span =
                tracing::info_span!(target: "clis::timing", "scan", lines = tracing::field::Empty);
            span.record("lines", 12_u64);
        });
        runtime.finish()?;
        let text = capture.text()?;
        assert_eq!(text.lines().count(), 1);
        assert!(text.contains("scan"));
        assert!(text.contains("elapsed_ms"));
        if format == Format::Json {
            let json: serde_json::Value = serde_json::from_str(&text)?;
            assert_eq!(json.pointer("/fields/lines"), Some(&serde_json::json!(12)));
            assert!(
                json.get("elapsed_ms")
                    .and_then(serde_json::Value::as_f64)
                    .is_some_and(|n| n >= 0.0)
            );
        }
    }
    Ok(())
}

#[test]
fn disabled_timings_do_not_evaluate_fields_even_when_logs_enabled() -> Result {
    let capture = Capture::default();
    let runtime = Runtime::with_writer(
        &Config {
            level: LevelFilter::TRACE,
            ..Config::default()
        },
        capture.clone(),
    );
    let mut evaluated = false;
    with_default(runtime.dispatch(), || {
        let _span =
            tracing::info_span!(target: "clis::timing", "scan", count = { evaluated = true; 1 });
    });
    runtime.finish()?;
    assert!(!evaluated);
    assert_eq!(capture.text()?, "");
    Ok(())
}

struct Fails {
    flush_only: bool,
}
impl Write for Fails {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.flush_only {
            Ok(bytes.len())
        } else {
            Err(io::Error::other("write failed"))
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.flush_only {
            Err(io::Error::other("flush failed"))
        } else {
            Ok(())
        }
    }
}

#[test]
fn finish_reports_write_and_flush_failures_in_both_formats() {
    for format in [Format::Text, Format::Json] {
        for flush_only in [false, true] {
            let runtime = Runtime::with_writer(
                &Config {
                    level: LevelFilter::INFO,
                    format,
                    ..Config::default()
                },
                Fails { flush_only },
            );
            with_default(runtime.dispatch(), || tracing::info!("event"));
            assert!(runtime.finish().is_err());
            assert!(runtime.finish().is_err());
        }
    }
}

#[test]
fn timing_sink_errors_are_reported() {
    let runtime = Runtime::with_writer(
        &Config {
            timings: true,
            ..Config::default()
        },
        Fails { flush_only: false },
    );
    with_default(runtime.dispatch(), || {
        let _span = tracing::info_span!(target: "clis::timing", "scan");
    });
    assert!(runtime.finish().is_err());
}

#[test]
fn file_destination_never_overwrites_existing_data() -> Result {
    let dir = assert_fs::TempDir::new()?;
    let path = dir.path().join("events");
    let config = Config {
        destination: Destination::File(path.clone()),
        level: LevelFilter::INFO,
        ..Config::default()
    };
    let runtime = Runtime::new(&config)?;
    with_default(runtime.dispatch(), || tracing::info!("retained"));
    runtime.finish()?;
    assert!(Runtime::new(&config).is_err());
    assert!(std::fs::read_to_string(path)?.contains("retained"));
    Ok(())
}

#[test]
fn explicit_dispatch_propagates_to_workers_without_mixed_json_records() -> Result {
    let capture = Capture::default();
    let runtime = Runtime::with_writer(
        &Config {
            level: LevelFilter::INFO,
            format: Format::Json,
            ..Config::default()
        },
        capture.clone(),
    );
    std::thread::scope(|scope| {
        for worker in 0..4 {
            let dispatch = runtime.dispatch().clone();
            scope.spawn(move || {
                with_default(&dispatch, || {
                    for sequence in 0..25 {
                        tracing::info!(worker, sequence, "record");
                    }
                });
            });
        }
    });
    runtime.finish()?;
    let text = capture.text()?;
    assert_eq!(text.lines().count(), 100);
    for line in text.lines() {
        let _: serde_json::Value = serde_json::from_str(line)?;
    }
    Ok(())
}

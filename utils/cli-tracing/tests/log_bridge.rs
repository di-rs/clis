#![allow(
    clippy::panic_in_result_fn,
    reason = "Test assertions fail the test; Result propagates fixture I/O errors."
)]

use cli_tracing::{Config, Destination, Format, TracingSession};
use tracing::{dispatcher::with_default, level_filters::LevelFilter};

// Keep the once-only global logger test in its own integration-test process.
#[test]
fn log_bridge_obeys_scoped_filters_without_duplicating_tracing_events()
-> Result<(), Box<dyn std::error::Error>> {
    tracing_log::LogTracer::init()?;
    assert!(tracing_log::LogTracer::init().is_err());
    let dir = assert_fs::TempDir::new()?;

    for (index, (level, expected)) in [
        (LevelFilter::OFF, 0),
        (LevelFilter::ERROR, 1),
        (LevelFilter::WARN, 2),
        (LevelFilter::INFO, 4),
        (LevelFilter::DEBUG, 5),
        (LevelFilter::TRACE, 6),
    ]
    .into_iter()
    .enumerate()
    {
        let path = dir.path().join(index.to_string());
        let session = TracingSession::new(&Config {
            level,
            format: Format::Json,
            destination: Destination::File(path.clone()),
            ..Config::default()
        })?;
        log::error!("outside scope before");
        with_default(session.dispatch(), || {
            log::error!(target: "dependency", "error 🦀\nmessage");
            log::warn!("warn message");
            log::info!("info message");
            log::debug!("debug message");
            log::trace!("trace message");
            tracing::info!("native tracing message");
        });
        log::error!("outside scope after");
        session.finish()?;
        let text = std::fs::read_to_string(path)?;
        let records = text
            .lines()
            .map(serde_json::from_str::<serde_json::Value>)
            .collect::<Result<Vec<_>, _>>()?;
        assert_eq!(records.len(), expected);
        assert!(!text.contains("outside scope"));
        if let Some(record) = records.first() {
            assert_eq!(record["level"], "ERROR");
            assert_eq!(record["fields"]["message"], "error 🦀\nmessage");
            assert_eq!(record["fields"]["log.target"], "dependency");
        }
        let native_count = records
            .iter()
            .filter(|record| record["fields"]["message"] == "native tracing message")
            .count();
        assert_eq!(native_count, usize::from(level >= LevelFilter::INFO));
    }
    Ok(())
}

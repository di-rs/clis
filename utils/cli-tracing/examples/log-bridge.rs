//! A standalone application owns both once-only global installations.

use cli_tracing::{Config, Format, TracingSession};
use tracing::level_filters::LevelFilter;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let session = TracingSession::new(&Config {
        level: LevelFilter::TRACE,
        format: Format::Json,
        ..Config::default()
    })?;
    tracing_log::LogTracer::init()?;
    tracing::dispatcher::set_global_default(session.dispatch().clone())?;

    log::error!("error example");
    log::warn!("warn example");
    log::info!("info example");
    log::debug!("debug example");
    log::trace!("trace example");
    tracing::info!(items = 1, "native tracing example");

    // Joined before finish: the global subscriber is visible to new threads.
    std::thread::scope(|scope| {
        scope.spawn(|| log::info!("worker example"));
    });
    session.finish()?;
    Ok(())
}

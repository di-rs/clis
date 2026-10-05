use crate::{Config, Destination, Format, sink::Sink};
use std::{
    fs::OpenOptions,
    io::{self, Write},
};
use tracing::{Dispatch, Subscriber};
use tracing_subscriber::{
    Layer, filter::filter_fn, fmt::format::FmtSpan, layer::SubscriberExt, registry::LookupSpan,
};

/// A scoped subscriber and owned diagnostic sink, with explicit checked finishing.
///
/// No global logger/subscriber or panic hook is installed. Use `dispatch()` with
/// `tracing::dispatcher::with_default`; clone it explicitly for worker threads.
/// Close spans and stop emitting before calling `finish()`.
pub struct TracingSession {
    dispatch: Dispatch,
    sink: Sink,
}

impl TracingSession {
    /// Open the configured destination. Files are created exclusively, never truncated.
    ///
    /// # Errors
    /// Returns filesystem errors, including an existing diagnostic file or symlink.
    pub fn new(config: &Config) -> io::Result<Self> {
        match &config.destination {
            Destination::Stderr => Ok(Self::with_writer(config, io::stderr())),
            Destination::File(path) => {
                let file = OpenOptions::new().write(true).create_new(true).open(path)?;
                Ok(Self::with_writer(config, file))
            }
        }
    }

    /// Build a subscriber around a caller-supplied owned sink instead of opening
    /// `config.destination`. No process state changes and no I/O happens here.
    pub fn with_writer(config: &Config, writer: impl Write + Send + 'static) -> Self {
        let level = config.level;
        let timings = config.timings;
        let sink = Sink::new(writer);
        let timing = formatter(config, sink.clone(), FmtSpan::CLOSE).with_filter(filter_fn(
            move |metadata| timings && metadata.is_span() && metadata.target() == "clis::timing",
        ));
        let events = formatter(config, sink.clone(), FmtSpan::NONE).with_filter(filter_fn(
            move |metadata| metadata.target() != "clis::timing" && *metadata.level() <= level,
        ));
        let subscriber = tracing_subscriber::registry()
            .with(timing)
            .with(events)
            // Gate callsites too: disabled stage fields must not be evaluated when
            // another active dispatch enables the same timing span.
            .with(filter_fn(move |metadata| {
                if metadata.target() == "clis::timing" {
                    timings && metadata.is_span()
                } else {
                    *metadata.level() <= level
                }
            }));
        Self {
            dispatch: Dispatch::new(subscriber),
            sink,
        }
    }

    /// Borrow the dispatch for a synchronous scope, or clone it for another thread.
    #[must_use]
    pub const fn dispatch(&self) -> &Dispatch {
        &self.dispatch
    }

    /// Flush the sink and report the first write/flush failure, including failures
    /// swallowed by a tracing callback. Repeated calls retain failure evidence.
    /// This does not deregister cloned dispatches or fsync files.
    ///
    /// # Errors
    /// Returns a sink write/flush failure or poisoned sink-lock error.
    pub fn finish(&self) -> io::Result<()> {
        self.sink.finish()
    }
}

fn formatter<S>(
    config: &Config,
    sink: Sink,
    span_events: FmtSpan,
) -> Box<dyn Layer<S> + Send + Sync>
where
    S: Subscriber + for<'lookup> LookupSpan<'lookup> + 'static,
{
    let formatter = tracing_subscriber::fmt::layer()
        .with_ansi(false)
        .with_span_events(span_events)
        .log_internal_errors(false)
        .with_writer(move || sink.clone());
    match config.format {
        Format::Text => formatter.boxed(),
        Format::Json => formatter.json().boxed(),
    }
}

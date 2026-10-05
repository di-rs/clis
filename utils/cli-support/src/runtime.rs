use crate::{Config, Destination, Format, sink::Sink, timing::Timings};
use std::{
    fs::OpenOptions,
    io::{self, Write},
};
use tracing::Dispatch;
use tracing_subscriber::{Layer, filter::filter_fn, layer::SubscriberExt};

/// A scoped subscriber and owned diagnostic sink, with explicit checked finishing.
///
/// No global logger/subscriber or panic hook is installed. Use `dispatch()` with
/// `tracing::dispatcher::with_default`; clone it explicitly for worker threads.
/// Close spans and stop emitting before calling `finish()`.
pub struct Runtime {
    dispatch: Dispatch,
    sink: Sink,
}

impl Runtime {
    /// Open the configured destination. Files are created exclusively, never truncated.
    ///
    /// # Errors
    /// Returns filesystem errors, including an existing diagnostic file or symlink.
    pub fn new(config: &Config) -> io::Result<Self> {
        let writer: Box<dyn Write + Send> = match &config.destination {
            Destination::Stderr => Box::new(io::stderr()),
            Destination::File(path) => {
                Box::new(OpenOptions::new().write(true).create_new(true).open(path)?)
            }
        };
        Ok(Self::with_writer(config, writer))
    }

    /// Build a subscriber around a caller-supplied owned sink instead of opening
    /// `config.destination`. No process state changes and no I/O happens here.
    pub fn with_writer(config: &Config, writer: impl Write + Send + 'static) -> Self {
        let level = config.level;
        let timings = config.timings;
        let sink = Sink::new(writer);
        let timing = Timings {
            sink: sink.clone(),
            format: config.format,
        }
        .with_filter(filter_fn(move |metadata| {
            timings && metadata.is_span() && metadata.target() == "clis::timing"
        }));
        let event_sink = sink.clone();
        let formatter = tracing_subscriber::fmt::layer()
            .with_ansi(false)
            .without_time()
            .log_internal_errors(false)
            .with_writer(move || event_sink.clone());
        let formatter = match config.format {
            Format::Text => formatter.boxed(),
            Format::Json => formatter.json().boxed(),
        }
        .with_filter(filter_fn(move |metadata| {
            metadata.target() != "clis::timing" && *metadata.level() <= level
        }));
        let subscriber = tracing_subscriber::registry()
            .with(timing)
            .with(formatter)
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

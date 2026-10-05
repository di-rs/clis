#![doc = include_str!("../README.md")]

//! Explicit diagnostics for CLI adapters, without process-global initialization.
//! Domain libraries should depend only on `tracing` and return typed errors.

mod config;
pub use config::{Config, ConfigError, Destination, Format, Overrides, parse_level};
mod session;
mod sink;
pub use session::TracingSession;

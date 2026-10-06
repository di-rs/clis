#![doc = include_str!("../README.md")]
#![forbid(unsafe_code)]

mod error;
mod model;
mod suite;

pub use error::{BenchError, ErrorKind};
pub use model::*;
pub use suite::{parse_suite, validate_suite};

//! Streaming synthetic data generators with typed options and caller-owned writers.
mod ending;
mod text;
pub use ending::LineEnding;
pub use text::{
    Alphabet, GenerationOptions, LengthUnit, TextOptions, gen_random_lines, generate, generate_text,
};

pub(crate) fn invalid(message: &str) -> anyhow::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidInput, message.to_owned()).into()
}

mod records;
pub use records::{RecordOptions, generate_records};
pub(crate) const CONTENT_LIMIT: usize = 8 * 1024 * 1024;

mod fields;
pub use fields::{FieldFormat, FieldOptions, generate_fields};

mod bytes;
pub use bytes::{ByteOptions, ByteSource, generate_bytes};

mod pair;
pub use pair::{PairOptions, generate_pair};

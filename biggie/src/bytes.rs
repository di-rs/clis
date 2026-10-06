use crate::{CONTENT_LIMIT, invalid};
use rand::{Rng, SeedableRng, rngs::StdRng};
use std::io::{self, Write};
/// Random bytes or a repeating literal pattern (1 byte through 8 MiB).
#[derive(Clone, Debug)]
pub enum ByteSource {
    Random { seed: Option<u64> },
    Pattern(Vec<u8>),
}
/// Exact byte budget; there are no implicit record terminators.
#[derive(Clone, Debug)]
pub struct ByteOptions {
    pub bytes: u64,
    pub source: ByteSource,
}
impl ByteOptions {
    /// Validate even when the byte budget is zero.
    /// # Errors
    /// Returns `InvalidInput` for an empty or oversized literal pattern.
    pub fn validate(&self) -> io::Result<()> {
        if let ByteSource::Pattern(pattern) = &self.source
            && (pattern.is_empty() || pattern.len() > CONTENT_LIMIT)
        {
            return Err(invalid("byte pattern must contain 1 byte through 8 MiB"));
        }
        Ok(())
    }
}
/// Generate arbitrary bytes with an 8 KiB scratch buffer and no writer flush.
/// The budget may end anywhere in a pattern, including inside CRLF or UTF-8.
/// ```
/// let mut output=Vec::new();
/// let options=biggie::ByteOptions{bytes:3,source:biggie::ByteSource::Pattern(vec![0,255])};
/// biggie::generate_bytes(&mut output,&options)?;
/// assert_eq!(output,[0,255,0]);
/// # Ok::<(), std::io::Error>(())
/// ```
/// # Errors
/// Invalid options fail before writing; write errors preserve partial output.
pub fn generate_bytes(mut writer: impl Write, options: &ByteOptions) -> io::Result<()> {
    options.validate()?;
    let mut rng = match options.source {
        ByteSource::Random { seed } => {
            Some(seed.map_or_else(|| StdRng::from_rng(&mut rand::rng()), StdRng::seed_from_u64))
        }
        ByteSource::Pattern(_) => None,
    };
    let span = tracing::debug_span!(
        "generate_bytes",
        requested_bytes = options.bytes,
        bytes_written = tracing::field::Empty
    );
    let _entered = span.enter();
    let mut buffer = [0_u8; 8192];
    let mut remaining = options.bytes;
    let mut position = 0_usize;
    while remaining > 0 {
        let count = remaining.min(8192);
        let bytes = buffer
            .get_mut(..usize::try_from(count).map_err(io::Error::other)?)
            .ok_or_else(|| io::Error::other("invalid byte buffer length"))?;
        match &options.source {
            ByteSource::Random { .. } => rng
                .as_mut()
                .ok_or_else(|| io::Error::other("missing RNG"))?
                .fill_bytes(bytes),
            ByteSource::Pattern(pattern) => {
                for byte in bytes.iter_mut() {
                    *byte = *pattern
                        .get(position)
                        .ok_or_else(|| io::Error::other("invalid pattern index"))?;
                    position = position.saturating_add(1);
                    if position == pattern.len() {
                        position = 0;
                    }
                }
            }
        }
        writer.write_all(bytes)?;
        remaining = remaining.saturating_sub(count);
    }
    span.record("bytes_written", options.bytes);
    Ok(())
}

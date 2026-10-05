use rand::distr::Alphanumeric;
use rand::{RngExt, SeedableRng, rngs::StdRng};
use std::{
    io::{self, Write},
    ops::RangeInclusive,
};

/// Bytes placed between records and, optionally, after the final record.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum LineEnding {
    #[default]
    Lf,
    CrLf,
}

impl LineEnding {
    const fn bytes(self) -> &'static [u8] {
        match self {
            Self::Lf => b"\n",
            Self::CrLf => b"\r\n",
        }
    }
}

/// Settings for ASCII alphanumeric records separated by line endings.
#[derive(Clone, Debug)]
pub struct GenerationOptions {
    /// Number of records; zero produces no bytes.
    pub lines: u64,
    /// Inclusive number of space-separated words per record; zero is a blank record.
    pub words_per_line: RangeInclusive<u32>,
    /// Inclusive ASCII byte length of each word; the minimum must be positive.
    pub word_length: RangeInclusive<u32>,
    /// Repeats output with the same build and options; not stable across versions/platforms.
    pub seed: Option<u64>,
    pub line_ending: LineEnding,
    /// Whether to terminate the last record (has no effect when `lines` is zero).
    pub final_newline: bool,
}

impl Default for GenerationOptions {
    fn default() -> Self {
        Self {
            lines: 100_000,
            words_per_line: 7..=14,
            word_length: 2..=11,
            seed: None,
            line_ending: LineEnding::Lf,
            final_newline: true,
        }
    }
}

impl GenerationOptions {
    /// Check ranges without writing output or drawing randomness.
    ///
    /// # Errors
    /// Returns [`io::ErrorKind::InvalidInput`] for reversed ranges or zero word length.
    pub fn validate(&self) -> io::Result<()> {
        if self.words_per_line.is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "words per line range must be ascending",
            ));
        }
        if self.word_length.is_empty() || *self.word_length.start() == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "word length range must be positive and ascending",
            ));
        }
        Ok(())
    }
}

/// Write random alphanumeric lines to the supplied writer without flushing it.
/// Zero lines is valid for direct callers. No CLI or subscriber setup is needed.
///
/// ```
/// let mut output = Vec::new();
/// biggie::gen_random_lines(&mut output, 2)?;
/// assert_eq!(output.iter().filter(|byte| **byte == b'\n').count(), 2);
/// # Ok::<(), std::io::Error>(())
/// ```
///
/// # Errors
/// Returns a write error; previously written bytes remain in the supplied writer.
pub fn gen_random_lines(writer: impl Write, num_lines: u64) -> io::Result<()> {
    generate(
        writer,
        &GenerationOptions {
            lines: num_lines,
            ..GenerationOptions::default()
        },
    )
}

/// Generate text using one RNG initialized here, then borrowed by internal helpers.
///
/// The same seed, options, and build reproduce the same bytes. `StdRng` and its
/// distributions may change across dependency versions or platforms: retain the
/// revision, lockfile, options, and output checksum with benchmark datasets.
/// With no seed, initialize the RNG from system-seeded thread randomness.
///
/// Writes incrementally using a fixed-size word buffer, without flushing the writer.
/// A blank final record without a terminator adds no bytes.
///
/// ```
/// use biggie::{GenerationOptions, generate};
/// let options = GenerationOptions { lines: 2, seed: Some(42), ..Default::default() };
/// let mut first = Vec::new();
/// let mut second = Vec::new();
/// generate(&mut first, &options)?;
/// generate(&mut second, &options)?;
/// assert_eq!(first, second);
/// # Ok::<(), std::io::Error>(())
/// ```
///
/// # Errors
/// Invalid ranges return [`io::ErrorKind::InvalidInput`] before writing anything,
/// including for zero records. Write errors preserve bytes already written.
pub fn generate(mut writer: impl Write, options: &GenerationOptions) -> io::Result<()> {
    options.validate()?;
    let mut rng = options
        .seed
        .map_or_else(|| StdRng::from_rng(&mut rand::rng()), StdRng::seed_from_u64);
    let num_lines = options.lines;
    let stage = tracing::debug_span!(
        "generate",
        requested_lines = num_lines,
        lines_written = tracing::field::Empty
    );
    let _entered = stage.enter();
    log::debug!("generating random text: {num_lines} requested lines");
    let mut word_buffer = [0_u8; 8192];
    for line in 0..num_lines {
        let num_words = rng.random_range(options.words_per_line.clone());
        for word in 0..num_words {
            if word != 0 {
                writer.write_all(b" ")?;
            }
            let length = rng.random_range(options.word_length.clone());
            write_random_word(&mut writer, &mut rng, length, &mut word_buffer)?;
        }
        if options.final_newline || line < num_lines.saturating_sub(1) {
            writer.write_all(options.line_ending.bytes())?;
        }
    }
    // Record on this span, not a caller's parent when this stage is filtered out.
    stage.record("lines_written", num_lines);
    log::trace!("generation complete: {num_lines} lines");
    Ok(())
}

fn write_random_word(
    writer: &mut impl Write,
    rng: &mut impl RngExt,
    mut remaining: u32,
    buffer: &mut [u8; 8192],
) -> io::Result<()> {
    while remaining > 0 {
        let length = remaining.min(8192);
        let bytes = buffer
            .get_mut(..usize::try_from(length).map_err(io::Error::other)?)
            .ok_or_else(|| io::Error::other("word chunk exceeds buffer"))?;
        for byte in bytes.iter_mut() {
            *byte = rng.sample(Alphanumeric);
        }
        writer.write_all(bytes)?;
        remaining = remaining.saturating_sub(length);
    }
    Ok(())
}

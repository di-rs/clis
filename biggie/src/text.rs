use anyhow::{Context, Result};
use rand::distr::Alphanumeric;
use rand::{RngExt, SeedableRng, rngs::StdRng};
use std::{
    io::{self, Write},
    ops::RangeInclusive,
};

use crate::LineEnding;

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
pub fn generate(writer: impl Write, options: &GenerationOptions) -> io::Result<()> {
    options.validate()?;
    generate_words(writer, options, b" ", write_random_word)
}

/// Generate text with explicit alphabets and scalar/byte length semantics.
/// Retains an 8 KiB word buffer and at most 4096 custom alphabet scalars.
/// Does not flush the caller's writer or normalize Unicode.
///
/// ```
/// use biggie::{Alphabet, GenerationOptions, LengthUnit, TextOptions, generate_text};
/// let options = TextOptions {
///     generation: GenerationOptions { lines: 2, seed: Some(42), ..Default::default() },
///     alphabet: Alphabet::Custom("ACGT".chars().collect()),
///     length_unit: LengthUnit::Bytes,
///     ..Default::default()
/// };
/// let mut data = Vec::new();
/// generate_text(&mut data, &options)?;
/// # Ok::<(), anyhow::Error>(())
/// ```
/// # Errors
/// Invalid options fail before writing; I/O errors preserve already emitted bytes.
pub fn generate_text(writer: impl Write, text: &TextOptions) -> Result<()> {
    text.validate()?;
    let alphabet = match &text.alphabet {
        Alphabet::Ascii => None,
        Alphabet::Unicode => Some(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789éβ猫界\u{301}🙂\u{200d}"
                .chars()
                .collect::<Vec<_>>(),
        ),
        Alphabet::Custom(chars) => Some(chars.clone()),
    };
    let result = if let Some(chars) = alphabet {
        generate_words(
            writer,
            &text.generation,
            text.separator.as_bytes(),
            |writer, rng, length, buffer| write_scalar_word(writer, rng, length, &chars, buffer),
        )
    } else if text.separator == " " {
        generate_words(writer, &text.generation, b" ", write_random_word)
    } else {
        generate_words(
            writer,
            &text.generation,
            text.separator.as_bytes(),
            write_random_word,
        )
    };
    result.context("Cannot write generated text")
}

// Select the word generator once, outside the hot record/word loops. The default
// space separator also stays a fixed-size array so small writes can inline.
fn generate_words<W: Write>(
    mut writer: W,
    options: &GenerationOptions,
    separator: impl AsRef<[u8]>,
    mut word: impl FnMut(&mut W, &mut StdRng, u32, &mut [u8; 8192]) -> io::Result<()>,
) -> io::Result<()> {
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
        for index in 0..num_words {
            if index != 0 {
                writer.write_all(separator.as_ref())?;
            }
            let length = rng.random_range(options.word_length.clone());
            word(&mut writer, &mut rng, length, &mut word_buffer)?;
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

/// Scalar set for generated words. Custom sets must contain distinct scalars.
#[derive(Clone, Debug, Default)]
pub enum Alphabet {
    #[default]
    Ascii,
    Unicode,
    Custom(Vec<char>),
}

/// Length unit; non-ASCII alphabets require scalar counts.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum LengthUnit {
    #[default]
    Bytes,
    Scalars,
}

/// Text operation options; legacy defaults remain ASCII words separated by spaces.
#[derive(Clone, Debug)]
pub struct TextOptions {
    pub generation: GenerationOptions,
    pub alphabet: Alphabet,
    pub length_unit: LengthUnit,
    pub separator: String,
}

impl Default for TextOptions {
    fn default() -> Self {
        Self {
            generation: GenerationOptions::default(),
            alphabet: Alphabet::Ascii,
            length_unit: LengthUnit::Bytes,
            separator: " ".into(),
        }
    }
}

impl TextOptions {
    /// Validate without drawing randomness or writing output.
    /// # Errors
    /// Returns `InvalidInput` for malformed alphabets, separators or length settings.
    pub fn validate(&self) -> Result<()> {
        self.generation.validate()?;
        if self.separator.contains(['\r', '\n']) {
            return Err(crate::invalid("word separator cannot contain CR or LF"));
        }
        let non_ascii = match &self.alphabet {
            Alphabet::Ascii => false,
            Alphabet::Unicode => true,
            Alphabet::Custom(chars) => {
                if chars.is_empty() || chars.len() > 4096 {
                    return Err(crate::invalid("custom alphabet must have 1..=4096 scalars"));
                }
                let mut seen = std::collections::HashSet::new();
                for ch in chars {
                    if matches!(ch, '\r' | '\n') || !seen.insert(ch) {
                        return Err(crate::invalid(
                            "alphabet cannot contain CR, LF or duplicate scalars",
                        ));
                    }
                }
                chars.iter().any(|ch| !ch.is_ascii())
            }
        };
        if non_ascii && self.length_unit == LengthUnit::Bytes {
            return Err(crate::invalid(
                "non-ASCII alphabets require --length-unit scalars",
            ));
        }
        Ok(())
    }
}

fn write_scalar_word(
    writer: &mut impl Write,
    rng: &mut impl RngExt,
    length: u32,
    chars: &[char],
    buffer: &mut [u8; 8192],
) -> io::Result<()> {
    let mut used = 0_usize;
    for _ in 0..length {
        let ch = chars
            .get(rng.random_range(0..chars.len()))
            .ok_or_else(|| io::Error::other("validated alphabet is empty"))?;
        let mut encoded = [0_u8; 4];
        let bytes = ch.encode_utf8(&mut encoded).as_bytes();
        if buffer.len().saturating_sub(used) < bytes.len() {
            writer.write_all(
                buffer
                    .get(..used)
                    .ok_or_else(|| io::Error::other("invalid buffer length"))?,
            )?;
            used = 0;
        }
        let end = used.saturating_add(bytes.len());
        buffer
            .get_mut(used..end)
            .ok_or_else(|| io::Error::other("invalid buffer range"))?
            .copy_from_slice(bytes);
        used = end;
    }
    writer.write_all(
        buffer
            .get(..used)
            .ok_or_else(|| io::Error::other("invalid buffer length"))?,
    )
}

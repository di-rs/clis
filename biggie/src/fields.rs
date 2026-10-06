use crate::{CONTENT_LIMIT, LineEnding, invalid};
use rand::{RngExt, SeedableRng, distr::Alphanumeric, rngs::StdRng};
use std::{
    io::{self, Write},
    ops::RangeInclusive,
};

/// Plain separators versus CSV quoting/escaping, without headers.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub enum FieldFormat {
    #[default]
    Delimited,
    /// CSV quoting and escaping; requires the `csv` Cargo feature.
    #[cfg(feature = "csv")]
    Csv,
}
impl FieldFormat {
    const fn is_csv(self) -> bool {
        match self {
            Self::Delimited => false,
            #[cfg(feature = "csv")]
            Self::Csv => true,
        }
    }
}
/// Field generator configuration. CSV line counts refer to logical records.
#[derive(Clone, Debug)]
pub struct FieldOptions {
    pub lines: u64,
    pub fields: u32,
    pub format: FieldFormat,
    pub delimiter: u8,
    pub values: Vec<String>,
    pub empty_every: Option<u64>,
    pub seed: Option<u64>,
    pub word_length: RangeInclusive<u32>,
    pub line_ending: LineEnding,
    pub final_newline: bool,
}
impl Default for FieldOptions {
    fn default() -> Self {
        Self {
            lines: 100_000,
            fields: 3,
            format: FieldFormat::Delimited,
            delimiter: b'\t',
            values: Vec::new(),
            empty_every: None,
            seed: None,
            word_length: 2..=11,
            line_ending: LineEnding::Lf,
            final_newline: true,
        }
    }
}
impl FieldOptions {
    /// Validate content and a conservative maximum encoded row size of 8 MiB.
    /// # Errors
    /// Returns `InvalidInput` for invalid shapes, overflow, delimiters or cell content.
    pub fn validate(&self) -> io::Result<()> {
        if self.fields == 0
            || self.empty_every == Some(0)
            || self.word_length.is_empty()
            || *self.word_length.start() == 0
        {
            return Err(invalid(
                "fields, word lengths and empty-every must be positive",
            ));
        }
        if !self.delimiter.is_ascii() || matches!(self.delimiter, 0 | b'\r' | b'\n' | b'"') {
            return Err(invalid(
                "delimiter must be one ASCII byte other than NUL, CR, LF or quote",
            ));
        }
        self.lines
            .checked_mul(u64::from(self.fields))
            .ok_or_else(|| invalid("cell count overflows u64"))?;
        if !self.values.is_empty() && self.seed.is_some() {
            return Err(invalid("literal field values cannot use a seed"));
        }
        let max_cell = if self.values.is_empty() {
            let length = u64::from(*self.word_length.end());
            if self.format.is_csv() && self.delimiter.is_ascii_alphanumeric() {
                length.saturating_add(2)
            } else {
                length
            }
        } else {
            let mut max_cell = 0_u64;
            let mut total = 0_usize;
            for value in &self.values {
                total = total
                    .checked_add(value.len())
                    .filter(|n| *n <= CONTENT_LIMIT)
                    .ok_or_else(|| invalid("field values exceed 8 MiB"))?;
                if self.format == FieldFormat::Delimited
                    && value
                        .bytes()
                        .any(|b| b == self.delimiter || matches!(b, b'\r' | b'\n'))
                {
                    return Err(invalid(
                        "plain fields cannot contain the delimiter, CR or LF",
                    ));
                }
                let length = u64::try_from(value.len()).map_err(io::Error::other)?;
                let encoded = if self.format.is_csv() {
                    length.saturating_mul(2).saturating_add(2)
                } else {
                    length
                };
                max_cell = max_cell.max(encoded);
            }
            max_cell
        };
        let row_size = max_cell
            .checked_add(1)
            .and_then(|n| n.checked_mul(u64::from(self.fields)))
            .and_then(|n| n.checked_add(2))
            .ok_or_else(|| invalid("row size overflows u64"))?;
        if row_size > u64::try_from(CONTENT_LIMIT).map_err(io::Error::other)? {
            return Err(invalid("maximum encoded row exceeds 8 MiB"));
        }
        Ok(())
    }
}

/// Generate rows with fixed field counts and optional CSV encoding.
/// Retains at most one bounded encoded row and one cell; never flushes the caller.
/// ```
/// let mut output=Vec::new();
/// let options=biggie::FieldOptions{lines:1,values:vec!["a".into(),"".into(),"c".into()],..Default::default()};
/// biggie::generate_fields(&mut output,&options)?;
/// assert_eq!(output,b"a\t\tc\n");
/// # Ok::<(), std::io::Error>(())
/// ```
/// # Errors
/// Invalid options fail before writing; write errors preserve partial output.
pub fn generate_fields(mut writer: impl Write, options: &FieldOptions) -> io::Result<()> {
    options.validate()?;
    let mut rng = options
        .seed
        .map_or_else(|| StdRng::from_rng(&mut rand::rng()), StdRng::seed_from_u64);
    let span = tracing::debug_span!(
        "generate_fields",
        requested_records = options.lines,
        records_written = tracing::field::Empty
    );
    let _entered = span.enter();
    let mut cell = Vec::new();
    let mut row = Vec::new();
    let mut index = 0_u64;
    for line in 0..options.lines {
        row.clear();
        match options.format {
            #[cfg(feature = "csv")]
            FieldFormat::Csv => {
                let mut encoder = csv::WriterBuilder::new()
                    .delimiter(options.delimiter)
                    .terminator(match options.line_ending {
                        LineEnding::Lf => csv::Terminator::Any(b'\n'),
                        LineEnding::CrLf => csv::Terminator::CRLF,
                    })
                    .from_writer(&mut row);
                for _ in 0..options.fields {
                    encoder
                        .write_field(cell_value(options, &mut rng, index, &mut cell)?)
                        .map_err(io::Error::from)?;
                    index = index.saturating_add(1);
                }
                encoder
                    .write_record(std::iter::empty::<&[u8]>())
                    .map_err(io::Error::from)?;
                encoder.flush()?; // Flushes only our row Vec, never the caller's writer.
            }
            FieldFormat::Delimited => {
                for field in 0..options.fields {
                    if field > 0 {
                        row.push(options.delimiter);
                    }
                    row.extend_from_slice(cell_value(options, &mut rng, index, &mut cell)?);
                    index = index.saturating_add(1);
                }
                row.extend_from_slice(options.line_ending.bytes());
            }
        }
        let bytes = if !options.final_newline && line == options.lines.saturating_sub(1) {
            row.strip_suffix(options.line_ending.bytes())
                .ok_or_else(|| io::Error::other("missing encoded terminator"))?
        } else {
            &row
        };
        writer.write_all(bytes)?;
    }
    span.record("records_written", options.lines);
    Ok(())
}
fn cell_value<'a>(
    options: &'a FieldOptions,
    rng: &mut StdRng,
    index: u64,
    scratch: &'a mut Vec<u8>,
) -> io::Result<&'a [u8]> {
    if options
        .empty_every
        .is_some_and(|n| index.saturating_add(1).is_multiple_of(n))
    {
        return Ok(b"");
    }
    if options.values.is_empty() {
        let length = rng.random_range(options.word_length.clone());
        scratch.clear();
        let count = usize::try_from(length).map_err(io::Error::other)?;
        scratch.extend(
            std::iter::repeat_with(|| rng.sample(Alphanumeric))
                .filter(|byte| options.format.is_csv() || *byte != options.delimiter)
                .take(count),
        );
        Ok(scratch)
    } else {
        let count = u64::try_from(options.values.len()).map_err(io::Error::other)?;
        let position = usize::try_from(
            index
                .checked_rem(count)
                .ok_or_else(|| io::Error::other("empty field corpus"))?,
        )
        .map_err(io::Error::other)?;
        options
            .values
            .get(position)
            .map(String::as_bytes)
            .ok_or_else(|| io::Error::other("invalid field index"))
    }
}

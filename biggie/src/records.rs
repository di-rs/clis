use crate::{LineEnding, invalid};
use anyhow::{Context, Result};
use std::io::Write;

/// Literal record schedule, bounded to 100,000 entries and 8 MiB of content.
/// Adjacent equal records (including across cycles) form one longer observed run.
#[derive(Clone, Debug)]
pub struct RecordOptions {
    pub records: Vec<String>,
    pub repeat: u64,
    pub cycles: u64,
    pub line_ending: LineEnding,
    pub final_newline: bool,
}
impl Default for RecordOptions {
    fn default() -> Self {
        Self {
            records: Vec::new(),
            repeat: 1,
            cycles: 1,
            line_ending: LineEnding::Lf,
            final_newline: true,
        }
    }
}
impl RecordOptions {
    /// Validate counts and content before writing.
    /// # Errors
    /// Returns `InvalidInput` for overflow, resource limits, zero repeats or embedded LF.
    pub fn validate(&self) -> Result<()> {
        if self.repeat == 0 {
            return Err(invalid("repeat must be positive"));
        }
        if self.records.len() > 100_000 {
            return Err(invalid("corpus exceeds 100000 records"));
        }
        self.records.iter().try_fold(0_usize, |size, record| {
            if record.contains('\n') {
                return Err(invalid("literal records cannot contain LF"));
            }
            size.checked_add(record.len())
                .filter(|n| *n <= crate::CONTENT_LIMIT)
                .ok_or_else(|| invalid("corpus exceeds 8 MiB"))
        })?;
        self.total_records()?;
        Ok(())
    }
    /// Count expanded records without expanding the schedule.
    /// # Errors
    /// Returns `InvalidInput` on u64 overflow.
    pub fn total_records(&self) -> Result<u64> {
        u64::try_from(self.records.len())
            .ok()
            .and_then(|n| n.checked_mul(self.repeat))
            .and_then(|n| n.checked_mul(self.cycles))
            .ok_or_else(|| invalid("record count overflows u64"))
    }
}

/// Stream a literal schedule without normalization, RNG or writer flushing.
/// Retains no expanded output. The caller owns the bounded corpus.
/// ```
/// let options=biggie::RecordOptions{records:vec!["hit".into()],repeat:2,..Default::default()};
/// let mut output=Vec::new();
/// biggie::generate_records(&mut output,&options)?;
/// assert_eq!(output,b"hit\nhit\n");
/// # Ok::<(), anyhow::Error>(())
/// ```
/// # Errors
/// Invalid options fail before writes; I/O errors preserve partial output.
pub fn generate_records(mut writer: impl Write, options: &RecordOptions) -> Result<()> {
    options.validate()?;
    let total = options.total_records()?;
    let span = tracing::debug_span!(
        "generate_records",
        requested_records = total,
        records_written = tracing::field::Empty
    );
    let _entered = span.enter();
    let mut remaining = total;
    if total > 0 {
        for _ in 0..options.cycles {
            for record in &options.records {
                for _ in 0..options.repeat {
                    writer
                        .write_all(record.as_bytes())
                        .context("Cannot write literal record")?;
                    remaining = remaining.saturating_sub(1);
                    if options.final_newline || remaining > 0 {
                        writer.write_all(options.line_ending.bytes())?;
                    }
                }
            }
        }
    }
    span.record("records_written", total);
    Ok(())
}

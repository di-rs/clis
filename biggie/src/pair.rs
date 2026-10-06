use crate::{LineEnding, invalid};
use anyhow::{Context, Result};
use rand::{RngExt, SeedableRng, rngs::StdRng};
use std::io::Write;
/// Counts of distinct sorted keys; copies supplies each key's multiplicity.
#[derive(Clone, Debug)]
pub struct PairOptions {
    pub left_only: u64,
    pub shared: u64,
    pub right_only: u64,
    pub copies: u64,
    pub seed: Option<u64>,
    pub line_ending: LineEnding,
    pub final_newline: bool,
}
impl Default for PairOptions {
    fn default() -> Self {
        Self {
            left_only: 0,
            shared: 0,
            right_only: 0,
            copies: 1,
            seed: None,
            line_ending: LineEnding::Lf,
            final_newline: true,
        }
    }
}
impl PairOptions {
    /// Check union and per-stream record counts before writing.
    /// # Errors
    /// Returns `InvalidInput` on overflow or zero copies.
    pub fn validate(&self) -> Result<()> {
        if self.copies == 0 {
            return Err(invalid("copies must be positive"));
        }
        self.union_count()?;
        self.record_counts()?;
        Ok(())
    }
    /// Expanded (left, right) record counts.
    /// # Errors
    /// Returns `InvalidInput` if either count overflows u64.
    pub fn record_counts(&self) -> Result<(u64, u64)> {
        let count = |unique: u64| {
            unique
                .checked_add(self.shared)
                .and_then(|n| n.checked_mul(self.copies))
                .ok_or_else(|| invalid("pair record count overflows u64"))
        };
        Ok((count(self.left_only)?, count(self.right_only)?))
    }
    fn union_count(&self) -> Result<u64> {
        self.left_only
            .checked_add(self.shared)
            .and_then(|n| n.checked_add(self.right_only))
            .ok_or_else(|| invalid("pair union count overflows u64"))
    }
}
/// Generate two byte-sorted streams with exact multiset overlap in constant memory.
///
/// Keys use fixed-width lowercase hex ordinals, independent of locale. Neither
/// writer is flushed. On failure either output may already contain partial data.
/// ```
/// let mut left=Vec::new(); let mut right=Vec::new();
/// biggie::generate_pair(&mut left,&mut right,&biggie::PairOptions{shared:2,..Default::default()})?;
/// assert_eq!(left,right);
/// # Ok::<(),anyhow::Error>(())
/// ```
/// # Errors
/// Invalid counts fail before writes; I/O errors stop generation immediately.
pub fn generate_pair(
    mut left: impl Write,
    mut right: impl Write,
    options: &PairOptions,
) -> Result<()> {
    options.validate()?;
    let (left_count, right_count) = options.record_counts()?;
    let span = tracing::debug_span!(
        "generate_pair",
        requested_left = left_count,
        requested_right = right_count,
        left_written = tracing::field::Empty,
        right_written = tracing::field::Empty
    );
    let _entered = span.enter();
    let prefix = options.seed.map(|seed| {
        let mut rng = StdRng::seed_from_u64(seed);
        format!("{:016x}", rng.random::<u64>())
    });
    let left_end = options.left_only.saturating_add(options.shared);
    let mut left_remaining = left_count;
    let mut right_remaining = right_count;
    for ordinal in 0..options.union_count()? {
        for _ in 0..options.copies {
            if ordinal < left_end {
                left_remaining = left_remaining.saturating_sub(1);
                write_key(
                    &mut left,
                    prefix.as_deref(),
                    ordinal,
                    left_remaining,
                    options,
                )?;
            }
            if ordinal >= options.left_only {
                right_remaining = right_remaining.saturating_sub(1);
                write_key(
                    &mut right,
                    prefix.as_deref(),
                    ordinal,
                    right_remaining,
                    options,
                )?;
            }
        }
    }
    span.record("left_written", left_count);
    span.record("right_written", right_count);
    Ok(())
}
fn write_key(
    writer: &mut impl Write,
    prefix: Option<&str>,
    ordinal: u64,
    remaining: u64,
    options: &PairOptions,
) -> Result<()> {
    if let Some(prefix) = prefix {
        write!(writer, "{prefix}-").context("Cannot write key prefix")?;
    }
    write!(writer, "{ordinal:016x}").context("Cannot write key ordinal")?;
    if options.final_newline || remaining > 0 {
        writer.write_all(options.line_ending.bytes())?;
    }
    Ok(())
}

use rand::RngExt;
use rand::distr::Alphanumeric;
use std::io::Write;

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
pub fn gen_random_lines(mut writer: impl Write, num_lines: u64) -> Result<(), std::io::Error> {
    let stage = tracing::info_span!(target: "clis::timing", "generate",
        requested_lines = num_lines, lines_written = tracing::field::Empty);
    let _entered = stage.enter();
    tracing::debug!(requested_lines = num_lines, "generating random text");
    for _ in 0..num_lines {
        let num_words = rand::random_range(7..15);
        let mut words = vec![];
        for _ in 0..num_words {
            words.push(random_string());
        }
        writeln!(writer, "{}", words.join(" "))?;
    }
    stage.record("lines_written", num_lines);
    tracing::trace!(lines_written = num_lines, "generation complete");
    Ok(())
}

fn random_string() -> String {
    let length = rand::random_range(2..12);
    rand::rng()
        .sample_iter(&Alphanumeric)
        .take(length)
        .map(char::from)
        .collect()
}

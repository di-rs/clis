use anyhow::{Context, Result};
use log::warn;

/// # Errors
/// Throws error when couldn't read the line or writing to the writer fails
pub fn find_matches(
    pattern: &str,
    reader: impl std::io::BufRead,
    mut writer: impl std::io::Write,
) -> Result<()> {
    for line in reader.lines() {
        let line = line.context("could not read line in file")?;

        if line.contains(pattern) {
            warn!("found line with pattern: {pattern} - {line}");
            writeln!(writer, "{line}").context("could not write to stdout")?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn find_a_match() {
        let mut result = Vec::new();
        let reader = b"lorem ipsum\ndelor sit amet";

        let _ = find_matches("lorem", &reader[..], &mut result);

        assert_eq!(result, b"lorem ipsum\n");
    }
}

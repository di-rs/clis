use log::warn;
use regex::Regex;

/// # Errors
/// Throws error when couldn't read the line or writing to the writer fails
pub fn find_matches(
    pattern: &Regex,
    reader: impl std::io::BufRead,
    mut writer: impl std::io::Write,
) -> Result<(), std::io::Error> {
    for line in reader.lines() {
        let line = line?;
        if line.contains(pattern.as_str()) {
            warn!("found line with pattern: {pattern} - {line}");
            writeln!(writer, "{line}")?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::error::Error;

    #[test]
    #[allow(clippy::panic_in_result_fn)]
    fn find_a_match() -> Result<(), Box<dyn Error>> {
        let mut result = Vec::new();
        let reader = b"lorem ipsum\ndelor sit amet";
        let pattern = Regex::new("lo*rem")?;

        find_matches(&pattern, &reader[..], &mut result)?;

        assert_eq!(result, b"lorem ipsum\n");
        Ok(())
    }
}

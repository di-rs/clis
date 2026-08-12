use std::io::{BufRead, Write};

/// # Errors
/// Throws error if unable to write into writer
pub fn report_unique_lines(
    mut reader: impl BufRead,
    mut writer: impl Write,
    line_count: bool,
) -> Result<(), std::io::Error> {
    let mut line = String::new();
    let mut prev_line = String::new();
    let mut count: usize = 0;

    while let bytes = reader.read_line(&mut line)?
        && bytes != 0
    {
        count = count.saturating_add(1);

        if line == prev_line {
            line.clear();
            continue;
        }

        if line_count {
            write!(writer, "{count:>4} {line}")?;
        } else {
            write!(writer, "{line}")?;
        }

        std::mem::swap(&mut line, &mut prev_line);
        count = 0;
        line.clear();
    }

    Ok(())
}

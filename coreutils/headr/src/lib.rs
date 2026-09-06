use std::{
    io::{BufRead, Write},
    path::Path,
};

/// # Errors
/// Throws error if unable to decode line in the reader or write to writer is failed
pub fn write_lines(
    mut reader: impl BufRead,
    mut writer: impl Write,
    lines: u64,
) -> Result<(), std::io::Error> {
    let mut line = String::new();

    for _ in 0..lines {
        let bytes = reader.read_line(&mut line)?;
        if bytes == 0 {
            break;
        }
        write!(writer, "{line}")?;
        line.clear();
    }

    Ok(())
}

/// # Errors
/// Throws error if unable to decode byte in the reader or write to writer is failed
pub fn write_bytes(
    reader: impl BufRead,
    mut writer: impl Write,
    bytes: u64,
) -> Result<(), std::io::Error> {
    let bytes_number = usize::try_from(bytes).unwrap_or(usize::MAX);
    let bytes = reader
        .bytes()
        .take(bytes_number)
        .collect::<Result<Vec<_>, _>>();
    write!(writer, "{}", String::from_utf8_lossy(&bytes?))?;
    Ok(())
}

/// # Errors
/// Throws error if write to writer is failed
pub fn write_file_header(mut writer: impl Write, filename: &Path) -> Result<(), std::io::Error> {
    let header = format!("==> {} <==", filename.display());
    writeln!(writer, "{header}")?;
    Ok(())
}

//! Crate to add prefix to the lines when it used with other writers.
//! A small `std::io::Write` adapter that prefixes non-empty lines.

use std::io::Write;

/// Writer wrapper struct
/// Scans the buffer and add prefix at the start of each line
/// Empty lines will be skiped
pub struct PrefixWriter<W> {
    writer: W,
    prefix: Vec<u8>,
    reminder: Vec<u8>,
}

impl<W: Write> PrefixWriter<W> {
    pub fn new<P: Into<Vec<u8>>>(writer: W, prefix: P) -> Self {
        Self {
            writer,
            prefix: prefix.into(),
            reminder: Vec::new(),
        }
    }

    /// Will skip empty lines and treat CRLF like a normal newline
    fn write_line(&mut self, line: &[u8]) -> Result<(), std::io::Error> {
        let content = line.strip_prefix(b"\r").unwrap_or(line);

        if !content.is_empty() {
            self.writer.write_all(&self.prefix)?;
        }

        self.writer.write_all(line)?;
        self.writer.write_all(b"\n")?;

        Ok(())
    }
}

impl<W: Write> Write for PrefixWriter<W> {
    fn write(&mut self, mut buf: &[u8]) -> std::io::Result<usize> {
        let original_len = buf.len();

        // If we have unfinished line, trying to complete it
        if !self.reminder.is_empty() {
            if let Some(pos) = buf.iter().position(|&byte| byte == b'\n') {
                #[allow(clippy::indexing_slicing)]
                self.reminder.extend_from_slice(&buf[..pos]);

                let line = std::mem::take(&mut self.reminder);
                self.write_line(&line)?;

                let next_pos = pos.saturating_add(1);
                buf = buf.get(next_pos..).unwrap_or_default();
            } else {
                self.reminder.extend_from_slice(buf);
                return Ok(original_len);
            }
        }

        // Everything up to the newline can be written
        while let Some(pos) = buf.iter().position(|&byte| byte == b'\n') {
            #[allow(clippy::indexing_slicing)]
            self.write_line(&buf[..pos])?;

            let next_pos = pos.saturating_add(1);
            buf = buf.get(next_pos..).unwrap_or_default();
        }

        // Everything after the newline should be kept as reminder
        if !buf.is_empty() {
            self.reminder.extend_from_slice(buf);
        }

        Ok(original_len)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        if !self.reminder.is_empty() {
            self.writer.write_all(&self.prefix)?;
            self.writer.write_all(&self.reminder)?;
            self.reminder.clear();
        }
        self.writer.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use pretty_assertions::assert_eq;

    const PREFIX: &str = "prefix: ";

    #[allow(clippy::unwrap_used)]
    fn run(input: &str, expected: &str) {
        let mut res = Vec::new();
        let mut prefix_writer = PrefixWriter::new(&mut res, PREFIX);

        prefix_writer.write_all(input.as_bytes()).unwrap();
        prefix_writer.flush().unwrap();

        let res_str = String::from_utf8_lossy(&res);

        assert_eq!(expected, res_str);
    }

    #[test]
    fn empty_string() {
        let input = "";
        let expected = input;
        run(input, expected);
    }

    #[test]
    fn empty_lines() {
        let input = "\n\n\n\n";
        let expected = input;
        run(input, expected);
    }

    #[test]
    fn one_line() {
        let input = "Lorem";
        let expected = [PREFIX, input].join("");
        run(input, &expected);
    }

    #[test]
    fn two_lines() {
        let input = "Lorem\nItsmut\n";
        let expected = [PREFIX, "Lorem\n", PREFIX, "Itsmut\n"].join("");
        run(input, &expected);
    }

    #[test]
    fn two_lines_remainder() {
        let input = "Lorem\nItsmut";
        let expected = [PREFIX, "Lorem\n", PREFIX, "Itsmut"].join("");
        run(input, &expected);
    }

    #[test]
    fn two_lines_rn() {
        let input = "Lorem\r\nItsmut\r\n";
        let expected = [PREFIX, "Lorem\r\n", PREFIX, "Itsmut\r\n"].join("");
        run(input, &expected);
    }

    #[test]
    fn two_lines_with_empty() {
        let input = "Lorem\n\n\n\n\nItsmut\n";
        let expected = [PREFIX, "Lorem\n\n\n\n\n", PREFIX, "Itsmut\n"].join("");
        run(input, &expected);
    }
}

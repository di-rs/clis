use std::io::{BufRead, Write};

pub struct Flags {
    pub number_lines: bool,
    pub number_nonblank_lines: bool
}

/// # Errors
/// Throws error if unable to decode line in the reader or write to writer is failed
pub fn write_lines(reader: impl BufRead, mut writer: impl Write, args: &Flags) -> Result<(), std::io::Error> {
    let mut num = 0_usize;

    macro_rules! write_numbered {
        ($line:expr) => {{
            num = num.saturating_add(1);
            writeln!(writer, "{num:>6}\t{}", $line)?;
        }};
    }
    
    for line in reader.lines() {
        let line = line?;
        if args.number_lines {
            write_numbered!(line);
        } else if args.number_nonblank_lines {
            if line.is_empty() {
                writeln!(writer)?;
            } else {
                write_numbered!(line);
            }
        } else {
            writeln!(writer, "{line}")?;
        }
    }
    Ok(())
}
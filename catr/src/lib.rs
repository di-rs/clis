use std::io::{BufRead, Write};

pub struct Flags {
    pub number_lines: bool,
    pub number_nonblank_lines: bool,
    pub squeeze_blank: bool,
}

/// # Errors
/// Throws error if unable to decode line in the reader or write to writer is failed
pub fn write_lines(
    reader: impl BufRead,
    mut writer: impl Write,
    args: &Flags,
) -> Result<(), std::io::Error> {
    let mut num = 0_usize;
    let mut prev_empty = false;

    for line in reader.lines() {
        let line = line?;

        if args.squeeze_blank {
            let cur_empty = line.is_empty();
            if cur_empty && prev_empty {
                continue;
            }
            prev_empty = cur_empty;
        }

        if args.number_lines {
            write_numbered(&mut writer, &mut num, &line)?;
        } else if args.number_nonblank_lines {
            if line.is_empty() {
                writeln!(writer)?;
            } else {
                write_numbered(&mut writer, &mut num, &line)?;
            }
        } else {
            writeln!(writer, "{line}")?;
        }
    }
    Ok(())
}

fn write_numbered(
    writer: &mut impl Write,
    num: &mut usize,
    line: &str,
) -> Result<(), std::io::Error> {
    *num = num.saturating_add(1);
    writeln!(writer, "{num:>6}\t{line}")
}

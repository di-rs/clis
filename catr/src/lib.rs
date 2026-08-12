use std::io::{BufRead, Write};

pub struct Flags {
    pub number_lines: bool,
    pub number_nonblank_lines: bool,
    pub squeeze_blank: bool,
}

/// # Errors
/// Throws error if unable to decode line in the reader or write to writer is failed
pub fn write_lines(
    mut reader: impl BufRead,
    mut writer: impl Write,
    args: &Flags,
) -> Result<(), std::io::Error> {
    let mut num = 0_usize;
    let mut prev_empty = false;
    let mut line = String::new();

    loop {
        let bytes = reader.read_line(&mut line)?;
        if bytes == 0 {
            break;
        }

        let is_empty = line.trim_end_matches(['\r', '\n']).is_empty();

        if args.squeeze_blank {
            dbg!(is_empty, prev_empty);
            if is_empty && prev_empty {
                line.clear();
                continue;
            }
            prev_empty = is_empty;
        }

        if args.number_lines {
            write_numbered(&mut writer, &mut num, &line)?;
        } else if args.number_nonblank_lines {
            if is_empty {
                writeln!(writer)?;
            } else {
                write_numbered(&mut writer, &mut num, &line)?;
            }
        } else {
            write!(writer, "{line}")?;
        }

        line.clear();
    }
    Ok(())
}

fn write_numbered(
    writer: &mut impl Write,
    num: &mut usize,
    line: &str,
) -> Result<(), std::io::Error> {
    *num = num.saturating_add(1);
    write!(writer, "{num:>6}\t{line}")
}

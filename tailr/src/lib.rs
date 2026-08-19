use std::{
    io::{BufRead, Read, Seek, SeekFrom, Write},
    str::FromStr,
};

#[derive(Debug, PartialEq, Eq, Clone, Copy)]
pub enum TakeValue {
    PlusZero,
    TakeNum(i64),
}

impl FromStr for TakeValue {
    type Err = std::num::ParseIntError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s == "+0" {
            Ok(Self::PlusZero)
        } else {
            let mut parsed = i64::from_str(s)?;

            // if number without sign -> we should add `-`
            if !s.starts_with(['-', '+']) {
                parsed *= -1;
            }

            Ok(Self::TakeNum(parsed))
        }
    }
}

pub struct BufferInfo {
    pub lines: i64,
    pub bytes: i64,
}

/// # Errors
/// Throws error when couldn't read the line
pub fn get_buffer_info(mut reader: impl BufRead) -> Result<BufferInfo, std::io::Error> {
    let mut lines: i64 = 0;
    let mut bytes: i64 = 0;
    let mut buf = Vec::new();

    while let bytes_read = reader.read_until(b'\n', &mut buf)?
        && bytes_read != 0
    {
        lines = lines.saturating_add(1);

        let line_bytes = i64::try_from(bytes_read).unwrap_or(i64::MAX);
        bytes = bytes.saturating_add(line_bytes);

        buf.clear();
    }

    Ok(BufferInfo { lines, bytes })
}

/// # Errors
/// Throws error when couldn't read the line or write to writer
pub fn print_lines(
    mut reader: impl BufRead,
    mut writer: impl Write,
    num_lines: TakeValue,
    total_lines: i64,
) -> Result<(), std::io::Error> {
    if let Some(start) = get_start_index(num_lines, total_lines) {
        let mut line_num: u64 = 0;
        let mut buf = Vec::new();

        while let bytes_read = reader.read_until(b'\n', &mut buf)?
            && bytes_read != 0
        {
            if line_num >= start {
                write!(writer, "{}", String::from_utf8_lossy(&buf))?;
            }
            line_num = line_num.saturating_add(1);
            buf.clear();
        }
    }
    Ok(())
}

/// # Errors
/// Throws error when couldn't read the line or write to writer
pub fn print_bytes<R: Read + Seek>(
    mut reader: R,
    mut writer: impl Write,
    num_bytes: TakeValue,
    total_bytes: i64,
) -> Result<(), std::io::Error> {
    if let Some(start) = get_start_index(num_bytes, total_bytes) {
        reader.seek(SeekFrom::Start(start))?;
        let mut buf = Vec::new();
        reader.read_to_end(&mut buf)?;
        if !buf.is_empty() {
            write!(writer, "{}", String::from_utf8_lossy(&buf))?;
        }
    }
    Ok(())
}

#[allow(
    clippy::arithmetic_side_effects,
    clippy::cast_sign_loss,
    clippy::as_conversions
)]
const fn get_start_index(take_val: TakeValue, total: i64) -> Option<u64> {
    if total == 0 {
        return None;
    }
    match take_val {
        TakeValue::PlusZero => Some(0),
        TakeValue::TakeNum(num) => {
            if num == 0 || num > total {
                None
            } else {
                let start = if num < 0 { total + num } else { num - 1 };
                if start < 0 {
                    Some(0)
                } else {
                    Some(start as u64)
                }
            }
        }
    }
}

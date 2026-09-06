use std::{
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
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

/// # Errors
/// Throws error when couldn't read the line
pub fn get_total_lines(reader: impl Read) -> Result<u64, std::io::Error> {
    let mut reader = BufReader::new(reader);
    let mut lines: u64 = 0;
    let mut buf = Vec::new();
    while let bytes_read = reader.read_until(b'\n', &mut buf)?
        && bytes_read != 0
    {
        lines = lines.saturating_add(1);
        buf.clear();
    }
    Ok(lines)
}

/// # Errors
/// Throws error when couldn't read the line or write to writer
pub fn print_lines<R: BufRead + Seek>(
    mut reader: R,
    mut writer: impl Write,
    num_lines: TakeValue,
    total_lines: u64,
) -> Result<(), std::io::Error> {
    if let Some(start) = get_start_index(num_lines, total_lines) {
        let mut buf = Vec::new();
        for _ in 0..start {
            if reader.read_until(b'\n', &mut buf)? == 0 {
                return Ok(());
            }
            buf.clear();
        }
        std::io::copy(&mut reader, &mut writer)?;
    }
    Ok(())
}

/// # Errors
/// Throws error when couldn't read the line or write to writer
pub fn print_bytes<R: Read + Seek>(
    mut reader: R,
    mut writer: impl Write,
    num_bytes: TakeValue,
    total_bytes: u64,
) -> Result<(), std::io::Error> {
    if let Some(start) = get_start_index(num_bytes, total_bytes) {
        reader.seek(SeekFrom::Start(start))?;
        std::io::copy(&mut reader, &mut writer)?;
    }
    Ok(())
}

fn get_start_index(take_val: TakeValue, total: u64) -> Option<u64> {
    if total == 0 {
        return None;
    }
    match take_val {
        TakeValue::PlusZero => Some(0),
        TakeValue::TakeNum(0) => None,
        TakeValue::TakeNum(num) if num > 0 => {
            let num = num.saturating_sub(1).unsigned_abs();
            (num < total).then_some(num)
        }
        TakeValue::TakeNum(num) => Some(total.saturating_sub(num.unsigned_abs())),
    }
}

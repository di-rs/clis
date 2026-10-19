use chrono::{Datelike, Local, NaiveDateTime, TimeZone, Timelike};
use color_eyre::{
    Result,
    eyre::{Context, eyre},
};
use rustix::fs::{self, AtFlags, CWD, Timespec, Timestamps, UTIME_NOW, UTIME_OMIT};
use std::{fs::OpenOptions, os::unix::fs::MetadataExt, path::Path};

const NOW: Timespec = Timespec {
    tv_sec: 0,
    tv_nsec: UTIME_NOW,
};
const OMIT: Timespec = Timespec {
    tv_sec: 0,
    tv_nsec: UTIME_OMIT,
};

/// Which timestamps to update and whether to create or follow files.
#[derive(Debug)]
#[allow(
    clippy::struct_excessive_bools,
    reason = "Independent flags select timestamps and filesystem behavior."
)]
pub struct Options {
    pub access: bool,
    pub modify: bool,
    pub no_create: bool,
    pub no_dereference: bool,
    pub adjustment: i64,
    pub times: Option<Timestamps>,
}

/// Parse the POSIX numeric timestamp in the process's local time zone.
///
/// # Errors
/// Rejects malformed timestamps and dates outside the supported range.
pub fn parse_timestamp(value: &str) -> Result<Timespec> {
    let invalid = || eyre!("Invalid timestamp format. Use [[CC]YY]MMDDhhmm[.SS]");
    let (digits, seconds) = value.split_once('.').unwrap_or((value, "00"));
    if !digits.bytes().all(|b| b.is_ascii_digit())
        || seconds.len() != 2
        || !seconds.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(invalid());
    }
    let year = match digits.len() {
        8 => Local::now().year().to_string(),
        10 => if digits.get(..2).ok_or_else(invalid)?.parse::<u8>()? >= 69 {
            "19"
        } else {
            "20"
        }
        .to_owned(),
        12 => String::new(),
        _ => return Err(invalid()),
    };
    let naive = NaiveDateTime::parse_from_str(&format!("{year}{digits}{seconds}"), "%Y%m%d%H%M%S")
        .map_err(|_| invalid())?;
    let time = Local
        .from_local_datetime(&naive)
        .earliest()
        .ok_or_else(invalid)?;
    let nanos = i64::from(time.timestamp_subsec_nanos());
    Ok(Timespec {
        tv_sec: time
            .timestamp()
            .checked_add(nanos / 1_000_000_000)
            .ok_or_else(invalid)?,
        tv_nsec: nanos % 1_000_000_000,
    })
}

/// Parse BSD's signed hours/minutes/seconds adjustment.
///
/// # Errors
/// Rejects non-digits and lengths other than two, four, or six digits.
pub fn parse_adjustment(value: &str) -> Result<i64> {
    let digits = value.strip_prefix('-').unwrap_or(value);
    if !matches!(digits.len(), 2 | 4 | 6) || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(eyre!("Invalid adjustment. Use [-][[hh]mm]SS"));
    }
    let mut total = 0_i64;
    for pair in digits.as_bytes().as_chunks::<2>().0 {
        let part: i64 = std::str::from_utf8(pair)?.parse()?;
        total = total
            .checked_mul(60)
            .and_then(|v| v.checked_add(part))
            .ok_or_else(|| eyre!("Adjustment is out of range"))?;
    }
    Ok(if value.starts_with('-') {
        total
            .checked_neg()
            .ok_or_else(|| eyre!("Adjustment is out of range"))?
    } else {
        total
    })
}

/// Interpret the BSD positional `MMDDhhmm[YY]` timestamp, if present.
///
/// # Errors
/// Returns an error for a numeric operand that is not a valid timestamp.
pub fn legacy_timestamp(value: &Path) -> Result<Option<Timespec>> {
    let Some(value) = value.to_str() else {
        return Ok(None);
    };
    if !matches!(value.len(), 8 | 10) || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Ok(None);
    }
    let numeric = if value.len() == 10 {
        let year = value
            .get(8..)
            .ok_or_else(|| eyre!("Invalid legacy timestamp"))?;
        let century = if year.parse::<u8>()? >= 39 {
            "19"
        } else {
            "20"
        };
        format!(
            "{century}{year}{}",
            value
                .get(..8)
                .ok_or_else(|| eyre!("Invalid legacy timestamp"))?
        )
    } else {
        value.to_owned()
    };
    let mut time = parse_timestamp(&numeric)?;
    time.tv_sec = time
        .tv_sec
        .checked_add(i64::from(Local::now().second()))
        .ok_or_else(|| eyre!("Legacy timestamp out of range"))?;
    Ok(Some(time))
}

/// Read both timestamps, optionally reading the symbolic link itself.
///
/// # Errors
/// Returns the filesystem error if the reference cannot be inspected.
pub fn reference_times(path: &Path, no_dereference: bool) -> Result<Timestamps> {
    let stat = if no_dereference {
        std::fs::symlink_metadata(path)
    } else {
        std::fs::metadata(path)
    }
    .wrap_err_with(|| format!("failed to read timestamps for {}", path.display()))?;
    Ok(Timestamps {
        last_access: Timespec {
            tv_sec: stat.atime(),
            tv_nsec: stat.atime_nsec(),
        },
        last_modification: Timespec {
            tv_sec: stat.mtime(),
            tv_nsec: stat.mtime_nsec(),
        },
    })
}

/// Parse a GNU date expression, relative to the supplied reference when present.
///
/// # Errors
/// Rejects invalid date expressions and timestamps outside the parser's range.
pub fn date_times(value: &str, reference: Option<&Timestamps>) -> Result<Timestamps> {
    // The GNU parser normalizes DST gaps; validate BSD local timestamps first.
    let local = value.trim().replace(',', ".");
    for format in ["%Y-%m-%dT%H:%M:%S%.f", "%Y-%m-%d %H:%M:%S%.f"] {
        if let Ok(naive) = NaiveDateTime::parse_from_str(&local, format) {
            if naive.nanosecond() >= 1_000_000_000 {
                return Err(eyre!(
                    "invalid date '{value}': seconds must be between 00 and 59"
                ));
            }
            let time = Local
                .from_local_datetime(&naive)
                .earliest()
                .ok_or_else(|| eyre!("invalid date '{value}': nonexistent local time"))?;
            let nanos = i64::from(time.timestamp_subsec_nanos());
            let time = Timespec {
                tv_sec: time
                    .timestamp()
                    .checked_add(nanos / 1_000_000_000)
                    .ok_or_else(|| eyre!("Date out of range"))?,
                tv_nsec: nanos % 1_000_000_000,
            };
            return Ok(Timestamps {
                last_access: time,
                last_modification: time,
            });
        }
    }

    let parse = |base: Option<Timespec>| -> Result<Timespec> {
        let parsed = if let Some(base) = base {
            let timestamp = jiff::Timestamp::new(base.tv_sec, i32::try_from(base.tv_nsec)?)?;
            parse_datetime::parse_datetime_at_date(
                timestamp.to_zoned(jiff::tz::TimeZone::system()),
                value,
            )
        } else {
            parse_datetime::parse_datetime(value)
        }
        .wrap_err_with(|| format!("invalid date '{value}'"))?;
        Ok(Timespec {
            tv_sec: parsed.unix_epoch_second(),
            tv_nsec: i64::from(parsed.subsec_nanosecond()),
        })
    };
    let access = parse(reference.map(|r| r.last_access))?;
    let modified = if let Some(reference) = reference {
        parse(Some(reference.last_modification))?
    } else {
        access
    };
    Ok(Timestamps {
        last_access: access,
        last_modification: modified,
    })
}

const fn flags(no_dereference: bool) -> AtFlags {
    if no_dereference {
        AtFlags::SYMLINK_NOFOLLOW
    } else {
        AtFlags::empty()
    }
}

impl Options {
    /// Update a file without opening existing files or truncating their contents.
    ///
    /// # Errors
    /// Returns errors for inaccessible paths, invalid adjustments, or failed updates.
    pub fn apply(&self, path: &Path) -> Result<()> {
        let mut times = if let Some(times) = &self.times {
            times.clone()
        } else if self.adjustment != 0 {
            match reference_times(path, self.no_dereference) {
                Ok(times) => times,
                Err(error)
                    if self.no_create
                        && error
                            .downcast_ref::<std::io::Error>()
                            .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound) =>
                {
                    return Ok(());
                }
                Err(error) => return Err(error),
            }
        } else {
            Timestamps {
                last_access: NOW,
                last_modification: NOW,
            }
        };
        for time in [&mut times.last_access, &mut times.last_modification] {
            time.tv_sec = time
                .tv_sec
                .checked_add(self.adjustment)
                .ok_or_else(|| eyre!("Timestamp adjustment overflow"))?;
        }
        if !self.access {
            times.last_access = OMIT;
        }
        if !self.modify {
            times.last_modification = OMIT;
        }
        match fs::utimensat(CWD, path, &times, flags(self.no_dereference)) {
            Ok(()) => Ok(()),
            Err(rustix::io::Errno::NOENT) if self.no_create => Ok(()),
            Err(rustix::io::Errno::NOENT) => {
                // No truncation if another process creates the file between these calls.
                let file = OpenOptions::new()
                    .write(true)
                    .create(true)
                    .truncate(false)
                    .open(path)
                    .wrap_err_with(|| format!("failed to create file {}", path.display()))?;
                fs::futimens(&file, &times)
                    .wrap_err_with(|| format!("failed to update timestamps for {}", path.display()))
            }
            Err(error) => Err(error)
                .wrap_err_with(|| format!("failed to update timestamps for {}", path.display())),
        }
    }
}

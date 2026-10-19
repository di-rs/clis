use clap::{Arg, ArgAction, Parser};
use color_eyre::{
    Result,
    eyre::{Context, eyre},
};
use jiff::{
    Zoned,
    civil::DateTime,
    tz::{AmbiguousOffset, TimeZone},
};
use rustix::fs::{Timespec, Timestamps};
use std::path::{Path, PathBuf};

/// Change file access and modification times, creating missing files.
#[derive(Parser, Debug)]
#[allow(
    clippy::struct_excessive_bools,
    reason = "The booleans represent independent CLI switches."
)]
#[command(author, version, disable_help_flag = true, args_override_self = true)]
#[command(arg(Arg::new("help").long("help").action(ArgAction::Help).help("Print help")))]
pub struct Cli {
    /// Change only the access time (combine with -m for both)
    #[arg(short = 'a', long)]
    pub access: bool,
    /// Change only the modification time (combine with -a for both)
    #[arg(short = 'm', long)]
    pub modify: bool,
    /// Do not create missing files
    #[arg(short = 'c', long = "no-create")]
    pub no_create: bool,
    /// Change symbolic links themselves; implies -c
    #[arg(short = 'h', long = "no-dereference")]
    pub no_dereference: bool,
    /// Set local year, month, day, hour, minute and optional .seconds, e.g. 200001011200.30
    #[arg(short = 't', long, value_parser = parse_timestamp, overrides_with_all = ["date", "reference"])]
    pub timestamp: Option<Timespec>,
    /// Set a date/time ("2000-01-01 12:00:00"), relative time ("1 hour"), or epoch seconds ("@946684800")
    #[arg(short = 'd', long, overrides_with = "timestamp")]
    pub date: Option<String>,
    /// Copy this file's times; -d can offset each time
    #[arg(short = 'r', long, overrides_with = "timestamp", value_name = "FILE")]
    pub reference: Option<PathBuf>,
    /// Add or subtract [-][[hh]mm]SS, e.g. -0130 subtracts 90 seconds; skips missing files
    #[arg(short = 'A', long, value_parser = parse_adjustment, allow_hyphen_values = true)]
    pub adjustment: Option<i64>,
    /// Select access (access, atime, use) or modification (modify, mtime) time
    #[arg(long = "time", value_parser = ["access", "atime", "use", "modify", "mtime"])]
    pub time: Option<String>,
    /// Files to update; '-' is a literal filename
    #[arg(required = true)]
    pub files: Vec<PathBuf>,
}

/// Parse the POSIX numeric timestamp in the process's local time zone.
///
/// # Errors
/// Rejects malformed timestamps and dates outside the supported range.
fn parse_timestamp(value: &str) -> Result<Timespec> {
    let invalid = || eyre!("Invalid timestamp format. Use [[CC]YY]MMDDhhmm[.SS]");
    let (digits, seconds) = value.split_once('.').unwrap_or((value, "00"));
    if !digits.bytes().all(|b| b.is_ascii_digit())
        || seconds.len() != 2
        || !seconds.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(invalid());
    }
    let year = match digits.len() {
        8 => Zoned::now().year().to_string(),
        10 => if digits.get(..2).ok_or_else(invalid)?.parse::<u8>()? >= 69 {
            "19"
        } else {
            "20"
        }
        .to_owned(),
        12 => String::new(),
        _ => return Err(invalid()),
    };
    let second: u8 = seconds.parse()?;
    if second > 60 {
        return Err(invalid());
    }
    let naive = DateTime::strptime(
        "%Y%m%d%H%M%S",
        format!("{year}{digits}{:02}", second.min(59)),
    )
    .map_err(|_| invalid())?;
    let mut time = local_time(naive).map_err(|_| invalid())?;
    time.tv_sec = time
        .tv_sec
        .checked_add(i64::from(second == 60))
        .ok_or_else(invalid)?;
    Ok(time)
}

/// Parse BSD's signed hours/minutes/seconds adjustment.
///
/// # Errors
/// Rejects non-digits and lengths other than two, four, or six digits.
fn parse_adjustment(value: &str) -> Result<i64> {
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
        .checked_add(i64::from(Zoned::now().second()))
        .ok_or_else(|| eyre!("Legacy timestamp out of range"))?;
    Ok(Some(time))
}

/// Parse a GNU date expression, relative to the supplied reference when present.
///
/// # Errors
/// Rejects invalid date expressions and timestamps outside the parser's range.
pub fn date_times(value: &str, reference: Option<&Timestamps>) -> Result<Timestamps> {
    // The GNU parser normalizes DST gaps; validate BSD local timestamps first.
    let local = value.trim().replace(',', ".");
    let local = match local.split_once('.') {
        Some((whole, fraction))
            if fraction.len() > 9 && fraction.bytes().all(|b| b.is_ascii_digit()) =>
        {
            format!("{whole}.{}", fraction.get(..9).unwrap_or(fraction))
        }
        _ => local,
    };
    for format in [
        "%Y-%m-%dT%H:%M:%S%.f",
        "%Y-%m-%d %H:%M:%S%.f",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
    ] {
        if let Ok(naive) = DateTime::strptime(format, &local) {
            // Jiff clamps leap seconds; date expressions must reject them.
            if local
                .rsplit(':')
                .next()
                .is_some_and(|seconds| seconds.starts_with("60"))
            {
                return Err(eyre!(
                    "invalid date '{value}': seconds must be between 00 and 59"
                ));
            }
            let time = local_time(naive).wrap_err_with(|| format!("invalid date '{value}'"))?;
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
    let modified =
        if let Some(reference) = reference.filter(|r| r.last_modification != r.last_access) {
            parse(Some(reference.last_modification))?
        } else {
            access
        };
    Ok(Timestamps {
        last_access: access,
        last_modification: modified,
    })
}

fn local_time(date: DateTime) -> Result<Timespec> {
    let time = TimeZone::system().to_ambiguous_timestamp(date);
    if matches!(time.offset(), AmbiguousOffset::Gap { .. }) {
        return Err(eyre!("nonexistent local time"));
    }
    let nanos = time.earlier()?.as_nanosecond();
    Ok(Timespec {
        tv_sec: i64::try_from(nanos.div_euclid(1_000_000_000))?,
        tv_nsec: i64::try_from(nanos.rem_euclid(1_000_000_000))?,
    })
}

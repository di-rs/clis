use chrono::NaiveDateTime;
use color_eyre::{Result, eyre::Context};
use std::{fs::File, path::Path, time::SystemTime};

/// # Errors
/// Throws error if cannot open or create file
pub fn file_proceed(
    file: &Path,
    timestamp: Option<NaiveDateTime>,
    skip_create: bool,
) -> Result<()> {
    let stats = std::fs::metadata(file);

    let file = match stats {
        Err(_) => {
            if skip_create {
                return Ok(());
            }
            File::create(file)
                .wrap_err_with(|| format!("failed to create file {}", file.display()))?
        }
        Ok(_) => {
            File::open(file).wrap_err_with(|| format!("failed to open file {}", file.display()))?
        }
    };

    set_modified(&file, timestamp)
}

fn set_modified(file: &File, timestamp: Option<NaiveDateTime>) -> Result<()> {
    let time = timestamp.map_or_else(SystemTime::now, |timestamp| {
        SystemTime::from(timestamp.and_utc())
    });

    file.set_modified(time)
        .wrap_err("Failed to update file time")
}

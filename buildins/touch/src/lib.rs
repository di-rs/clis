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

/// Read both timestamps, optionally reading the symbolic link itself.
///
/// # Errors
/// Returns the filesystem error if the reference cannot be inspected.
pub fn reference_times(path: &Path, no_dereference: bool) -> std::io::Result<Timestamps> {
    let stat = if no_dereference {
        std::fs::symlink_metadata(path)
    } else {
        std::fs::metadata(path)
    }?;
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
                Err(error) if self.no_create && error.kind() == std::io::ErrorKind::NotFound => {
                    return Ok(());
                }
                Err(error) => {
                    return Err(error).wrap_err_with(|| {
                        format!("failed to read timestamps for {}", path.display())
                    });
                }
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

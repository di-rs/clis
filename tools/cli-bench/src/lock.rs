use crate::BenchError;
use std::{
    fs::{self, File},
    os::unix::fs::{DirBuilderExt, MetadataExt},
    path::Path,
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

/// Held per-user OS lock. Dropping closes the descriptor, without unlinking it.
/// Only cooperating cli-bench processes for this account are excluded.
///
/// ```compile_fail
/// use cli_bench::{MeasurementLock, ExperimentPreparation, ProcessRunner, Store, prepare_experiment};
/// fn cannot_release_early(lock: MeasurementLock, request: &ExperimentPreparation<'_>, store: &Store, runner: &ProcessRunner) -> Result<(), cli_bench::BenchError> {
///     let prepared = prepare_experiment(&lock, request, store, runner)?;
///     drop(lock);
///     let _cases = prepared.cases();
///     Ok(())
/// }
/// ```
#[derive(Debug)]
pub struct MeasurementLock {
    _file: File,
    wait_duration: Duration,
}
impl MeasurementLock {
    /// Acquire before preparation, retaining this capability through final checks.
    /// Reports contention once; waiting has no build/sample deadline.
    /// # Errors
    /// Rejects unsafe lock paths, cancellation and waiting-diagnostic failures.
    pub fn acquire(
        cancellation: &AtomicBool,
        mut on_wait: impl FnMut() -> Result<(), BenchError>,
    ) -> Result<Self, BenchError> {
        check_cancelled(cancellation)?;
        // macOS aliases the system /tmp directory to /private/tmp. Only this
        // system root is canonicalized; harness-owned entries never follow links.
        let root = fs::canonicalize("/tmp")?;
        let uid = rustix::process::geteuid().as_raw();
        let file = open_lock(&root.join(format!("cli-bench-{uid}")), uid)?;
        let start = Instant::now();
        let mut reported = false;
        loop {
            check_cancelled(cancellation)?;
            match file.try_lock() {
                Ok(()) => {
                    return Ok(Self {
                        _file: file,
                        wait_duration: start.elapsed(),
                    });
                }
                Err(std::fs::TryLockError::WouldBlock) => {
                    if !reported {
                        on_wait()?;
                        reported = true;
                    }
                    check_cancelled(cancellation)?;
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(std::fs::TryLockError::Error(error)) => return Err(error.into()),
            }
        }
    }
    #[must_use]
    pub const fn wait_duration(&self) -> Duration {
        self.wait_duration
    }
}

fn check_cancelled(cancellation: &AtomicBool) -> Result<(), BenchError> {
    if cancellation.load(Ordering::Relaxed) {
        return Err(BenchError::Execution(
            "cancelled while acquiring measurement lock".into(),
        ));
    }
    Ok(())
}
fn open_lock(path: &Path, uid: u32) -> Result<File, BenchError> {
    use rustix::fs::{Mode, OFlags, open, openat};
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(error) => return Err(error.into()),
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o777 != 0o700 {
        return Err(BenchError::Execution(
            "measurement lock directory must be owner-only, owned and not a symlink".into(),
        ));
    }
    let directory = File::from(
        open(
            path,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )
        .map_err(std::io::Error::from)?,
    );
    let opened = directory.metadata()?;
    if (metadata.dev(), metadata.ino()) != (opened.dev(), opened.ino()) {
        return Err(BenchError::Execution(
            "measurement lock directory changed".into(),
        ));
    }
    let file = File::from(
        openat(
            &directory,
            "measurement.lock",
            OFlags::RDWR | OFlags::CREATE | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::RUSR | Mode::WUSR,
        )
        .map_err(std::io::Error::from)?,
    );
    let metadata = file.metadata()?;
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.mode() & 0o777 != 0o600
        || metadata.nlink() != 1
    {
        return Err(BenchError::Execution(
            "measurement lock file must be an owned private regular file".into(),
        ));
    }
    Ok(file)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;

    #[test]
    fn cancellation_and_diagnostic_failure_never_bypass_contention()
    -> Result<(), Box<dyn std::error::Error>> {
        let cancelled = AtomicBool::new(false);
        let held = MeasurementLock::acquire(&cancelled, || Ok(()))?;
        let mut notices = 0;
        let result = MeasurementLock::acquire(&cancelled, || {
            notices += 1;
            cancelled.store(true, Ordering::Relaxed);
            Ok(())
        });
        require(
            result.is_err(),
            &format!("assertion failed: {}", stringify!(result.is_err())),
        )?;
        require(
            (notices) == (1),
            &format!("assertion failed: {}", stringify!((notices) == (1))),
        )?;
        cancelled.store(false, Ordering::Relaxed);
        require(
            MeasurementLock::acquire(&cancelled, || {
                Err(BenchError::Execution("diagnostic failed".into()))
            })
            .is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(
                    MeasurementLock::acquire(&cancelled, || Err(BenchError::Execution(
                        "diagnostic failed".into()
                    )))
                    .is_err()
                )
            ),
        )?;
        drop(held);
        require(
            MeasurementLock::acquire(&cancelled, || Ok(())).is_ok(),
            &format!(
                "assertion failed: {}",
                stringify!(MeasurementLock::acquire(&cancelled, || Ok(())).is_ok())
            ),
        )?;
        Ok(())
    }
    #[test]
    fn lock_entries_reject_symlinks_wrong_owner_permissions_and_hardlinks()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::PermissionsExt;
        let root = assert_fs::TempDir::new()?;
        let uid = rustix::process::geteuid().as_raw();
        let owned = root.join("owned");
        let file = open_lock(&owned, uid)?;
        let identity = file.metadata()?.ino();
        drop(file);
        require(
            (open_lock(&owned, uid)?.metadata()?.ino()) == (identity),
            &format!(
                "assertion failed: {}",
                stringify!((open_lock(&owned, uid)?.metadata()?.ino()) == (identity))
            ),
        )?;
        require(
            open_lock(&owned, uid.wrapping_add(1)).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&owned, uid.wrapping_add(1)).is_err())
            ),
        )?;
        fs::set_permissions(&owned, fs::Permissions::from_mode(0o755))?;
        require(
            open_lock(&owned, uid).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&owned, uid).is_err())
            ),
        )?;
        fs::set_permissions(&owned, fs::Permissions::from_mode(0o700))?;
        std::os::unix::fs::symlink(&owned, root.join("linked"))?;
        require(
            open_lock(&root.join("linked"), uid).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&root.join("linked"), uid).is_err())
            ),
        )?;
        let path = owned.join("measurement.lock");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644))?;
        require(
            open_lock(&owned, uid).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&owned, uid).is_err())
            ),
        )?;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
        fs::hard_link(&path, root.join("hardlink"))?;
        require(
            open_lock(&owned, uid).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&owned, uid).is_err())
            ),
        )?;
        fs::remove_file(&path)?;
        std::os::unix::fs::symlink(root.join("hardlink"), &path)?;
        require(
            open_lock(&owned, uid).is_err(),
            &format!(
                "assertion failed: {}",
                stringify!(open_lock(&owned, uid).is_err())
            ),
        )?;
        Ok(())
    }
    fn require(condition: bool, message: &str) -> Result<(), Box<dyn std::error::Error>> {
        if condition {
            Ok(())
        } else {
            Err(message.into())
        }
    }
}

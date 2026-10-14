#![doc = include_str!("../README.md")]

use std::{ffi::OsStr, path::Path, time::Duration};

pub use assert_cmd::Command;

mod fixtures;
pub use fixtures::Fixtures;

#[doc(hidden)]
pub use assert_cmd::cargo::cargo_bin;

/// Build a command with repository test defaults, leaving overrides to callers.
///
/// The child starts in `package_dir`, with C locale, UTC, a request for plain
/// output via environment, and logging disabled. Other environment variables
/// are inherited; this is not a hermetic sandbox. No parent state is modified.
///
/// The 30-second `assert_cmd` timeout is not a deadline for a process tree or a
/// blocked stdin writer. Run tests under nextest's outer CI timeout as well.
#[must_use]
pub fn command(binary: impl AsRef<OsStr>, package_dir: impl AsRef<Path>) -> Command {
    let mut command = Command::new(binary);
    command
        .current_dir(package_dir)
        .timeout(Duration::from_secs(30));
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("CLIS_LOG_") {
            command.env_remove(key);
        }
    }
    command.envs([
        ("LC_ALL", "C"),
        ("TZ", "UTC"),
        ("NO_COLOR", "1"),
        ("TERM", "dumb"),
        ("CLIS_LOG_LEVEL", "off"),
        ("CLIS_LOG_STDERR", "off"),
    ]);
    command
}

/// Discover the consuming package's built CLI and apply [`command`] defaults.
///
/// Use in Cargo integration tests. An explicit binary name is also supported.
#[macro_export]
macro_rules! cargo_bin_cmd {
    () => {
        $crate::cargo_bin_cmd!(env!("CARGO_PKG_NAME"))
    };
    ($name:expr) => {
        $crate::command($crate::cargo_bin!($name), env!("CARGO_MANIFEST_DIR"))
    };
}

/// Locate `tests/inputs/` and `tests/expected/` in the consuming package.
#[macro_export]
macro_rules! fixtures {
    () => {
        $crate::Fixtures::new(concat!(env!("CARGO_MANIFEST_DIR"), "/tests"))
    };
}

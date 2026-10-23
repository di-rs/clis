#![doc = include_str!("../docs/testing.md")]

use std::{ffi::OsStr, path::Path, time::Duration};

pub use assert_cmd::Command;
pub use cli_testing_macros::test_case;

pub mod catalog;
mod context;
mod error;
mod harness;
pub mod recording;
pub mod reference;
pub use context::{Context, TestContext, Workspace, customize};
pub use error::{Error, Result};
pub use harness::run_catalog;

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
    let mut command = Command::from_std(std_command(binary, package_dir));
    command.timeout(Duration::from_secs(30));
    command
}

/// Configure a standard process command for custom pipes and signal handling.
/// Callers own process deadlines and child cleanup.
#[must_use]
pub fn std_command(
    binary: impl AsRef<OsStr>,
    package_dir: impl AsRef<Path>,
) -> std::process::Command {
    let mut command = std::process::Command::new(binary);
    command.current_dir(package_dir);
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("SANCTUARY_LOG_") {
            command.env_remove(key);
        }
    }
    command.envs(default_environment());
    command
}

pub(crate) fn default_environment() -> std::collections::BTreeMap<String, String> {
    [
        ("LC_ALL", "C"),
        ("TZ", "UTC"),
        ("NO_COLOR", "1"),
        ("TERM", "dumb"),
        ("SANCTUARY_LOG_LEVEL", "off"),
        ("SANCTUARY_LOG_STDERR", "off"),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_owned(), value.to_owned()))
    .collect()
}

/// Build a test context using the consuming package's executable and directory.
#[macro_export]
macro_rules! context {
    () => {
        $crate::TestContext::new(
            $crate::cargo_bin!(env!("CARGO_PKG_NAME")),
            env!("CARGO_MANIFEST_DIR"),
        )
    };
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

use std::{
    fs, io,
    path::{Component, Path, PathBuf},
};

/// Read-only access to package-owned input and expected-output fixtures.
///
/// Names are relative to the respective fixture directory. These are trusted
/// repository files; this API validates names, not symlink destinations.
#[derive(Debug, Clone)]
pub struct Fixtures {
    root: PathBuf,
}

impl Fixtures {
    /// Set the package's `tests` directory. Prefer [`crate::fixtures!`].
    #[must_use]
    pub fn new(tests_dir: impl Into<PathBuf>) -> Self {
        Self {
            root: tests_dir.into(),
        }
    }

    /// Read raw bytes from `inputs/name` without decoding or normalization.
    ///
    /// # Errors
    /// Returns an error for an invalid name or unreadable fixture.
    pub fn read_input(&self, name: impl AsRef<Path>) -> io::Result<Vec<u8>> {
        self.read("inputs", name.as_ref())
    }

    /// Read raw bytes from `expected/name` without decoding or normalization.
    ///
    /// # Errors
    /// Returns an error for an invalid name or unreadable fixture.
    pub fn read_expected(&self, name: impl AsRef<Path>) -> io::Result<Vec<u8>> {
        self.read("expected", name.as_ref())
    }

    fn read(&self, directory: &str, name: &Path) -> io::Result<Vec<u8>> {
        if name.as_os_str().is_empty()
            || !name
                .components()
                .all(|part| matches!(part, Component::Normal(_)))
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!(
                    "fixture name must be relative within {directory}/: {}",
                    name.display()
                ),
            ));
        }
        let path = self.root.join(directory).join(name);
        fs::read(&path).map_err(|error| {
            io::Error::new(
                error.kind(),
                format!("cannot read fixture {}: {error}", path.display()),
            )
        })
    }
}

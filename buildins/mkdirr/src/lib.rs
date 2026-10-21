use anyhow::{Context, Result};
use std::{fs, path::Path};

/// # Errors
/// Returns an error if creation fails or the path exists without parent creation.
pub fn create_directory(path: &Path, with_parent: bool) -> Result<()> {
    let res = if with_parent {
        fs::create_dir_all(path)
    } else {
        fs::create_dir(path)
    };

    res.with_context(|| format!("{}: failed to create directory", path.display()))
}

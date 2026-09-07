use color_eyre::{
    Result,
    eyre::{Context, bail},
};
use std::{fs, path::Path};

/// # Errors
/// Throws error if directory is exists or it's not p;ossible to create it
pub fn create_directory(path: &Path, with_parent: bool) -> Result<()> {
    let metadata = fs::metadata(path);
    if metadata.is_ok() {
        bail!("{}: direcory already exists", path.display())
    }

    let res = if with_parent {
        fs::create_dir_all(path)
    } else {
        fs::create_dir(path)
    };

    res.wrap_err_with(|| format!("{}: failed to create directory", path.display()))
}

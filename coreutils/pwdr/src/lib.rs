use color_eyre::Result;
use std::{env, path::PathBuf};

/// # Errors
/// Throws error if cannot read current dir or dir path is broken
pub fn physical_path() -> Result<PathBuf> {
    let current_dir = std::env::current_dir()?;
    let path = current_dir.canonicalize()?;
    Ok(path)
}

/// # Errors
/// Throws error if cannot read current dir or dir path is broken
pub fn logical_path() -> Result<PathBuf> {
    let cwd = std::env::current_dir()?;

    let Some(pwd) = env::var_os("PWD").map(PathBuf::from) else {
        return Ok(cwd);
    };
    if !pwd.is_absolute() {
        return Ok(cwd);
    }

    match (pwd.canonicalize(), cwd.canonicalize()) {
        (Ok(pwd_real), Ok(cwd_real)) if pwd_real == cwd_real => Ok(pwd),
        _ => Ok(cwd),
    }
}

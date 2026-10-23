use super::buffer::Buffer;
use std::{
    fs::File,
    io::{self, Write},
    path::Path,
};
mod security;
#[derive(Debug)]
pub enum SaveOutcome {
    Saved,
    DurabilityWarning(io::Error),
}
pub fn open(path: &Path) -> io::Result<Buffer> {
    Ok(std::fs::read_to_string(path)?.into())
}
pub fn save(buffer: &Buffer, path: &Path) -> io::Result<SaveOutcome> {
    save_with_sync(buffer, path, File::sync_all)
}
pub(super) fn save_with_sync(
    buffer: &Buffer,
    path: &Path,
    sync_directory: impl FnOnce(&File) -> io::Result<()>,
) -> io::Result<SaveOutcome> {
    // Resolve existing symlinks so saving updates their target, not the link.
    let destination = match std::fs::canonicalize(path) {
        Ok(path) => path,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            match std::fs::symlink_metadata(path) {
                Ok(metadata) if metadata.file_type().is_symlink() => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidInput,
                        "save destination is a dangling symlink",
                    ));
                }
                Ok(_) => return Err(error),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => path.to_path_buf(),
                Err(error) => return Err(error),
            }
        }
        Err(error) => return Err(error),
    };
    let parent = destination
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| std::path::Path::new("."));
    let security = match std::fs::metadata(&destination) {
        Ok(metadata) => {
            if !metadata.is_file() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "save destination is not a regular file",
                ));
            }
            if metadata.permissions().readonly() {
                return Err(std::io::ErrorKind::PermissionDenied.into());
            }
            // Atomic replacement must require write access to the destination,
            // even when the directory permits renaming over it.
            rustix::fs::accessat(
                rustix::fs::CWD,
                path,
                rustix::fs::Access::WRITE_OK,
                rustix::fs::AtFlags::EACCESS,
            )?;
            let original = File::options().write(true).open(&destination)?;
            Some(security::Security::read(&destination, &original)?)
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    // Opening the directory can fail; do so before committing any changes.
    let directory = File::open(parent)?;
    let staging = tempfile::tempdir_in(parent)?;
    security::make_private_directory(staging.path())?;
    let mut file = tempfile::NamedTempFile::new_in(staging.path())?;
    security::make_private(file.path(), file.as_file())?;
    for line in &buffer.lines {
        writeln!(file, "{line}")?;
    }
    if let Some(security) = security {
        security.apply(file.path(), file.as_file())?;
    }
    file.as_file().sync_all()?;
    file.persist(&destination).map_err(|error| error.error)?;
    Ok(match sync_directory(&directory) {
        Ok(()) => SaveOutcome::Saved,
        Err(error) => SaveOutcome::DurabilityWarning(error),
    })
}

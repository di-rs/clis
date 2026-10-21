use std::io::Write;

use super::{Buffer, fileinfo::FileInfo};

#[derive(Default)]
pub struct FileBuffer {
    buffer: Buffer,
    pub(crate) fileinfo: FileInfo,
}

impl FileBuffer {
    pub fn new(buffer: Buffer, filename: &str) -> Self {
        Self {
            buffer,
            fileinfo: FileInfo::from(filename),
        }
    }

    pub fn open(file_name: &str) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(file_name)?;
        Ok(Self {
            buffer: content.into(),
            fileinfo: FileInfo::from(file_name),
        })
    }

    pub fn save(&self) -> Result<(), std::io::Error> {
        let path = &self.fileinfo.path;
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
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => path.clone(),
                    Err(error) => return Err(error),
                }
            }
            Err(error) => return Err(error),
        };
        let parent = destination
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or_else(|| std::path::Path::new("."));
        let permissions = match std::fs::metadata(&destination) {
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
                Some(metadata.permissions())
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let mut file = tempfile::NamedTempFile::new_in(parent)?;
        if let Some(permissions) = permissions {
            file.as_file().set_permissions(permissions)?;
        }
        for line in &self.buffer.lines {
            writeln!(file, "{line}")?;
        }
        file.as_file().sync_all()?;
        file.persist(&destination).map_err(|error| error.error)?;
        #[cfg(unix)]
        std::fs::File::open(parent)?.sync_all()?;
        Ok(())
    }
}

impl std::ops::Deref for FileBuffer {
    type Target = Buffer;

    fn deref(&self) -> &Self::Target {
        &self.buffer
    }
}

impl std::ops::DerefMut for FileBuffer {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.buffer
    }
}

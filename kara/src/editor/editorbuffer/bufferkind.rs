use std::path::Path;

use super::{Buffer, FileBuffer};

pub enum BufferKind {
    Buffer(Buffer),
    File(FileBuffer),
}

impl BufferKind {
    pub fn save(&self) -> Result<(), std::io::Error> {
        match &self {
            Self::File(file_buffer) => file_buffer.save(),
            Self::Buffer(_buffer) => Ok(()),
        }
    }

    pub fn filename(&self) -> Option<&Path> {
        match &self {
            Self::File(file_buffer) => Some(&file_buffer.fileinfo.path),
            Self::Buffer(_) => None,
        }
    }

    pub const fn has_file(&self) -> bool {
        matches!(self, Self::File(_))
    }
}

impl std::ops::Deref for BufferKind {
    type Target = Buffer;

    fn deref(&self) -> &Self::Target {
        match self {
            Self::Buffer(buffer) => buffer,
            Self::File(buffer) => buffer,
        }
    }
}

impl std::ops::DerefMut for BufferKind {
    fn deref_mut(&mut self) -> &mut Self::Target {
        match self {
            Self::Buffer(buffer) => buffer,
            Self::File(buffer) => buffer,
        }
    }
}

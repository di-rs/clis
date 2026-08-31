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

    pub const fn filename(&self) -> Option<&String> {
        match &self {
            Self::File(file_buffer) => Some(&file_buffer.filename),
            Self::Buffer(_) => None,
        }
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
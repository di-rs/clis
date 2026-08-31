use super::{Buffer, FileBuffer};

pub enum BufferKind {
    Buffer(Buffer),
    File(FileBuffer),
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
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
        let mut file = std::fs::File::create(&self.fileinfo.path)?;
        for line in &self.buffer.lines {
            writeln!(file, "{line}")?;
        }
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

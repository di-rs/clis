use std::io::Write;

use super::Buffer;

#[derive(Default)]
pub struct FileBuffer {
    buffer: Buffer,
    filename: String,
}

impl FileBuffer {
    pub fn open(file_name: &str) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(file_name)?;
        Ok(Self {
            buffer: content.into(),
            filename: file_name.to_owned(),
        })
    }

    pub fn save(&self) -> Result<(), std::io::Error> {
        let mut file = std::fs::File::create(&self.filename)?;
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

use std::io::BufRead;

#[allow(clippy::struct_field_names)]
#[derive(Debug, PartialEq, Eq, Default)]
pub struct FileInfo {
    pub num_lines: usize,
    pub num_words: usize,
    pub num_bytes: usize,
    pub num_chars: usize,
}

impl FileInfo {
    pub const fn add(&mut self, other: &Self) {
        self.num_lines = self.num_lines.saturating_add(other.num_lines);
        self.num_words = self.num_words.saturating_add(other.num_words);
        self.num_bytes = self.num_bytes.saturating_add(other.num_bytes);
        self.num_chars = self.num_chars.saturating_add(other.num_chars);
    }

    fn read_from_buffer(mut reader: impl BufRead) -> Result<Self, std::io::Error> {
        let mut info_acc = Self::default();
        let mut line = String::new();

        while let bytes = reader.read_line(&mut line)?
            && bytes != 0
        {
            let info = Self {
                num_lines: 1,
                num_bytes: bytes,
                num_chars: line.chars().count(),
                num_words: line.split_whitespace().count(),
            };
            info_acc.add(&info);
            line.clear();
        }

        Ok(info_acc)
    }
}

/// # Errors
/// Wher fails to write info into a writer
pub fn get_file_info(reader: impl BufRead) -> Result<FileInfo, std::io::Error> {
    FileInfo::read_from_buffer(reader)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn test_count() {
        let text = "I don't wanna go to school tomorrow.\nI just wanna chill at home.";
        let info = FileInfo::read_from_buffer(Cursor::new(text));

        let expected = FileInfo {
            num_lines: 2,
            num_words: 13,
            num_bytes: 64,
            num_chars: 64,
        };

        assert!(info.is_ok());
        assert_eq!(info.unwrap_or_default(), expected);
    }
}

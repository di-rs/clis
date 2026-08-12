use std::io::{BufRead, Write};

#[allow(clippy::struct_field_names)] 
#[derive(Debug, PartialEq, Eq, Default)]
struct FileInfo {
    num_lines: usize,
    num_words: usize,
    num_bytes: usize,
    num_chars: usize,
}

impl FileInfo {
    fn read_from_buffer(mut reader: impl BufRead) -> Result<Self, std::io::Error> {
        let mut num_lines: usize = 0;
        let mut num_words: usize = 0;
        let mut num_bytes: usize = 0;
        let mut num_chars: usize = 0;

        let mut line = String::new();

        while let Ok(bytes) = reader.read_line(&mut line) && bytes != 0 {
            num_lines = num_lines.saturating_add(1);
            num_bytes = num_bytes.saturating_add(bytes);
            num_chars = num_chars.saturating_add(line.chars().count());
            num_words = num_words.saturating_add(line.split_whitespace().count());
            
            line.clear();
        }

        Ok(Self {
            num_lines,
            num_words,
            num_bytes,
            num_chars,
        })
    }
}

/// # Errors
/// Wher fails to write info into a writer
pub fn write_file_info(reader: impl BufRead, mut writer: impl Write) -> Result<(), std::io::Error> {
    let info = FileInfo::read_from_buffer(reader);
    write!(writer, "{info:?}")?;
    Ok(())
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
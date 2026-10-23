use std::io::{self, Read, Write};

use ropey::Rope;
use unicode_segmentation::UnicodeSegmentation;

use super::{TextPosition, line_layout::LineLayout};

/// Character offsets address Unicode scalar values, never terminal cells.
#[derive(Clone, Copy)]
struct CharOffset(usize);

#[derive(Clone)]
pub struct Buffer {
    text: Rope,
    newline: &'static str,
}

impl Default for Buffer {
    fn default() -> Self {
        Self {
            text: Rope::new(),
            newline: "\n",
        }
    }
}

impl Buffer {
    pub fn read(reader: impl Read) -> io::Result<Self> {
        let text = Rope::from_reader(reader)?;
        let mut newline = "\n";
        let mut previous_cr = false;
        for character in text.chars() {
            if character == '\n' {
                if previous_cr {
                    newline = "\r\n";
                }
                break;
            }
            previous_cr = character == '\r';
        }
        Ok(Self { text, newline })
    }

    pub fn write_to(&self, writer: impl Write) -> io::Result<()> {
        self.text.write_to(writer)
    }

    pub fn insert_char(&mut self, character: char, at: TextPosition) {
        if let Some(CharOffset(offset)) = self.char_offset(at) {
            self.text.insert_char(offset, character);
        }
    }

    pub fn insert_newline(&mut self, at: TextPosition) {
        if let Some(CharOffset(offset)) = self.char_offset(at) {
            self.text.insert(offset, self.newline);
        }
    }

    pub fn delete(&mut self, at: TextPosition) -> bool {
        let Some(CharOffset(start)) = self.char_offset(at) else {
            return false;
        };
        let Some(line) = self.line_text(at.line) else {
            return false;
        };
        let count = if let Some(grapheme) = line.graphemes(true).nth(at.grapheme) {
            grapheme.chars().count()
        } else if at.line.saturating_add(1) < self.height() {
            if self.text.get_char(start) == Some('\r') {
                2
            } else {
                1
            }
        } else {
            return false;
        };
        self.text.remove(start..start.saturating_add(count));
        true
    }

    pub fn is_empty(&self) -> bool {
        self.text.len_chars() == 0
    }

    pub fn get_line(&self, index: usize) -> Option<LineLayout> {
        let text = self.line_text(index)?;
        let Ok(layout) = text.parse();
        Some(layout)
    }

    pub fn height(&self) -> usize {
        self.text.len_lines()
    }

    fn line_text(&self, index: usize) -> Option<String> {
        let mut line = self.text.get_line(index)?.to_string();
        if line.ends_with('\n') {
            line.pop();
            if line.ends_with('\r') {
                line.pop();
            }
        }
        Some(line)
    }

    fn char_offset(&self, at: TextPosition) -> Option<CharOffset> {
        let line = self.line_text(at.line)?;
        let byte = line
            .grapheme_indices(true)
            .nth(at.grapheme)
            .map_or(line.len(), |(byte, _)| byte);
        let chars = line.get(..byte)?.chars().count();
        Some(CharOffset(
            self.text
                .try_line_to_char(at.line)
                .ok()?
                .saturating_add(chars),
        ))
    }
}

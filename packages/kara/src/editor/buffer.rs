use ropey::Rope;
use unicode_segmentation::UnicodeSegmentation;

use super::{TextPosition, line_layout::LineLayout};

/// Character offsets address Unicode scalar values, never terminal cells.
#[derive(Clone, Copy)]
struct CharOffset(usize);

#[derive(Default, Clone)]
pub struct Buffer {
    text: Rope,
    // Compatibility with the existing empty buffer and synthetic end row.
    // The lossless-I/O step will use Ropey's native line model instead.
    line_count: usize,
}

impl Buffer {
    pub fn insert_char(&mut self, character: char, at: TextPosition) {
        if at.line > self.height() {
            return;
        }
        if at.line == self.height() {
            if self.line_count > 0 {
                self.text.insert(self.text.len_chars(), "\n");
            }
            self.text.insert_char(self.text.len_chars(), character);
            self.line_count = self.line_count.saturating_add(1);
        } else if let Some(CharOffset(offset)) = self.char_offset(at) {
            self.text.insert_char(offset, character);
        }
    }

    pub fn insert_newline(&mut self, at: TextPosition) {
        if at.line == self.height() {
            if self.line_count > 0 {
                self.text.insert(self.text.len_chars(), "\n");
            }
            self.line_count = self.line_count.saturating_add(1);
        } else if let Some(CharOffset(offset)) = self.char_offset(at) {
            self.text.insert(offset, "\n");
            self.line_count = self.line_count.saturating_add(1);
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
            self.line_count = self.line_count.saturating_sub(1);
            1
        } else {
            return false;
        };
        self.text.remove(start..start.saturating_add(count));
        true
    }

    pub const fn is_empty(&self) -> bool {
        self.line_count == 0
    }

    pub fn get_line(&self, index: usize) -> Option<LineLayout> {
        let text = self.line_text(index)?;
        let Ok(layout) = text.parse();
        Some(layout)
    }

    pub const fn height(&self) -> usize {
        self.line_count
    }

    fn line_text(&self, index: usize) -> Option<String> {
        if index >= self.height() {
            return None;
        }
        let mut line = self.text.get_line(index)?.to_string();
        if line.ends_with('\n') {
            line.pop();
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

impl From<String> for Buffer {
    fn from(value: String) -> Self {
        let mut text = Rope::new();
        let mut line_count: usize = 0;
        for line in value.lines() {
            if line_count > 0 {
                text.insert(text.len_chars(), "\n");
            }
            text.insert(text.len_chars(), line);
            line_count = line_count.saturating_add(1);
        }
        Self { text, line_count }
    }
}

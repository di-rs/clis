use crate::editor::{TextPosition, line_layout::LineLayout};

#[derive(Default, Clone)]
pub struct Buffer {
    pub(crate) lines: Vec<LineLayout>,
}

impl Buffer {
    pub fn insert_char(&mut self, char: char, at: TextPosition) {
        if at.line > self.height() {
            return;
        }
        if at.line == self.height() {
            let Ok(line) = char.to_string().parse();
            self.lines.push(line);
        } else if let Some(line) = self.lines.get_mut(at.line) {
            line.insert_char(char, at.grapheme);
        }
    }

    pub fn insert_newline(&mut self, at: TextPosition) {
        if at.line == self.height() {
            self.lines.push(LineLayout::default());
        } else if let Some(line) = self.lines.get_mut(at.line) {
            let new = line.split(at.grapheme);
            self.lines.insert(at.line.saturating_add(1), new);
        }
    }

    pub fn delete(&mut self, at: TextPosition) -> bool {
        let Some(line) = self.lines.get_mut(at.line) else {
            return false;
        };
        if at.grapheme < line.len() {
            line.delete(at.grapheme);
            return true;
        }

        let next_line_index = at.line.saturating_add(1);
        if self.height() > next_line_index {
            let next_line = self.lines.remove(next_line_index);
            if let Some(line) = self.lines.get_mut(at.line) {
                line.append(&next_line);
            }
            return true;
        }
        false
    }

    pub const fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    pub fn get_line(&self, idx: usize) -> Option<&LineLayout> {
        self.lines.get(idx)
    }

    pub const fn height(&self) -> usize {
        self.lines.len()
    }
}

impl From<String> for Buffer {
    fn from(value: String) -> Self {
        Self {
            lines: value.lines().map(|l| l.parse().unwrap()).collect(),
        }
    }
}

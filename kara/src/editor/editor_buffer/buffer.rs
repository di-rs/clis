use super::line::Line;
use crate::editor::Location;

#[derive(Default)]
pub struct Buffer {
    pub(crate) lines: Vec<Line>,
}

impl Buffer {
    pub fn insert_char(&mut self, char: char, at: Location) {
        if at.y > self.height() {
            return;
        }
        if at.y == self.height() {
            let str = char.to_string();
            self.lines.push(str.as_str().into());
        } else if let Some(line) = self.lines.get_mut(at.y) {
            line.insert_char(char, at.x);
        }
    }

    pub fn insert_newline(&mut self, at: Location) {
        if at.y == self.height() {
            self.lines.push(Line::default());
        } else if let Some(line) = self.lines.get_mut(at.y) {
            let new = line.split(at.x);
            self.lines.insert(at.y.saturating_add(1), new);
        }
    }

    pub fn delete(&mut self, at: Location) {
        let Some(line) = self.lines.get_mut(at.y) else {
            return;
        };
        if at.x < line.len() {
            line.delete(at.x);
            return;
        }

        let next_line_index = at.y.saturating_add(1);
        if self.height() > next_line_index {
            let next_line = self.lines.remove(next_line_index);
            if let Some(line) = self.lines.get_mut(at.y) {
                line.append(&next_line);
            }
        }
    }

    pub const fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    pub fn get_line(&self, idx: usize) -> Option<&Line> {
        self.lines.get(idx)
    }

    pub const fn height(&self) -> usize {
        self.lines.len()
    }
}

impl From<String> for Buffer {
    fn from(value: String) -> Self {
        Self {
            lines: value.lines().map(Into::into).collect(),
        }
    }
}

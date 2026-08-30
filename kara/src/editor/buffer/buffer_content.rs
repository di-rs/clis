use super::line::Line;
use crate::editor::Location;

#[derive(Default)]
pub struct BufferContent {
    lines: Vec<Line>,
}

impl BufferContent {
    pub fn open(file_name: &str) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(file_name)?;
        let mut lines = Vec::new();
        for line in content.lines() {
            lines.push(line.into());
        }
        Ok(Self { lines })
    }

    pub fn insert_char(&mut self, char: char, at: Location) {
        if at.y > self.lines.len() {
            return;
        }
        if at.y == self.lines.len() {
            let str = char.to_string();
            self.lines.push(str.as_str().into());
        } else if let Some(line) = self.lines.get_mut(at.y) {
            line.insert_char(char, at.x);
        }
    }

    pub fn delete(&mut self, at: Location) {
        if let Some(line) = self.lines.get_mut(at.y) {
            line.delete(at.x);
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

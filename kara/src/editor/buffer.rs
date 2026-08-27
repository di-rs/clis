mod line;
mod move_caret;
use crate::editor::Coordinate;
use line::Line;

pub use move_caret::Direction;

#[derive(Default)]
pub struct Buffer {
    lines: Vec<Line>,
    text_location: Coordinate,
    max_prev_x: usize,
}

impl Buffer {
    pub fn open(file_name: &str) -> Result<Self, std::io::Error> {
        let content = std::fs::read_to_string(file_name)?;
        let mut lines = Vec::new();
        for line in content.lines() {
            lines.push(line.into());
        }
        Ok(Self {
            lines,
            text_location: Coordinate::default(),
            max_prev_x: 0,
        })
    }

    pub fn insert_char(&mut self, char: char) -> Result<(), String> {
        let line = self.lines.get_mut(self.text_location.y).ok_or("line is always accessible")?;
        let x = self.text_location.x.saturating_sub(1);
        let prev_len = line.len();

        Ok(())
    }

    pub fn caret_location(&self) -> Coordinate {
        let y = self.text_location.y;
        let x = self
            .lines
            .get(y)
            .map_or(0, |line| line.width_until(self.text_location.x));
        Coordinate { x, y }
    }

    pub const fn is_empty(&self) -> bool {
        self.lines.is_empty()
    }

    pub fn get_line(&self, idx: usize) -> Option<&Line> {
        self.lines.get(idx)
    }

    const fn height(&self) -> usize {
        self.lines.len()
    }
}

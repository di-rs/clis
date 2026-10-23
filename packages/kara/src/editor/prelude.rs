#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct Size {
    pub height: usize,
    pub width: usize,
}

#[derive(Copy, Clone)]
pub struct Position {
    pub col: usize,
    pub row: usize,
}

#[derive(Default, Clone, Copy, PartialEq, Eq)]
pub struct ScreenPosition {
    pub x: usize,
    pub y: usize,
}

impl ScreenPosition {
    pub fn saturation_sub(&self, coordinate: Self) -> Self {
        debug_assert!(self.x >= coordinate.x);
        debug_assert!(self.y >= coordinate.y);
        Self {
            x: self.x.saturating_sub(coordinate.x),
            y: self.y.saturating_sub(coordinate.y),
        }
    }
}

impl From<ScreenPosition> for Position {
    fn from(loc: ScreenPosition) -> Self {
        Self {
            col: loc.x,
            row: loc.y,
        }
    }
}

/// A position between graphemes in a logical text line, not terminal cells.
#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct TextPosition {
    pub grapheme: usize,
    pub line: usize,
}

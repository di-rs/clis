use chrono::{DateTime, Local};

#[derive(Default, Clone, Copy, Debug)]
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
pub struct Location {
    pub x: usize,
    pub y: usize,
}

impl Location {
    pub fn saturation_sub(&self, coordinate: Self) -> Self {
        debug_assert!(self.x >= coordinate.x);
        debug_assert!(self.y >= coordinate.y);
        Self {
            x: self.x.saturating_sub(coordinate.x),
            y: self.y.saturating_sub(coordinate.y),
        }
    }
}

impl From<Location> for Position {
    fn from(loc: Location) -> Self {
        Self {
            col: loc.x,
            row: loc.y,
        }
    }
}

pub type LocalTimestamp = DateTime<Local>;
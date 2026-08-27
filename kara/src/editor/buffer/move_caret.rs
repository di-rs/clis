use std::cmp::{max, min};

use super::Buffer;
use super::line::Line;

#[derive(Clone, Copy)]
pub enum Direction {
    Up(usize),
    Down(usize),
    Left(usize),
    Right(usize),
    StartOfLine,
    EndOfLine,
    StartOfBuffer,
    EndOfBuffer,
}

impl Buffer {
    pub fn move_caret(&mut self, direction: Direction) {
        match direction {
            Direction::Up(step) => self.move_up(step),
            Direction::Down(step) => self.move_down(step),
            Direction::Left(step) => self.move_left(step),
            Direction::Right(step) => self.move_right(step),
            Direction::StartOfLine => self.move_line_start(),
            Direction::EndOfLine => self.move_line_end(),
            Direction::StartOfBuffer => {
                self.text_location.y = 0;
            }
            Direction::EndOfBuffer => {
                self.text_location.y = self.lines.len().saturating_sub(1);
            }
        }
    }

    fn move_up(&mut self, step: usize) {
        self.text_location.y = self.text_location.y.saturating_sub(step);
        self.snap_to_valid_graheme();
    }

    fn move_down(&mut self, step: usize) {
        self.text_location.y = self.text_location.y.saturating_add(step);
        self.snap_to_valid_graheme();
        self.snap_to_valid_line();
    }

    fn move_left(&mut self, step: usize) {
        if self.text_location.x > 0 {
            self.text_location.x = self.text_location.x.saturating_sub(step);
            self.reset_max_x();
        } else if self.text_location.y > 0 {
            self.move_up(1);
            self.move_line_end();
        }
    }

    fn move_right(&mut self, step: usize) {
        let width = self.width_at(self.text_location.y);
        if self.text_location.x < width {
            self.text_location.x = self.text_location.x.saturating_add(step);
            self.reset_max_x();
        } else if self.text_location.y < self.height() {
            self.move_down(1);
            self.move_line_start();
        }
    }

    fn move_line_start(&mut self) {
        if let Some(line) = self.get_line(self.text_location.y) {
            let whitespace_count = line.prefix_whitespace_count();
            self.text_location.x = whitespace_count;
        } else {
            self.text_location.x = 0;
        }
        self.reset_max_x();
    }

    fn move_line_end(&mut self) {
        self.text_location.x = self.width_at(self.text_location.y);
        self.reset_max_x();
    }

    fn snap_to_valid_graheme(&mut self) {
        self.max_prev_x = max(self.text_location.x, self.max_prev_x);
        let width = self.width_at(self.text_location.y);
        self.text_location.x = min(width, self.max_prev_x);
    }

    fn snap_to_valid_line(&mut self) {
        self.text_location.y = min(self.text_location.y, self.height());
    }

    const fn reset_max_x(&mut self) {
        self.max_prev_x = self.text_location.x;
    }

    fn width_at(&self, at: usize) -> usize {
        self.lines.get(at).map_or(0, Line::len)
    }
}

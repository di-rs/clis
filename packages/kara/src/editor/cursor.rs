use super::{TextPosition, buffer::Buffer};
use std::cmp::{max, min};

#[derive(Debug, Clone, Copy)]
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

#[derive(Default)]
pub struct Cursor {
    pub position: TextPosition,
    desired_grapheme: usize,
}

impl Cursor {
    pub fn clamp_after_edit(&mut self, text: &Buffer) {
        self.snap_to_valid_line(text);
        self.position.grapheme = min(self.position.grapheme, self.current_line_width(text));
        self.reset_max_x();
    }

    pub fn move_caret(&mut self, text: &Buffer, direction: Direction) {
        match direction {
            Direction::Up(step) => self.move_up(text, step),
            Direction::Down(step) => self.move_down(text, step),
            Direction::Left(step) => self.move_left(text, step),
            Direction::Right(step) => self.move_right(text, step),
            Direction::StartOfLine => self.move_line_start(text),
            Direction::EndOfLine => self.move_line_end(text),
            Direction::StartOfBuffer => {
                self.position.line = 0;
                self.snap_to_valid_graheme(text);
            }
            Direction::EndOfBuffer => {
                self.position.line = text.height().saturating_sub(1);
                self.snap_to_valid_graheme(text);
            }
        }
    }

    fn move_up(&mut self, text: &Buffer, step: usize) {
        self.position.line = self.position.line.saturating_sub(step);
        self.snap_to_valid_graheme(text);
    }

    fn move_down(&mut self, text: &Buffer, step: usize) {
        self.position.line = self.position.line.saturating_add(step);
        self.snap_to_valid_line(text);
        self.snap_to_valid_graheme(text);
    }

    fn move_left(&mut self, text: &Buffer, step: usize) {
        if self.position.grapheme > 0 {
            self.position.grapheme = self.position.grapheme.saturating_sub(step);
            self.reset_max_x();
        } else if self.position.line > 0 {
            self.move_up(text, 1);
            self.move_line_end(text);
        }
    }

    fn move_right(&mut self, text: &Buffer, step: usize) {
        let width = self.current_line_width(text);
        if self.position.grapheme < width {
            self.position.grapheme = self.position.grapheme.saturating_add(step);
            self.reset_max_x();
        } else if self.position.line < text.height() {
            self.move_down(text, 1);
            self.move_line_start(text);
        }
    }

    fn move_line_start(&mut self, text: &Buffer) {
        if let Some(line) = text.get_line(self.position.line) {
            let whitespace_count = line.prefix_whitespace_count();
            self.position.grapheme = whitespace_count;
        } else {
            self.position.grapheme = 0;
        }
        self.reset_max_x();
    }

    fn move_line_end(&mut self, text: &Buffer) {
        self.position.grapheme = self.current_line_width(text);
        self.reset_max_x();
    }

    fn snap_to_valid_graheme(&mut self, text: &Buffer) {
        self.desired_grapheme = max(self.position.grapheme, self.desired_grapheme);
        let width = self.current_line_width(text);
        self.position.grapheme = min(width, self.desired_grapheme);
    }

    fn snap_to_valid_line(&mut self, text: &Buffer) {
        self.position.line = min(self.position.line, text.height());
    }

    fn current_line_width(&self, text: &Buffer) -> usize {
        text.get_line(self.position.line)
            .map_or(0, |line| line.len())
    }

    const fn reset_max_x(&mut self) {
        self.desired_grapheme = self.position.grapheme;
    }
}

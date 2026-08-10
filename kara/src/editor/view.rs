use std::fmt::Display;

use crate::editor::{Coordinate, Size, buffer::Buffer, terminal::Terminal};

const NAME: &str = env!("CARGO_PKG_NAME");
const VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct View {
    needs_redraw: bool,
    pub size: Size,
    pub scroll_offset: Coordinate,
}

const Y_OVERSCAN: usize = 5;
const X_OVERSCAN: usize = 5;

impl View {
    pub fn new() -> Self {
        Self {
            needs_redraw: true,
            size: Terminal::size().unwrap_or_default(),
            scroll_offset: Coordinate::default(),
        }
    }

    pub const fn resize(&mut self, to: Size) {
        self.size = to;
        self.needs_redraw = true;
    }

    pub fn render(&mut self, buffer: &Buffer) {
        if !self.needs_redraw {
            return;
        }

        let Size { height, width } = self.size;
        if height == 0 || width == 0 {
            return;
        }

        let top = self.scroll_offset.y;

        for i in 0..height {
            match buffer.get_line(i.saturating_add(top)) {
                Some(line) => {
                    let left = self.scroll_offset.x;
                    let right = self.scroll_offset.x.saturating_add(width);
                    Self::render_line(i, line.get(left..right));
                }
                None => Self::render_line(i, "~"),
            }
        }

        if buffer.is_empty() {
            self.draw_welcome_message();
        }

        self.needs_redraw = false;
    }

    pub const fn scroll_into_view(&mut self, current_location: Coordinate) {
        let Coordinate { x, y } = current_location;

        self.scroll_vertically(y);
        self.scroll_horizontally(x);
    }

    const fn scroll_horizontally(&mut self, to: usize) {
        let Size { width, .. } = self.size;

        let mut offset_changed = false;

        let start = self.scroll_offset.x.saturating_add(X_OVERSCAN);
        let end = self
            .scroll_offset
            .x
            .saturating_add(width)
            .saturating_sub(X_OVERSCAN);

        if self.scroll_offset.x > 0 && to < start {
            self.scroll_offset.x = to.saturating_sub(X_OVERSCAN);
            offset_changed = true;
        } else if to >= end {
            self.scroll_offset.x = to
                .saturating_sub(width.saturating_sub(X_OVERSCAN))
                .saturating_add(1);
            offset_changed = true;
        }

        self.needs_redraw = self.needs_redraw || offset_changed;
    }

    const fn scroll_vertically(&mut self, to: usize) {
        let Size { height, .. } = self.size;

        let mut offset_changed = false;

        let top = self.scroll_offset.y.saturating_add(Y_OVERSCAN);
        let bottom = self
            .scroll_offset
            .y
            .saturating_add(height)
            .saturating_sub(Y_OVERSCAN);

        if self.scroll_offset.y > 0 && to < top {
            self.scroll_offset.y = to.saturating_sub(Y_OVERSCAN);
            offset_changed = true;
        } else if to >= bottom {
            self.scroll_offset.y = to
                .saturating_sub(height.saturating_sub(Y_OVERSCAN))
                .saturating_add(1);
            offset_changed = true;
        }

        self.needs_redraw = self.needs_redraw || offset_changed;
    }

    fn draw_welcome_message(&self) {
        let Size { height, width } = self.size;

        let welcome_message = format!("{NAME} v{VERSION}");
        let message_len = welcome_message.len();

        let y = height / 3;
        let padding = width.saturating_sub(message_len).saturating_sub(1) / 2;

        let mut message = format!("~{}{}", " ".repeat(padding), welcome_message);
        message.truncate(width);

        Self::render_line(y, message);
    }

    fn render_line(at: usize, line_text: impl Display) {
        let result = Terminal::print_row(at, line_text);
        debug_assert!(result.is_ok(), "Failed to render line");
    }
}

use std::{
    fmt::Display,
    time::{Duration, Instant},
};

use crate::editor::terminal::Terminal;

const MESSAGE_DURATION: Duration = Duration::new(5, 0);

struct Message {
    content: String,
    time: Instant,
}

impl Message {
    fn new(content: String) -> Self {
        Self {
            content,
            time: Instant::now(),
        }
    }

    fn is_expired(&self) -> bool {
        Instant::now().duration_since(self.time) > MESSAGE_DURATION
    }
}

impl Default for Message {
    fn default() -> Self {
        Self {
            content: String::new(),
            time: Instant::now(),
        }
    }
}

impl Display for Message {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.is_expired() {
            write!(f, "")
        } else {
            write!(f, "{}", self.content)
        }
    }
}

#[derive(Default)]
pub struct MessageBar {
    current_message: Message,
    needs_redraw: bool,
    cleared_after_expiration: bool,
}

impl MessageBar {
    pub fn set(&mut self, new_message: String) {
        self.current_message = Message::new(new_message);
        self.mark_redraw(true);
        self.cleared_after_expiration = false;
    }

    pub fn clear(&mut self) {
        self.set(String::new());
    }

    pub fn render(&mut self, position_y: usize) {
        if !self.needs_render() {
            return;
        }
        if self.current_message.is_expired() {
            self.cleared_after_expiration = true;
        }
        _ = Terminal::print_row(position_y, &self.current_message);
        self.mark_redraw(false);
    }

    fn needs_render(&self) -> bool {
        self.needs_redraw || (!self.cleared_after_expiration && self.current_message.is_expired())
    }

    const fn mark_redraw(&mut self, value: bool) {
        self.needs_redraw = value;
    }
}

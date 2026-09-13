use super::terminal::Terminal;

#[derive(Default)]
pub struct MessageBar {
    current_message: String,
    needs_redraw: bool,
}

impl MessageBar {
    pub fn update_message(&mut self, new_message: String) {
        if new_message != self.current_message {
            self.current_message = new_message;
            self.mark_redraw(true);
        }
    }

    pub fn render(&mut self, position_y: usize) {
        if !self.needs_redraw {
            return;
        }
        _ = Terminal::print_row(position_y, &self.current_message);
        self.mark_redraw(false);
    }

    const fn mark_redraw(&mut self, value: bool) {
        self.needs_redraw = value;
    }
}

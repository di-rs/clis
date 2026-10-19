use std::{cmp::min, path::Path, range::Range};

use crate::editor::{
    Size,
    commandbar::interpreter::Interpreter,
    editorcommand::{Edit, EditorCommand},
    line::Line,
    terminal::Terminal,
};

mod interpreter;

#[derive(Default)]
pub struct CommandBar {
    prompt: String,
    value: Line,
    size: Size,
}

impl CommandBar {
    pub fn new(value: &str) -> Self {
        Self {
            prompt: ":".to_owned(),
            value: value.parse().unwrap(),
            size: Terminal::size().unwrap_or_default(),
        }
    }

    pub const fn resize(&mut self, size: Size) {
        self.size = size;
    }

    pub fn render(&self, position_y: usize) {
        let area_for_value = self.size.width.saturating_sub(self.prompt.len());
        let value_end = self.value.width();
        let value_start = value_end.saturating_sub(area_for_value);
        let message = format!(
            "{}{}",
            self.prompt,
            self.value.get(Range::from(value_start..value_end))
        );
        let to_print = if message.len() <= self.size.width {
            message
        } else {
            String::new()
        };

        _ = Terminal::print_row(position_y, to_print);
    }

    pub fn handle_edit(&mut self, edit_command: &Edit) {
        match edit_command {
            Edit::Insert(char) => self.value.append_char(*char),
            Edit::DeleteBackward => self.value.delete_last(),
            _ => {}
        }
    }

    pub fn produce_command(
        &mut self,
        current_file: Option<&Path>,
    ) -> Result<EditorCommand, String> {
        let cmd = Interpreter::into_command(&self.value, current_file);
        self.value = Line::default();
        cmd
    }

    pub fn caret_position_col(&self) -> usize {
        min(
            self.prompt.len().saturating_add(self.value.len()),
            self.size.width,
        )
    }
}

use std::{cmp::min, path::Path, range::Range};

use crate::editor::{
    Size,
    command::{Edit, EditorCommand},
    command_bar::interpreter::Interpreter,
    line_layout::LineLayout,
};

mod interpreter;

#[derive(Default)]
pub struct CommandBar {
    prompt: String,
    value: LineLayout,
    size: Size,
}

impl CommandBar {
    pub fn new(value: &str, size: Size) -> Self {
        Self {
            prompt: ":".to_owned(),
            value: value.parse().unwrap(),
            size,
        }
    }

    pub const fn resize(&mut self, size: Size) {
        self.size = size;
    }

    pub fn text(&self) -> String {
        let area_for_value = self
            .size
            .width
            .saturating_sub(self.prompt.len())
            .saturating_sub(1);
        let value_end = self.value.width();
        let value_start = value_end.saturating_sub(area_for_value);
        format!(
            "{}{}",
            self.prompt,
            self.value.get(Range::from(value_start..value_end))
        )
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
        self.value = LineLayout::default();
        cmd
    }

    pub fn caret_position_col(&self) -> usize {
        min(
            self.prompt.len().saturating_add(self.value.width()),
            self.size.width.saturating_sub(1),
        )
    }
}

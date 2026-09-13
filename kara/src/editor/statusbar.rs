use crate::editor::{
    Size, documentstatus::DocumentStatus, editor_buffer::EditorBuffer, editormode::EditorMode,
    terminal::Terminal,
};

const MARGIN_BOTTOM: usize = 0;

pub struct StatusBar {
    width: usize,
    margin_bottom: usize,
    position_y: usize,
    is_visible: bool,
}

impl StatusBar {
    pub fn new() -> Self {
        let size = Terminal::size().unwrap_or_default();
        let margin_bottom = MARGIN_BOTTOM;
        let mut statusbar = Self {
            width: size.width,
            margin_bottom,
            position_y: 0,
            is_visible: false,
        };
        statusbar.resize(size);
        statusbar
    }

    pub fn resize(&mut self, size: Size) {
        self.width = size.width;

        let new_position = size
            .height
            .checked_sub(self.margin_bottom)
            .and_then(|x| x.checked_sub(1));

        self.is_visible = new_position.is_some();
        self.position_y = new_position.unwrap_or(0);
    }

    pub fn render(&self, buffer: &EditorBuffer, mode: EditorMode) {
        if !self.is_visible {
            return;
        }
        let Ok(size) = Terminal::size() else {
            return;
        };

        let status: DocumentStatus = buffer.into();

        let filename = &status.filename;
        let file_status = status.modified_indicator_to_string();
        let position = status.position_indicator_to_string();

        let left = format!("{filename} {file_status}");
        let right = format!("{mode} | {position}");

        let remainder_len = size.width.saturating_sub(left.len()).saturating_sub(1);

        let status = format!("{left}{right:>remainder_len$}");

        let to_print = if status.len() <= size.width {
            status
        } else {
            String::new()
        };

        let result = Terminal::print_inverted_row(self.position_y, to_print);
        debug_assert!(result.is_ok(), "Failed to render status bar");
    }
}

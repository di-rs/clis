use crate::editor::{
    Size, documentstatus::DocumentStatus, editorbuffer::EditorBuffer, editormode::EditorMode,
    terminal::Terminal,
};

#[derive(Default)]
pub struct StatusBar {
    size: Size,
}

impl StatusBar {
    pub const fn resize(&mut self, size: Size) {
        self.size = size;
    }

    pub fn render(&self, position_y: usize, buffer: &EditorBuffer, mode: EditorMode) {
        let Size { width, .. } = self.size;

        let status: DocumentStatus = buffer.into();

        let filename = &status.filename;
        let file_status = status.modified_indicator_to_string();
        let position = status.position_indicator_to_string();

        let left = format!("{filename} {file_status}");
        let right = format!("{mode} | {position}");

        let remainder_len = width.saturating_sub(left.len()).saturating_sub(1);

        let status = format!("{left}{right:>remainder_len$}");

        let to_print = if status.len() <= width {
            status
        } else {
            String::new()
        };

        let result = Terminal::print_inverted_row(position_y, to_print);
        debug_assert!(result.is_ok(), "Failed to render status bar");
    }
}

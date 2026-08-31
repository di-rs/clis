use crate::editor::{
    Size, editor_buffer::EditorBuffer, editormode::EditorMode, terminal::Terminal,
};

const MARGIN_BOTTOM: usize = 1;

pub struct StatusBar {
    width: usize,
    margin_bottom: usize,
    position_y: usize,
}

impl StatusBar {
    pub fn new() -> Self {
        let Size { height, width } = Terminal::size().unwrap_or_default();
        let margin_bottom = MARGIN_BOTTOM;
        Self {
            width,
            margin_bottom,
            position_y: height.saturating_sub(margin_bottom).saturating_sub(1),
        }
    }

    pub const fn resize(&mut self, size: Size) {
        self.width = size.width;
        self.position_y = size
            .height
            .saturating_sub(self.margin_bottom)
            .saturating_sub(1);
    }

    pub fn render(&self, buffer: &EditorBuffer, mode: EditorMode) {
        let DocumentStatus {
            total_lines,
            current_line_index,
            filename,
            is_modified,
        } = buffer.into();

        let filename_status = filename.map_or_else(String::new, |filename| {
            format!("| {filename} {}", if is_modified { "(modified)" } else { "" })
        });

        let mut status = format!("{mode} | {current_line_index}:{total_lines} {filename_status}");
        status.truncate(self.width);

        let result = Terminal::print_row(self.position_y, &status);
        debug_assert!(result.is_ok(), "Failed to render status bar");
    }
}

#[derive(Default, Eq, PartialEq, Debug)]
pub struct DocumentStatus {
    total_lines: usize,
    current_line_index: usize,
    is_modified: bool,
    filename: Option<String>,
}

impl From<&EditorBuffer> for DocumentStatus {
    fn from(buffer: &EditorBuffer) -> Self {
        let total_lines = buffer.height();
        let current_line_index = buffer.caret_location().y;
        let is_modified = buffer.modified_at.is_some();
        let filename = buffer.filename().map(std::borrow::ToOwned::to_owned);

        Self {
            total_lines,
            current_line_index,
            is_modified,
            filename,
        }
    }
}

use crossterm::cursor::{self, SetCursorStyle::{BlinkingBar, BlinkingBlock}};

#[derive(Debug, Clone, Copy)]
pub enum EditorMode {
    View,
    Edit
}

impl EditorMode {
    pub const fn get_cursor_style(self) -> cursor::SetCursorStyle {
        match self {
            Self::View => BlinkingBlock,
            Self::Edit => BlinkingBar,
        }
    }
}
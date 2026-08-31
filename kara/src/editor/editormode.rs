use std::fmt::Display;

use crossterm::cursor::{
    self,
    SetCursorStyle::{BlinkingBar, BlinkingBlock},
};

use crate::editor::editor_buffer::Direction;

#[derive(Debug, Clone, Copy)]
pub enum Placement {
    Start,
    End,
    Left,
    Right,
}

#[derive(Debug, Clone, Copy)]
pub enum EditorMode {
    View,
    Edit(Placement),
}

impl EditorMode {
    pub const fn get_cursor_style(self) -> cursor::SetCursorStyle {
        match self {
            Self::View => BlinkingBlock,
            Self::Edit(_) => BlinkingBar,
        }
    }

    pub const fn change_mode_movement(self, other: Self) -> Option<Direction> {
        match (self, other) {
            (Self::View, Self::Edit(Placement::End)) => Some(Direction::EndOfLine),
            (Self::View, Self::Edit(Placement::Start)) => Some(Direction::StartOfLine),
            (Self::View, Self::Edit(Placement::Right)) => Some(Direction::Right(1)),

            (Self::Edit(Placement::End | Placement::Right), Self::View) => Some(Direction::Left(1)),

            (_, _) => None,
        }
    }
}

impl Display for EditorMode {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let name = match self {
            Self::View => "NAVIGATION",
            Self::Edit(_) => "EDIT",
        };
        f.write_str(name)
    }
}

use crossterm::event::{
    Event,
    KeyCode::{self, Char},
    KeyEvent, KeyModifiers,
};

use crate::editor::{
    Size,
    editormode::{EditorMode, Placement},
};

#[derive(Debug)]
pub enum Direction {
    Up,
    Left,
    Right,
    Down,
    PageUp,
    PageDown,
    LineStart,
    LineEnd,
    Home,
    End,
}

#[derive(Debug)]
pub enum EditorCommand {
    Move(Direction),
    Resize(Size),
    ChangeMode(EditorMode),
    Insert(char),
    Delete,
    Backspace,
    Enter,
    Save,
    Quit,
    Unknown,
}

impl EditorCommand {
    pub fn from_event(event: &Event, mode: EditorMode) -> Result<Self, String> {
        let common_command = Self::match_common_event_mode(event);
        common_command.map_or_else(
            || match mode {
                EditorMode::View => Ok(Self::match_event_view_mode(event)),
                EditorMode::Edit(_) => Ok(Self::match_event_edit_mode(event)),
            },
            Ok,
        )
    }

    fn match_common_event_mode(event: &Event) -> Option<Self> {
        match event {
            Event::Key(KeyEvent {
                code, modifiers, ..
            }) => match (code, modifiers) {
                (Char('q'), &KeyModifiers::CONTROL) => Some(Self::Quit),
                (Char('s'), &KeyModifiers::CONTROL) => Some(Self::Save),
                
                (KeyCode::Up, _) => Some(Self::Move(Direction::Up)),
                (KeyCode::Down, _) => Some(Self::Move(Direction::Down)),
                (KeyCode::Left, _) => Some(Self::Move(Direction::Left)),
                (KeyCode::Right, _) => Some(Self::Move(Direction::Right)),
                _ => None,
            },
            Event::Resize(width, height) => Some(Self::Resize(Size {
                height: (*height).into(),
                width: (*width).into(),
            })),
            _ => None,
        }
    }

    const fn match_event_view_mode(event: &Event) -> Self {
        match event {
            Event::Key(KeyEvent {
                code, modifiers, ..
            }) => match (code, modifiers) {
                (Char('u'), _) => Self::Move(Direction::Up),
                (Char('j'), _) | (KeyCode::Enter, &KeyModifiers::NONE) => {
                    Self::Move(Direction::Down)
                }
                (Char('h'), _) => Self::Move(Direction::Left),
                (Char('k'), _) => Self::Move(Direction::Right),
                (Char('s'), _) => Self::Move(Direction::LineStart),
                (Char('e'), _) => Self::Move(Direction::LineEnd),
                (Char('g'), _) => Self::Move(Direction::Home),
                (Char('G'), _) => Self::Move(Direction::End),
                (Char('p'), _) => Self::Move(Direction::PageUp),
                (Char('P'), _) => Self::Move(Direction::PageDown),

                (Char('i'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Left)),
                (Char('a'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Right)),
                (Char('I'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Start)),
                (Char('A'), _) => Self::ChangeMode(EditorMode::Edit(Placement::End)),

                (Char('d'), _) => Self::Delete,
                _ => Self::Unknown,
            },
            _ => Self::Unknown,
        }
    }

    const fn match_event_edit_mode(event: &Event) -> Self {
        match event {
            Event::Key(KeyEvent {
                code, modifiers, ..
            }) => match (code, modifiers) {
                (Char('c'), &KeyModifiers::CONTROL) | (KeyCode::Esc, _) => {
                    Self::ChangeMode(EditorMode::View)
                }
                (Char(char), &KeyModifiers::NONE | &KeyModifiers::SHIFT) => Self::Insert(*char),
                (KeyCode::Tab, &KeyModifiers::NONE) => Self::Insert('\t'),
                (KeyCode::Enter, &KeyModifiers::NONE) => Self::Enter,
                (KeyCode::Backspace, _) => Self::Backspace,
                _ => Self::Unknown,
            },
            _ => Self::Unknown,
        }
    }
}

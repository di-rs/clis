use crossterm::event::{
    Event,
    KeyCode::{self, Char},
    KeyEvent, KeyModifiers,
};

use crate::editor::Size;

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

pub enum EditorCommand {
    Move(Direction),
    Resize(Size),
    // ChangeMode(EditorMode),
    Quit,
    UnknownEvent,
    UnknownCode,
}

impl TryFrom<Event> for EditorCommand {
    type Error = String;

    fn try_from(event: Event) -> Result<Self, Self::Error> {
        match event {
            Event::Key(KeyEvent {
                code, modifiers, ..
            }) => match (code, modifiers) {
                (Char('q'), KeyModifiers::CONTROL) => Ok(Self::Quit),
                (KeyCode::Up | Char('u'), _) => Ok(Self::Move(Direction::Up)),
                (KeyCode::Down | Char('j'), _) => Ok(Self::Move(Direction::Down)),
                (KeyCode::Left | Char('h'), _) => Ok(Self::Move(Direction::Left)),
                (KeyCode::Right | Char('k'), _) => Ok(Self::Move(Direction::Right)),
                (Char('s'), _) => Ok(Self::Move(Direction::LineStart)),
                (Char('e'), _) => Ok(Self::Move(Direction::LineEnd)),
                (Char('g'), _) => Ok(Self::Move(Direction::Home)),
                (Char('G'), _) => Ok(Self::Move(Direction::End)),
                (Char('p'), _) => Ok(Self::Move(Direction::PageUp)),
                (Char('P'), _) => Ok(Self::Move(Direction::PageDown)),
                _ => Ok(Self::UnknownCode),
            },
            Event::Resize(width, height) => Ok(Self::Resize(Size {
                height: height.into(),
                width: width.into(),
            })),
            _ => Ok(Self::UnknownEvent),
        }
    }
}

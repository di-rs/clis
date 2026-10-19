use crossterm::event::{
    Event,
    KeyCode::{self, Char},
    KeyEvent, KeyModifiers,
};

use crate::editor::{
    Size,
    editormode::{EditorMode, Placement},
};

#[derive(Debug, PartialEq, Eq)]
pub enum Move {
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

#[derive(Debug, PartialEq, Eq)]
pub enum System {
    Save,
    SaveAs(String),
    Quit,
    QuitForce,
    Resize(Size),
}

impl TryFrom<KeyEvent> for System {
    type Error = String;

    fn try_from(event: KeyEvent) -> Result<Self, Self::Error> {
        match (event.code, event.modifiers) {
            (Char('q'), KeyModifiers::CONTROL) => Ok(Self::Quit),
            (Char('s'), KeyModifiers::CONTROL) => Ok(Self::Save),

            _ => Err(format!(
                "Unsupported key code {:?} with modifiers {:?}",
                event.code, event.modifiers
            )),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum Edit {
    Insert(char),
    InsertNewline,
    Delete,
    DeleteBackward,
}

impl TryFrom<KeyEvent> for Edit {
    type Error = String;

    fn try_from(event: KeyEvent) -> Result<Self, Self::Error> {
        match (event.code, event.modifiers) {
            (Char(char), KeyModifiers::NONE | KeyModifiers::SHIFT) => Ok(Self::Insert(char)),

            (KeyCode::Tab, KeyModifiers::NONE) => Ok(Self::Insert('\t')),
            (KeyCode::Enter, KeyModifiers::NONE) => Ok(Self::InsertNewline),
            (KeyCode::Backspace, _) => Ok(Self::DeleteBackward),
            (KeyCode::Delete, _) => Ok(Self::Delete),

            _ => Err(format!(
                "Unsupported key code {:?} with modifiers {:?}",
                event.code, event.modifiers
            )),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum EditorCommand {
    Move(Move),
    ChangeMode(EditorMode),
    Edit(Edit),
    System(System),
    Unknown,
}

impl EditorCommand {
    pub fn from_event(event: &Event, mode: EditorMode) -> Result<Self, String> {
        let common_command = Self::match_common_event_mode(event);
        common_command.map_or_else(
            || match mode {
                EditorMode::View => Ok(Self::match_event_view_mode(event)),
                EditorMode::Edit(_) | EditorMode::Command => Ok(Self::match_event_edit_mode(event)),
            },
            Ok,
        )
    }

    fn match_common_event_mode(event: &Event) -> Option<Self> {
        match *event {
            Event::Key(key_event) => {
                if let Ok(system) = System::try_from(key_event) {
                    return Some(Self::System(system));
                }
                match (key_event.code, key_event.modifiers) {
                    (KeyCode::Up, _) => Some(Self::Move(Move::Up)),
                    (KeyCode::Down, _) => Some(Self::Move(Move::Down)),
                    (KeyCode::Left, _) => Some(Self::Move(Move::Left)),
                    (KeyCode::Right, _) => Some(Self::Move(Move::Right)),
                    _ => None,
                }
            }
            Event::Resize(width, height) => Some(Self::System(System::Resize(Size {
                height: (height).into(),
                width: (width).into(),
            }))),
            _ => None,
        }
    }

    const fn match_event_view_mode(event: &Event) -> Self {
        match *event {
            Event::Key(KeyEvent {
                code, modifiers, ..
            }) => match (code, modifiers) {
                (Char('u'), _) => Self::Move(Move::Up),
                (Char('j'), _) | (KeyCode::Enter, KeyModifiers::NONE) => Self::Move(Move::Down),
                (Char('h'), _) => Self::Move(Move::Left),
                (Char('k'), _) => Self::Move(Move::Right),
                (Char('s'), _) => Self::Move(Move::LineStart),
                (Char('e'), _) => Self::Move(Move::LineEnd),
                (Char('g'), _) => Self::Move(Move::Home),
                (Char('G'), _) => Self::Move(Move::End),
                (Char('p'), _) => Self::Move(Move::PageUp),
                (Char('P'), _) => Self::Move(Move::PageDown),

                (Char('i'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Left)),
                (Char('a'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Right)),
                (Char('I'), _) => Self::ChangeMode(EditorMode::Edit(Placement::Start)),
                (Char('A'), _) => Self::ChangeMode(EditorMode::Edit(Placement::End)),

                (Char(':'), _) => Self::ChangeMode(EditorMode::Command),

                (Char('d'), _) => Self::Edit(Edit::Delete),
                _ => Self::Unknown,
            },
            _ => Self::Unknown,
        }
    }

    fn match_event_edit_mode(event: &Event) -> Self {
        match *event {
            Event::Key(key_event) => {
                match (key_event.code, key_event.modifiers) {
                    (Char('c'), KeyModifiers::CONTROL) | (KeyCode::Esc, _) => {
                        return Self::ChangeMode(EditorMode::View);
                    }
                    _ => {}
                }
                Edit::try_from(key_event).map_or(Self::Unknown, Self::Edit)
            }
            _ => Self::Unknown,
        }
    }
}

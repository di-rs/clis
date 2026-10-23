use super::{Size, mode::EditorMode};

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

#[derive(Debug, PartialEq, Eq)]
pub enum Edit {
    Insert(char),
    InsertNewline,
    Delete,
    DeleteBackward,
}

#[derive(Debug, PartialEq, Eq)]
pub enum EditorCommand {
    Move(Move),
    ChangeMode(EditorMode),
    Edit(Edit),
    System(System),
    Unknown,
}

use buffer::{Buffer, Direction};
use crossterm::event::{Event, KeyEvent, KeyEventKind, read};
use terminal::Terminal;
use view::View;

mod buffer;
mod editorcommand;
mod terminal;
mod view;

mod prelude;
pub use prelude::*;

use crate::editor::editorcommand::EditorCommand;

pub struct Editor {
    should_quit: bool,
    view: View,
    buffer: Buffer,
}

impl Editor {
    pub fn builder() -> EditorBuilder {
        EditorBuilder::default()
    }

    fn new(file_name: Option<String>) -> Result<Self, std::io::Error> {
        let current_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |panic_info| {
            let _ = Terminal::terminate();
            current_hook(panic_info);
        }));

        Terminal::initialize()?;
        let buffer = file_name
            .and_then(|file_name| Buffer::open(&file_name).ok())
            .unwrap_or_default();

        Ok(Self {
            should_quit: false,
            view: View::new(),
            buffer,
        })
    }

    pub fn run(&mut self) {
        loop {
            self.refresh_screen();
            if self.should_quit {
                break;
            }

            match read() {
                Ok(event) => self.evaluate_event(event),
                Err(err) => {
                    debug_assert!(false, "Could not read event: {err:?}");
                }
            }
        }
    }

    fn refresh_screen(&mut self) {
        let _ = Terminal::hide_caret();

        let caret_location = self.buffer.caret_location();

        self.view.scroll_into_view(caret_location);
        self.view.render(&self.buffer);

        let location = caret_location.saturation_sub(self.view.scroll_offset);
        let _ = Terminal::move_to(location.into());

        let _ = Terminal::show_caret();
        let _ = Terminal::execute();
    }

    fn evaluate_event(&mut self, event: Event) {
        let should_process = match &event {
            Event::Key(KeyEvent { kind, .. }) => kind == &KeyEventKind::Press,
            Event::Resize(_, _) => true,
            _ => false,
        };

        if should_process {
            match EditorCommand::try_from(event) {
                Ok(command) => self.handle_command(command),
                Err(err) => {
                    debug_assert!(false, "Could not handle command: {err}");
                }
            }
        }
    }

    fn handle_command(&mut self, command: EditorCommand) {
        match command {
            EditorCommand::Move(direction) => {
                self.move_caret(&direction);
            }
            EditorCommand::Resize(size) => {
                self.view.resize(size);
            }
            EditorCommand::Quit => {
                self.should_quit = true;
            }
            EditorCommand::UnknownEvent | EditorCommand::UnknownCode => (),
        }
    }

    fn move_caret(&mut self, direction: &editorcommand::Direction) {
        use editorcommand::Direction::{
            Down, End, Home, Left, LineEnd, LineStart, PageDown, PageUp, Right, Up,
        };

        match direction {
            Up => self.buffer.move_caret(Direction::Up(1)),
            Down => self.buffer.move_caret(Direction::Down(1)),
            Left => self.buffer.move_caret(Direction::Left(1)),
            Right => self.buffer.move_caret(Direction::Right(1)),
            LineStart => self.buffer.move_caret(Direction::StartOfLine),
            LineEnd => self.buffer.move_caret(Direction::EndOfLine),
            Home => self.buffer.move_caret(Direction::StartOfBuffer),
            End => self.buffer.move_caret(Direction::EndOfBuffer),
            PageUp => {
                let step = self.view.size.height / 2;
                self.buffer.move_caret(Direction::Up(step));
            }
            PageDown => {
                let step = self.view.size.height / 2;
                self.buffer.move_caret(Direction::Down(step));
            }
        }
    }
}

impl Drop for Editor {
    fn drop(&mut self) {
        let _ = Terminal::terminate();
        if self.should_quit {
            let _ = Terminal::move_to(Position { col: 0, row: 0 });
            let _ = Terminal::print("Goodbye.\r\n");
        }
    }
}

#[derive(Default)]
pub struct EditorBuilder {
    file_name: Option<String>,
}

impl EditorBuilder {
    pub fn file(mut self, file_name: Option<&String>) -> Self {
        self.file_name = file_name.cloned();
        self
    }

    pub fn build(self) -> Result<Editor, std::io::Error> {
        Editor::new(self.file_name)
    }
}

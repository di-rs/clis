use crossterm::event::{Event, KeyEvent, KeyEventKind, read};
use editorbuffer::{Direction, EditorBuffer};
use terminal::Terminal;
use view::View;

mod commandbar;
mod documentstatus;
mod editorbuffer;
mod editorcommand;
mod editormode;
mod line;
mod messagebar;
mod statusbar;
mod terminal;
mod view;

mod prelude;
pub use prelude::*;

use crate::editor::{
    commandbar::CommandBar,
    editorcommand::{
        Edit,
        EditorCommand::{self, System},
        Move,
        System::{Quit, QuitForce, Resize, Save, SaveAs},
    },
    editormode::EditorMode,
    messagebar::MessageBar,
    statusbar::StatusBar,
};

pub const APP_NAME: &str = env!("CARGO_PKG_NAME");
const MAX_QUIT_ATTEMPTS: u8 = 2;

#[derive(Default)]
pub struct Editor {
    title: String,
    terminal_size: Size,
    mode: EditorMode,
    should_quit: bool,
    quit_attempts: Option<u8>,
    buffer: EditorBuffer,
    view: View,
    statusbar: StatusBar,
    messagebar: MessageBar,
    commandbar: Option<CommandBar>,
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
        let mut editor = Self::default();

        let size = Terminal::size().unwrap_or_default();
        editor.resize(size);
        editor
            .messagebar
            .set("Ctrl-s = save | Ctrl-q = quit".to_string());

        if let Some(filename) = file_name {
            editor.open_file(&filename);
        }

        Ok(editor)
    }

    fn open_file(&mut self, file_name: &str) {
        match EditorBuffer::open(file_name) {
            Ok(buffer) => self.buffer = buffer,
            Err(e) => {
                self.messagebar
                    .set(format!("ERR: Could not open file `{file_name}` {e}"));
            }
        }
    }

    pub fn run(&mut self) {
        loop {
            self.refresh_screen();
            // TODO: Do it only if buffer was changed
            self.refresh_title();
            if self.should_quit {
                break;
            }

            match read() {
                Ok(event) => self.evaluate_event(&event),
                Err(err) => {
                    debug_assert!(false, "Could not read event: {err:?}");
                }
            }
        }
    }

    fn refresh_title(&mut self) {
        let title = self.buffer.filename().map_or_else(
            || APP_NAME.to_string(),
            |p| format!("{} - {APP_NAME}", p.display()),
        );

        if title != self.title && matches!(Terminal::set_title(&title), Ok(())) {
            self.title = title;
        }
    }

    fn refresh_screen(&mut self) {
        let Size { width, height } = self.terminal_size;
        if height == 0 || width == 0 {
            return;
        }
        let _ = Terminal::hide_caret();

        let caret_location = self.buffer.caret_location();
        self.view.scroll_into_view(caret_location);

        let bottom_bar_row = height.saturating_sub(1);
        if let Some(commandbar) = &mut self.commandbar {
            commandbar.render(bottom_bar_row);
        } else {
            self.messagebar.render(bottom_bar_row);
        }
        if height > 1 {
            self.view.render(&self.buffer);
        }
        if height > 2 {
            let statusbar_pos = height.saturating_sub(2);
            self.statusbar
                .render(statusbar_pos, &self.buffer, self.mode);
        }

        let location = if let Some(commandbar) = &mut self.commandbar {
            Location {
                x: commandbar.caret_position_col(),
                y: bottom_bar_row,
            }
        } else {
            caret_location.saturation_sub(self.view.scroll_offset)
        };

        let _ = Terminal::move_to(location.into());
        let _ = Terminal::show_caret(self.mode.get_cursor_style());
        let _ = Terminal::execute();
    }

    fn evaluate_event(&mut self, event: &Event) {
        let should_process = match &event {
            Event::Key(KeyEvent { kind, .. }) => kind == &KeyEventKind::Press,
            Event::Resize(_, _) => true,
            _ => false,
        };

        if should_process {
            match EditorCommand::from_event(event, self.mode) {
                Ok(command) => self.handle_command(command),
                Err(err) => {
                    debug_assert!(false, "Could not handle command: {err}");
                }
            }
        }
    }

    fn handle_command(&mut self, command: EditorCommand) {
        match command {
            System(Quit) => self.quit(false),
            System(QuitForce) => self.quit(true),
            System(Resize(size)) => self.resize(size),
            _ => self.reset_quit_attempts(),
        }

        match command {
            System(Quit | QuitForce | Resize(_)) => {}
            System(Save) => self.save(),
            System(SaveAs(filename)) => {
                let _ = self.buffer.save_as(&filename);
            }
            EditorCommand::Move(direction) => self.handle_move_command(&direction),
            EditorCommand::Edit(edit) => self.handle_edit_command(&edit),
            EditorCommand::ChangeMode(mode) => {
                self.change_mode(mode);
            }
            EditorCommand::Unknown => (),
        }
    }

    fn handle_move_command(&mut self, direction: &Move) {
        use editorcommand::Move::{
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
                let step = self.terminal_size.height / 2;
                self.buffer.move_caret(Direction::Up(step));
            }
            PageDown => {
                let step = self.terminal_size.height / 2;
                self.buffer.move_caret(Direction::Down(step));
            }
        }
    }

    fn handle_edit_command(&mut self, edit_command: &Edit) {
        match self.mode {
            EditorMode::Edit(_) => match edit_command {
                Edit::Insert(char) => self.buffer.insert_char(*char),
                Edit::InsertNewline => self.buffer.insert_newline(),
                Edit::Delete => self.buffer.delete(),
                Edit::DeleteBackward => self.buffer.delete_backward(),
            },
            EditorMode::Command => {
                if let Some(commandbar) = &mut self.commandbar {
                    match edit_command {
                        Edit::InsertNewline => {
                            match commandbar.produce_command(self.buffer.filename()) {
                                Err(e) => self.messagebar.set(e),
                                Ok(command) => self.handle_command(command),
                            }
                            self.change_mode(EditorMode::View);
                        }
                        edit => commandbar.handle_edit(edit),
                    }
                }
            }
            EditorMode::View => {}
        }
    }

    const fn resize(&mut self, size: Size) {
        self.terminal_size = size;
        let Size { height, width } = size;
        self.view.resize(Size {
            height: height.saturating_sub(2),
            width,
        });
        self.statusbar.resize(Size { height: 1, width });
        if let Some(commandbar) = &mut self.commandbar {
            commandbar.resize(Size { height: 1, width });
        }
    }

    fn change_mode(&mut self, mode: EditorMode) {
        match mode {
            EditorMode::Command => self.commandbar = Some(CommandBar::new("")),
            mode => {
                self.commandbar = None;
                if let Some(direction) = self.mode.change_mode_movement(mode) {
                    self.buffer.move_caret(direction);
                }
            }
        }
        self.mode = mode;
    }

    fn save(&mut self) {
        if self.buffer.has_file() {
            match self.buffer.save() {
                Ok(()) => self.messagebar.set("File saved successfully.".to_owned()),
                Err(e) => self.messagebar.set(format!("Error writing file! {e}")),
            }
        } else {
            self.commandbar = Some(CommandBar::new("w "));
            self.mode = EditorMode::Command;
        }
    }

    fn quit(&mut self, force: bool) {
        if !self.buffer.is_modified() || force {
            self.should_quit = true;
            return;
        }

        let attempt = self.quit_attempts.map_or(1, |x| x.saturating_add(1));
        let left_attempts = MAX_QUIT_ATTEMPTS.saturating_sub(attempt);

        if left_attempts == 0 {
            self.should_quit = true;
        } else {
            self.messagebar.set(format!(
                "WARNING! File has unsaved changes. Press Ctrl-Q {left_attempts} more times to quit."
            ));
            self.quit_attempts = Some(attempt);
        }
    }

    fn reset_quit_attempts(&mut self) {
        if self.quit_attempts.is_some() {
            self.quit_attempts = None;
            self.messagebar.clear();
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

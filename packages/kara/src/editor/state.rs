use super::{
    Size,
    command::{
        Edit,
        EditorCommand::{self, System},
        Move,
        System::{Quit, QuitForce, Resize, Save, SaveAs},
    },
    commandbar::CommandBar,
    editorbuffer::{self, Direction, EditorBuffer},
    messagebar::MessageBar,
    mode::EditorMode,
    statusbar::StatusBar,
    view::View,
};
use crossterm::event::{Event, KeyEvent, KeyEventKind};
const MAX_QUIT_ATTEMPTS: u8 = 2;
#[derive(Default)]
pub(super) struct EditorState {
    pub(super) terminal_size: Size,
    pub(super) mode: EditorMode,
    pub(super) should_quit: bool,
    pub(super) quit_attempts: Option<u8>,
    pub(super) buffer: EditorBuffer,
    pub(super) view: View,
    pub(super) statusbar: StatusBar,
    pub(super) messagebar: MessageBar,
    pub(super) commandbar: Option<CommandBar>,
}

impl EditorState {
    pub(super) fn open_file(&mut self, file_name: &str) {
        match EditorBuffer::open(file_name) {
            Ok(buffer) => self.buffer = buffer,
            Err(e) => {
                let message = format!("ERR: Could not open file `{file_name}` {e}");
                cli_log::record_error(&message);
                self.messagebar.set(message);
            }
        }
    }

    pub(super) fn evaluate_event(&mut self, event: &Event) {
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

    pub(super) fn handle_command(&mut self, command: EditorCommand) {
        match command {
            System(Quit) => self.quit(false),
            System(QuitForce) => self.quit(true),
            System(Resize(size)) => self.resize(size),
            _ => self.reset_quit_attempts(),
        }

        match command {
            System(Quit | QuitForce | Resize(_)) => {}
            System(Save) => self.save(),
            System(SaveAs(filename)) => match self.buffer.save_as(&filename) {
                Ok(outcome) => self.report_save(outcome),
                Err(error) => {
                    cli_log::record_error(&error);
                    self.messagebar
                        .set(format!("ERR: Could not save file: {error}"));
                }
            },
            EditorCommand::Move(direction) => self.handle_move_command(&direction),
            EditorCommand::Edit(edit) => self.handle_edit_command(&edit),
            EditorCommand::ChangeMode(mode) => {
                self.change_mode(mode);
            }
            EditorCommand::Unknown => (),
        }
    }

    pub(super) fn handle_move_command(&mut self, direction: &Move) {
        use super::command::Move::{
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

    pub(super) fn handle_edit_command(&mut self, edit_command: &Edit) {
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
                                Err(e) => {
                                    cli_log::record_error(&e);
                                    self.messagebar.set(e);
                                }
                                Ok(command) => self.handle_command(command),
                            }
                            self.change_mode(EditorMode::View);
                        }
                        edit => commandbar.handle_edit(edit),
                    }
                }
            }
            EditorMode::View => {
                if matches!(edit_command, Edit::Delete) {
                    self.buffer.delete();
                }
            }
        }
    }

    pub(super) const fn resize(&mut self, size: Size) {
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

    pub(super) fn change_mode(&mut self, mode: EditorMode) {
        match mode {
            EditorMode::Command => self.commandbar = Some(CommandBar::new("", self.terminal_size)),
            mode => {
                self.commandbar = None;
                if let Some(direction) = self.mode.change_mode_movement(mode) {
                    self.buffer.move_caret(direction);
                }
            }
        }
        self.mode = mode;
    }

    pub(super) fn save(&mut self) {
        if self.buffer.has_file() {
            match self.buffer.save() {
                Ok(outcome) => self.report_save(outcome),
                Err(e) => {
                    let message = format!("Error writing file! {e}");
                    cli_log::record_error(&message);
                    self.messagebar.set(message);
                }
            }
        } else {
            self.commandbar = Some(CommandBar::new("w ", self.terminal_size));
            self.mode = EditorMode::Command;
        }
    }

    pub(super) fn report_save(&mut self, outcome: editorbuffer::SaveOutcome) {
        let message = match outcome {
            editorbuffer::SaveOutcome::Saved => "File saved successfully.".to_owned(),
            editorbuffer::SaveOutcome::DurabilityWarning(error) => {
                let message = format!("WARNING: File saved, but directory sync failed: {error}");
                cli_log::record_error(&message);
                message
            }
        };
        self.messagebar.set(message);
    }

    pub(super) fn quit(&mut self, force: bool) {
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

    pub(super) fn reset_quit_attempts(&mut self) {
        if self.quit_attempts.is_some() {
            self.quit_attempts = None;
            self.messagebar.clear();
        }
    }
}

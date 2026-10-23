use super::{
    Size,
    command::{
        Edit,
        EditorCommand::{self, System},
        Move,
        System::{Quit, QuitForce, Resize, Save, SaveAs},
    },
    document::{self, Direction, Document},
    message_bar::MessageBar,
    mode::EditorMode,
    prompt::Prompt,
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
    pub(super) buffer: Document,
    pub(super) view: View,
    pub(super) message_bar: MessageBar,
    pub(super) prompt: Option<Prompt>,
    pub(super) search: super::search::Search,
    search_snapshot: Option<super::search::SearchSnapshot>,
}

impl EditorState {
    pub(super) fn open_file(&mut self, file_name: &str) {
        match Document::open(file_name) {
            Ok(buffer) => self.buffer = buffer,
            Err(e) => {
                let message = format!("ERR: Could not open file `{file_name}` {e}");
                cli_log::record_error(&message);
                self.message_bar.set(message);
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
                    self.message_bar
                        .set(format!("ERR: Could not save file: {error}"));
                }
            },
            EditorCommand::Move(direction) => self.handle_move_command(&direction),
            EditorCommand::Edit(edit) => self.handle_edit_command(&edit),
            EditorCommand::ChangeMode(mode) => {
                self.change_mode(mode);
            }
            EditorCommand::StartSearch(direction) => {
                self.change_mode(EditorMode::Search);
                self.search.direction = direction;
                let label = match direction {
                    super::search::SearchDirection::Forward => "/",
                    super::search::SearchDirection::Backward => "?",
                };
                self.prompt = Some(Prompt::new(label, "", self.terminal_size));
            }
            EditorCommand::RepeatSearch { reverse } => self.repeat_search(reverse),
            EditorCommand::ClearSearch => self.search.visible = false,
            EditorCommand::Unknown => (),
        }
    }

    pub(super) fn handle_move_command(&mut self, direction: &Move) {
        use super::command::Move::{
            Down, End, Home, Left, LineEnd, LineStart, PageDown, PageUp, Right, Up,
        };
        if self.prompt.is_some() {
            if self.mode == EditorMode::Search {
                let step = match direction {
                    Down | Right => Some(super::search::SearchDirection::Forward),
                    Up | Left => Some(super::search::SearchDirection::Backward),
                    _ => None,
                };
                if let Some(step) = step {
                    self.search.step(step);
                    self.reveal_search_match();
                }
            }
            return;
        }

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
            EditorMode::Search => {
                if matches!(edit_command, Edit::InsertNewline) {
                    if self.search.query.is_empty()
                        && let Some(snapshot) = &self.search_snapshot
                    {
                        let origin = snapshot.cursor.position;
                        self.search
                            .update(&self.buffer, snapshot.previous.query.clone(), origin);
                        self.search.select(origin, self.search.direction, false);
                        self.reveal_search_match();
                    }
                    self.search_snapshot = None;
                    self.message_bar
                        .set(format!("{} matches", self.search.matches.len()));
                    self.change_mode(EditorMode::View);
                } else if let Some(prompt) = &mut self.prompt {
                    prompt.handle_edit(edit_command);
                    let origin = self.search_snapshot.as_ref().map_or_else(
                        || self.buffer.cursor().position,
                        |snapshot| snapshot.cursor.position,
                    );
                    self.search.update(&self.buffer, prompt.value(), origin);
                    self.reveal_search_match();
                }
            }
            EditorMode::Command => {
                if let Some(prompt) = &mut self.prompt {
                    match edit_command {
                        Edit::InsertNewline => {
                            match prompt.produce_command(self.buffer.filename()) {
                                Err(e) => {
                                    cli_log::record_error(&e);
                                    self.message_bar.set(e);
                                }
                                Ok(command) => self.handle_command(command),
                            }
                            self.change_mode(EditorMode::View);
                        }
                        edit => prompt.handle_edit(edit),
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

    fn repeat_search(&mut self, reverse: bool) {
        if self.search.query.is_empty() {
            return;
        }
        self.search.refresh(&self.buffer);
        let direction = if reverse {
            self.search.direction.reversed()
        } else {
            self.search.direction
        };
        self.search
            .select(self.buffer.cursor().position, direction, false);
        self.search.visible = true;
        self.reveal_search_match();
        if self.search.matches.is_empty() {
            self.message_bar.set("No matches".to_owned());
        }
    }

    fn reveal_search_match(&mut self) {
        if let Some(position) = self.search.current() {
            self.buffer.set_position(position);
            self.view.center(self.buffer.caret_location());
        } else if let Some(snapshot) = &self.search_snapshot {
            self.buffer.restore_cursor(snapshot.cursor.clone());
            self.view.restore(snapshot.scroll_offset);
        }
    }

    pub(super) const fn resize(&mut self, size: Size) {
        self.terminal_size = size;
        let Size { height, width } = size;
        self.view.resize(Size {
            height: height.saturating_sub(2),
            width,
        });
        if let Some(prompt) = &mut self.prompt {
            prompt.resize(Size { height: 1, width });
        }
    }

    pub(super) fn change_mode(&mut self, mode: EditorMode) {
        match mode {
            EditorMode::Search => {
                self.search_snapshot = Some(super::search::SearchSnapshot {
                    cursor: self.buffer.cursor(),
                    scroll_offset: self.view.scroll_offset,
                    previous: std::mem::take(&mut self.search),
                });
                self.prompt = Some(Prompt::new("/", "", self.terminal_size));
            }
            EditorMode::Command => self.prompt = Some(Prompt::new(":", "", self.terminal_size)),
            mode => {
                if let Some(snapshot) = self.search_snapshot.take() {
                    self.buffer.restore_cursor(snapshot.cursor);
                    self.search = snapshot.previous;
                    self.view.restore(snapshot.scroll_offset);
                }
                self.prompt = None;
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
                    self.message_bar.set(message);
                }
            }
        } else {
            self.prompt = Some(Prompt::new(":", "w ", self.terminal_size));
            self.mode = EditorMode::Command;
        }
    }

    pub(super) fn report_save(&mut self, outcome: document::SaveOutcome) {
        let message = match outcome {
            document::SaveOutcome::Saved => "File saved successfully.".to_owned(),
            document::SaveOutcome::DurabilityWarning(error) => {
                let message = format!("WARNING: File saved, but directory sync failed: {error}");
                cli_log::record_error(&message);
                message
            }
        };
        self.message_bar.set(message);
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
            self.message_bar.set(format!(
                "WARNING! File has unsaved changes. Press Ctrl-Q {left_attempts} more times to quit."
            ));
            self.quit_attempts = Some(attempt);
        }
    }

    pub(super) fn reset_quit_attempts(&mut self) {
        if self.quit_attempts.is_some() {
            self.quit_attempts = None;
            self.message_bar.clear();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::editor::{ScreenPosition, TextPosition};

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify exact search viewport restoration."
    )]
    fn search_centers_wraps_and_restores_scroll() -> Result<(), Box<dyn std::error::Error>> {
        let dir = assert_fs::TempDir::new()?;
        let path = dir.path().join("text");
        let text = (0..70)
            .map(|line| {
                if line == 35 || line == 60 {
                    "x\n"
                } else {
                    "long ordinary line\n"
                }
            })
            .collect::<String>();
        std::fs::write(&path, text)?;
        let mut state = EditorState::default();
        state.open_file(path.to_str().ok_or("invalid path")?);
        state.resize(Size {
            width: 12,
            height: 8,
        });
        let origin = TextPosition {
            line: 20,
            grapheme: 10,
        };
        let scroll = ScreenPosition { x: 7, y: 18 };
        state.buffer.set_position(origin);
        state.view.scroll_offset = scroll;
        state.change_mode(EditorMode::Search);
        state.handle_edit_command(&Edit::Insert('x'));
        assert_eq!(state.buffer.cursor().position.line, 35);
        assert_eq!(state.view.scroll_offset.y, 32);
        state.handle_move_command(&Move::Down);
        assert_eq!(state.buffer.cursor().position.line, 60);
        state.handle_move_command(&Move::Right);
        assert_eq!(state.buffer.cursor().position.line, 35);
        state.change_mode(EditorMode::View);
        state.view.scroll_into_view(state.buffer.caret_location());
        assert_eq!(state.buffer.cursor().position, origin);
        assert_eq!(state.view.scroll_offset, scroll);
        Ok(())
    }
}

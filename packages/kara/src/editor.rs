use crossterm::event::read;
use state::EditorState;
use terminal::Terminal;
mod buffer;
mod command;
mod cursor;
mod document;
mod document_status;
mod keymap;
mod line_layout;
mod message_bar;
mod mode;
mod persistence;
mod prelude;
mod prompt;
mod search;
mod state;
mod terminal;
mod ui;
mod view;
use document::Document;
pub use prelude::*;
pub const APP_NAME: &str = env!("CARGO_PKG_NAME");
#[derive(Default)]
pub struct Editor {
    title: String,
    terminal_active: bool,
    state: EditorState,
    renderer: Option<ratatui::Terminal<ratatui::backend::CrosstermBackend<std::io::Stdout>>>,
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
        editor.terminal_active = true;
        editor.renderer = Some(ratatui::Terminal::new(
            ratatui::backend::CrosstermBackend::new(std::io::stdout()),
        )?);

        let size = Terminal::size().unwrap_or_default();
        editor.state.resize(size);
        editor
            .state
            .message_bar
            .set("Ctrl-s = save | Ctrl-q = quit".to_string());

        if let Some(filename) = file_name {
            editor.state.open_file(&filename);
        }

        Ok(editor)
    }

    pub fn run(&mut self) -> std::io::Result<()> {
        loop {
            self.refresh_screen()?;
            // TODO: Do it only if buffer was changed
            self.refresh_title();
            if self.state.should_quit {
                break;
            }

            match read() {
                Ok(event) => self.state.evaluate_event(&event),
                Err(err) => {
                    return Err(err);
                }
            }
        }
        Ok(())
    }

    fn refresh_title(&mut self) {
        let title = self.state.buffer.filename().map_or_else(
            || APP_NAME.to_string(),
            |p| format!("{} - {APP_NAME}", p.display()),
        );

        if title != self.title && matches!(Terminal::set_title(&title), Ok(())) {
            self.title = title;
        }
    }

    fn refresh_screen(&mut self) -> std::io::Result<()> {
        if let Some(renderer) = &mut self.renderer {
            renderer.draw(|frame| ui::render(frame, &mut self.state))?;
            Terminal::show_caret(self.state.mode.get_cursor_style())?;
            Terminal::execute()?;
        }
        Ok(())
    }
}

impl Drop for Editor {
    fn drop(&mut self) {
        if !self.terminal_active {
            return;
        }
        let _ = Terminal::terminate();
        if self.state.should_quit {
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

#[cfg(test)]
mod tests {
    use super::{
        Editor,
        command::{EditorCommand, System},
    };
    use std::{io, process::Command};

    #[test]
    #[ignore = "Runs in a child process with isolated logging configuration"]
    fn handled_errors_child() {
        let session = cli_log::init("kara");
        session.set_stderr_level(log::LevelFilter::Off);
        let mut editor = Editor::default();
        editor.state.open_file("missing-parent/document.txt");
        editor
            .state
            .handle_command(EditorCommand::System(System::SaveAs(
                "missing-parent/saved.txt".to_owned(),
            )));
        editor.state.save();
        let _ = session.finish(0);
    }

    #[test]
    #[allow(
        clippy::panic_in_result_fn,
        reason = "Assertions verify handled errors remain observable without disrupting the editor."
    )]
    fn handled_errors_are_persisted_without_stderr() -> Result<(), Box<dyn std::error::Error>> {
        let dir = assert_fs::TempDir::new()?;
        let output = Command::new(std::env::current_exe()?)
            .current_dir(dir.path())
            .args([
                "--exact",
                "editor::tests::handled_errors_child",
                "--ignored",
            ])
            .env("SANCTUARY_LOG_DIR", dir.path())
            .env("SANCTUARY_LOG_LEVEL", "info")
            .env("SANCTUARY_LOG_STDERR", "debug")
            .output()?;
        assert!(output.status.success());
        assert_eq!(output.stderr, b"");
        let log_file = std::fs::read_dir(dir.path())?
            .filter_map(std::result::Result::ok)
            .map(|entry| entry.path())
            .find(|path| {
                path.file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| {
                        name.starts_with("kara_") && name.ends_with("_rCURRENT.jsonl")
                    })
            })
            .ok_or_else(|| io::Error::other("missing Kara invocation log"))?;
        let records: Vec<serde_json::Value> = std::fs::read_to_string(log_file)?
            .lines()
            .map(serde_json::from_str)
            .collect::<Result<_, _>>()?;
        let errors: Vec<_> = records
            .iter()
            .filter(|record| record["kv"]["event"] == "error")
            .collect();
        assert_eq!(errors.len(), 2);
        let open = errors
            .first()
            .ok_or_else(|| io::Error::other("missing open error"))?;
        assert!(
            open["text"]
                .as_str()
                .is_some_and(|message| message.contains("Could not open file"))
        );
        assert_eq!(
            records.last().map(|record| &record["kv"]["exit_code"]),
            Some(&serde_json::json!(0))
        );
        Ok(())
    }
}

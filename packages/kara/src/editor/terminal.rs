use crossterm::cursor::SetCursorStyle::BlinkingBlock;
use crossterm::cursor::{self, MoveTo, Show};
use crossterm::style::Print;
use crossterm::terminal::{
    self, DisableLineWrap, EnableLineWrap, EnterAlternateScreen, LeaveAlternateScreen, SetTitle,
};
use crossterm::terminal::{Clear, ClearType, disable_raw_mode, enable_raw_mode};
use std::fmt::Display;
use std::io::{Write, stdout};

use crate::editor::{Position, Size};

#[macro_export]
macro_rules! queue {
    ($($command:expr),*) => {{
        use ::std::io::Write;
        use ::std::io::stdout;

        // This allows the macro to take both mut impl Write and &mut impl Write.
        Ok(stdout().by_ref())
            $(.and_then(|writer| crossterm::QueueableCommand::queue(writer, $command)))*
            .map(|_| ())
    }}
}

pub struct Terminal {}

type Result<T> = std::result::Result<T, std::io::Error>;

impl Terminal {
    pub fn initialize() -> Result<()> {
        enable_raw_mode()?;
        let result = (|| {
            Self::enter_alternate_screen()?;
            Self::disable_line_wrap()?;
            Self::clear_screen()?;
            Self::move_to(Position { col: 0, row: 0 })?;
            Self::execute()
        })();
        if result.is_err() {
            let _ = Self::terminate();
        }
        result
    }

    pub fn terminate() -> Result<()> {
        // Attempt every restoration even if stdout or another step fails.
        let results = [
            Self::leave_alternate_screen(),
            Self::enable_line_wrap(),
            Self::show_caret(BlinkingBlock),
            Self::execute(),
            disable_raw_mode(),
        ];
        results.into_iter().collect()
    }

    fn enter_alternate_screen() -> Result<()> {
        queue!(EnterAlternateScreen)
    }

    fn leave_alternate_screen() -> Result<()> {
        queue!(LeaveAlternateScreen)
    }

    fn enable_line_wrap() -> Result<()> {
        queue!(EnableLineWrap)
    }

    fn disable_line_wrap() -> Result<()> {
        queue!(DisableLineWrap)
    }

    pub fn clear_screen() -> Result<()> {
        queue!(Clear(ClearType::All))
    }

    pub fn move_to(position: Position) -> Result<()> {
        let x = u16::try_from(position.col).unwrap_or(u16::MAX);
        let y = u16::try_from(position.row).unwrap_or(u16::MAX);

        queue!(MoveTo(x, y))
    }

    pub fn show_caret(style: cursor::SetCursorStyle) -> Result<()> {
        queue!(Show, style)
    }

    pub fn set_title(title: &str) -> Result<()> {
        queue!(SetTitle(display_text(title)))
    }

    pub fn print(data: impl Display) -> Result<()> {
        queue!(Print(data))
    }

    pub fn size() -> Result<Size> {
        let size = terminal::size()?;
        Ok(Size {
            height: size.1.into(),
            width: size.0.into(),
        })
    }

    pub fn execute() -> Result<()> {
        stdout().flush()?;
        Ok(())
    }
}

pub(super) fn display_text(text: &str) -> String {
    text.chars()
        .map(|character| {
            if character.is_control() {
                character.escape_default().to_string()
            } else {
                character.to_string()
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::display_text;

    #[test]
    fn escapes_terminal_controls_and_preserves_unicode() {
        assert_eq!(display_text("café 東京"), "café 東京");
        assert_eq!(
            display_text("\x1b\x07\r\n\x7f\u{009b}"),
            r"\u{1b}\u{7}\r\n\u{7f}\u{9b}"
        );
    }
}

use crossterm::cursor::SetCursorStyle::BlinkingBlock;
use crossterm::cursor::{Hide, MoveTo, Show};
use crossterm::style::Print;
use crossterm::terminal::{self, EnterAlternateScreen, LeaveAlternateScreen};
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
        Self::enter_alternate_screen()?;
        Self::clear_screen()?;
        Self::move_to(Position { col: 0, row: 0 })?;
        Self::execute()?;
        Ok(())
    }

    pub fn terminate() -> Result<()> {
        Self::leave_alternate_screen()?;
        Self::show_caret()?;
        Self::execute()?;
        disable_raw_mode()?;
        Ok(())
    }

    fn enter_alternate_screen() -> Result<()> {
        queue!(EnterAlternateScreen)
    }

    fn leave_alternate_screen() -> Result<()> {
        queue!(LeaveAlternateScreen)
    }

    pub fn clear_screen() -> Result<()> {
        queue!(Clear(ClearType::All))
    }

    pub fn clear_line() -> Result<()> {
        queue!(Clear(ClearType::CurrentLine))
    }

    pub fn move_to(position: Position) -> Result<()> {
        let x = u16::try_from(position.col).unwrap_or(u16::MAX);
        let y = u16::try_from(position.row).unwrap_or(u16::MAX);

        queue!(MoveTo(x, y))
    }

    pub fn show_caret() -> Result<()> {
        queue!(Show, BlinkingBlock)
    }

    pub fn hide_caret() -> Result<()> {
        queue!(Hide)
    }

    pub fn print(data: impl Display) -> Result<()> {
        queue!(Print(data))
    }

    pub fn print_row(row: usize, line_text: impl Display) -> Result<()> {
        Self::move_to(Position { col: 0, row })?;
        Self::clear_line()?;
        Self::print(line_text)
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

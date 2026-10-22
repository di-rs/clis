//! Sanctuary utilities registered after Brush's defaults, replacing their entries.

use brush_core::builtins::{ContentOptions, ContentType, SimpleCommand};
use brush_core::{Error, ExecutionContext, ExecutionResult, ShellExtensions};

pub struct Status<const SUCCESS: bool>;

impl<const SUCCESS: bool> Status<SUCCESS> {
    const fn exit_status() -> u8 {
        if SUCCESS {
            sanctuary_true::exit_status()
        } else {
            sanctuary_false::exit_status()
        }
    }
}

impl<const SUCCESS: bool> SimpleCommand for Status<SUCCESS> {
    fn get_content(
        name: &str,
        _kind: ContentType,
        _options: &ContentOptions,
    ) -> Result<String, Error> {
        Ok(format!(
            "{name}: Sanctuary builtin; ignore arguments and return {}.\n",
            Self::exit_status()
        ))
    }

    fn execute<SE: ShellExtensions, I: Iterator<Item = S>, S: AsRef<str>>(
        _context: ExecutionContext<'_, SE>,
        _args: I,
    ) -> Result<ExecutionResult, Error> {
        Ok(brush_core::ExecutionExitCode::from(Self::exit_status()).into())
    }
}

use biggie::LineEnding;
use clap::{Args, CommandFactory, FromArgMatches, Parser, Subcommand, ValueEnum};
use std::ops::RangeInclusive;
pub mod bytes;
pub mod fields;
pub mod pair;
pub mod records;
pub mod text;

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum Ending {
    Lf,
    Crlf,
}
impl From<Ending> for LineEnding {
    fn from(value: Ending) -> Self {
        match value {
            Ending::Lf => Self::Lf,
            Ending::Crlf => Self::CrLf,
        }
    }
}
#[derive(Args, Debug)]
pub struct EndingArgs {
    /// Record terminator, independent of the host OS
    #[arg(short = 'e', long, value_enum, default_value = "lf")]
    pub line_ending: Ending,
    /// Omit the final record terminator
    #[arg(short = 'N', long)]
    pub no_final_newline: bool,
}

#[derive(Parser, Debug)]
#[command(author, version, about, propagate_version = true)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<Command>,
    #[command(flatten)]
    pub text: text::TextArgs,
    #[command(flatten)]
    pub logging: cli_log::GlobalLogArgs,
}
/// Validated command and resolved diagnostics; parsing details stay at the edge.
pub struct ParsedCli {
    pub command: Command,
    pub logging: cli_log::LogArgs,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Generate random text (the default command)
    Text(text::TextArgs),
    /// Repeat literal records in a predictable schedule
    Records(records::RecordArgs),
    /// Generate rows with a fixed number of fields
    Fields(fields::FieldArgs),
    /// Generate exact-size random or patterned bytes
    Bytes(bytes::ByteArgs),
    /// Generate sorted streams with exact shared and unique counts
    Pair(pair::PairArgs),
}
impl Cli {
    /// Recover global logging flags even when another argument fails validation.
    pub fn error_log_level() -> Option<log::LevelFilter> {
        let mut command = Self::command();
        command.build();
        let mut args = std::env::args_os().skip(1).peekable();
        let mut logging_args = Vec::new();
        while let Some(arg) = args.next() {
            let Some(arg) = arg.to_str() else {
                continue;
            };
            if arg == "--" {
                break;
            }
            if let Some(value) = arg.strip_prefix("--log-level=") {
                logging_args.extend([
                    std::ffi::OsString::from("--log-level"),
                    std::ffi::OsString::from(value),
                ]);
                continue;
            }
            let value = if arg == "--log-level" {
                args.next_if(|value| !value.to_string_lossy().starts_with('-'))
            } else {
                let Some(shorts) = arg.strip_prefix('-').filter(|arg| !arg.starts_with('-')) else {
                    continue;
                };
                let mut shorts = shorts.chars();
                let mut value = None;
                while let Some(short) = shorts.next() {
                    if short == 'L' {
                        let attached = shorts.as_str().strip_prefix('=').unwrap_or(shorts.as_str());
                        value = if attached.is_empty() {
                            args.next_if(|value| !value.to_string_lossy().starts_with('-'))
                        } else {
                            Some(std::ffi::OsString::from(attached))
                        };
                        break;
                    }
                    if !command.get_arguments().any(|arg| {
                        arg.get_short() == Some(short) && !arg.get_action().takes_values()
                    }) {
                        break;
                    }
                }
                value
            };
            if let Some(value) = value {
                logging_args.extend([std::ffi::OsString::from("--log-level"), value]);
            }
        }
        ErrorLogging::try_parse_from(logging_args)
            .ok()?
            .logging
            .resolve()
            .ok()
            .map(|logging| logging.level())
    }

    pub fn parse_validated() -> Result<ParsedCli, clap::Error> {
        Self::try_parse_validated_from(std::env::args_os())
    }

    fn try_parse_validated_from(
        args: impl IntoIterator<Item = impl Into<std::ffi::OsString> + Clone>,
    ) -> Result<ParsedCli, clap::Error> {
        let mut command = Self::command();
        let matches = command.try_get_matches_from_mut(args)?;
        if matches.subcommand_name().is_some()
            && text::TextArgs::augment_args(clap::Command::new("text"))
                .get_arguments()
                .any(|arg| {
                    matches.value_source(arg.get_id().as_str())
                        == Some(clap::parser::ValueSource::CommandLine)
                })
        {
            return Err(command.error(
                clap::error::ErrorKind::ArgumentConflict,
                "text arguments before a subcommand are not allowed",
            ));
        }
        let cli = Self::from_arg_matches(&matches)?;
        let logging = cli
            .logging
            .resolve()
            .map_err(|error| command.error(clap::error::ErrorKind::ValueValidation, error))?;
        let selected = cli.command.unwrap_or(Command::Text(cli.text));
        let validation = match &selected {
            Command::Text(args) => args.options().validate(),
            Command::Records(args) => args.literal_options().validate(),
            Command::Fields(args) => args.options().validate(),
            Command::Bytes(args) => args.options().validate(),
            Command::Pair(args) => args.validate(),
        };
        if let Err(error) = validation {
            return Err(command.error(clap::error::ErrorKind::ValueValidation, error));
        }
        Ok(ParsedCli {
            command: selected,
            logging,
        })
    }
}

#[derive(Parser)]
#[command(no_binary_name = true, args_override_self = true)]
struct ErrorLogging {
    #[command(flatten)]
    logging: cli_log::GlobalLogArgs,
}
pub fn parse_range(value: &str) -> Result<RangeInclusive<u32>, String> {
    let (start, end) = value.split_once("..=").unwrap_or((value, value));
    let parse = |s: &str| {
        s.parse::<u32>()
            .map_err(|_| "expected N or MIN..=MAX using unsigned 32-bit integers".to_owned())
    };
    let range = parse(start)?..=parse(end)?;
    if range.is_empty() {
        return Err("range minimum must not exceed its maximum".to_owned());
    }
    Ok(range)
}
pub fn parse_word_length(value: &str) -> Result<RangeInclusive<u32>, String> {
    let range = parse_range(value)?;
    if *range.start() == 0 {
        return Err("word length must be positive".to_owned());
    }
    Ok(range)
}

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
    pub logging: cli_tracing::LogArgs,
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
    pub fn parse_validated() -> Self {
        let mut command = Self::command().mut_arg("log_level", |arg| {
            arg.short('L').global(true).env(None::<&str>)
        });
        let matches = command.clone().get_matches();
        if matches.subcommand_name().is_some()
            && command.get_arguments().any(|arg| {
                arg.get_id() != "log_level"
                    && matches.value_source(arg.get_id().as_str())
                        == Some(clap::parser::ValueSource::CommandLine)
            })
        {
            command
                .error(
                    clap::error::ErrorKind::ArgumentConflict,
                    "text arguments before a subcommand are not allowed",
                )
                .exit();
        }
        let mut cli = Self::from_arg_matches(&matches).unwrap_or_else(|err| err.exit());
        // Clap validates a parent's environment value before a later subcommand flag.
        // Resolve the shared environment fallback only after global flag propagation.
        if matches.value_source("log_level") != Some(clap::parser::ValueSource::CommandLine) {
            let logging_matches = cli_tracing::LogArgs::augment_args(clap::Command::new("biggie"))
                .get_matches_from(["biggie"]);
            cli.logging = cli_tracing::LogArgs::from_arg_matches(&logging_matches)
                .unwrap_or_else(|err| err.exit());
        }
        let validation = match &cli.command {
            None => cli.text.options().validate(),
            Some(Command::Text(args)) => args.options().validate(),
            Some(Command::Records(args)) => args.literal_options().validate(),
            Some(Command::Fields(args)) => args.options().validate(),
            Some(Command::Bytes(args)) => args.options().validate(),
            Some(Command::Pair(args)) => args.validate(),
        };
        if let Err(error) = validation {
            command
                .error(clap::error::ErrorKind::ValueValidation, error)
                .exit();
        }
        cli
    }
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

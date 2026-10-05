use biggie::{GenerationOptions, LineEnding};
use clap::{Parser, ValueEnum};
use std::{ops::RangeInclusive, path::PathBuf};

#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum Ending {
    Lf,
    Crlf,
}

#[derive(Parser, Debug)]
#[command(author, version, about)]
pub struct Cli {
    /// Output filename, or - for stdout (use ./- for a file named -)
    #[arg(value_name = "FILE", default_value = "out.txt")]
    pub file: PathBuf,

    /// Number of records; zero produces empty output
    #[arg(short('n'), long, default_value = "100000", value_name = "LINES")]
    pub lines: u64,

    /// Seed for reproducible output with the same build and options
    #[arg(long, value_name = "U64")]
    pub seed: Option<u64>,

    /// Words per record: fixed N or inclusive MIN..=MAX (zero permits blank lines)
    #[arg(long, default_value = "7..=14", value_name = "RANGE", value_parser = parse_range)]
    pub words_per_line: RangeInclusive<u32>,

    /// ASCII bytes per word: positive N or inclusive MIN..=MAX
    #[arg(long, default_value = "2..=11", value_name = "RANGE", value_parser = parse_word_length)]
    pub word_length: RangeInclusive<u32>,

    /// Record terminator
    #[arg(long, value_enum, default_value = "lf")]
    pub line_ending: Ending,

    /// Omit the last record's terminator
    #[arg(long)]
    pub no_final_newline: bool,

    #[command(flatten)]
    pub logging: cli_tracing::LogArgs,
}

impl Cli {
    pub fn generation_options(&self) -> GenerationOptions {
        GenerationOptions {
            lines: self.lines,
            words_per_line: self.words_per_line.clone(),
            word_length: self.word_length.clone(),
            seed: self.seed,
            line_ending: match self.line_ending {
                Ending::Lf => LineEnding::Lf,
                Ending::Crlf => LineEnding::CrLf,
            },
            final_newline: !self.no_final_newline,
        }
    }
}

fn parse_range(value: &str) -> Result<RangeInclusive<u32>, String> {
    let (start, end) = value.split_once("..=").unwrap_or((value, value));
    let parse = |part: &str| {
        part.parse::<u32>()
            .map_err(|_| "expected N or MIN..=MAX using unsigned 32-bit integers".to_owned())
    };
    let range = parse(start)?..=parse(end)?;
    if range.is_empty() {
        return Err("range minimum must not exceed its maximum".to_owned());
    }
    Ok(range)
}

fn parse_word_length(value: &str) -> Result<RangeInclusive<u32>, String> {
    let range = parse_range(value)?;
    if *range.start() == 0 {
        return Err("word length must be positive".to_owned());
    }
    Ok(range)
}

use super::{EndingArgs, parse_range, parse_word_length};
use biggie::{Alphabet, GenerationOptions, LengthUnit, TextOptions};
use clap::{Args, ValueEnum};
use std::{ops::RangeInclusive, path::PathBuf};

#[derive(Clone, Copy, Debug, ValueEnum, Default)]
pub enum AlphabetArg {
    #[default]
    Ascii,
    Unicode,
}
#[derive(Clone, Copy, Debug, ValueEnum, Default)]
pub enum UnitArg {
    #[default]
    Bytes,
    Scalars,
}

#[derive(Args, Debug)]
pub struct TextArgs {
    /// Output filename, or - for stdout (./- names a file)
    #[arg(value_name = "FILE", default_value = "out.txt")]
    pub file: PathBuf,
    /// Number of records, including zero
    #[arg(short = 'n', long, default_value = "100000")]
    pub lines: u64,
    /// Reproducible seed for the same build/platform
    #[arg(short = 's', long)]
    pub seed: Option<u64>,
    /// Words per record: N or MIN..=MAX; zero permits blank records
    #[arg(short='w', long, default_value="7..=14", value_parser=parse_range)]
    pub words_per_line: RangeInclusive<u32>,
    /// Positive units per word: N or MIN..=MAX
    #[arg(short='l', long, default_value="2..=11", value_parser=parse_word_length)]
    pub word_length: RangeInclusive<u32>,
    /// Preset word alphabet
    #[arg(
        short = 'a',
        long,
        value_enum,
        default_value = "ascii",
        conflicts_with = "alphabet_chars"
    )]
    pub alphabet: AlphabetArg,
    /// Literal custom alphabet of distinct scalars
    #[arg(short = 'A', long)]
    pub alphabet_chars: Option<String>,
    /// Non-ASCII alphabets require scalars
    #[arg(short = 'u', long, value_enum, default_value = "bytes")]
    pub length_unit: UnitArg,
    /// Literal separator between words (no CR/LF)
    #[arg(short = 'd', long, default_value = " ")]
    pub word_separator: String,
    #[command(flatten)]
    pub ending: EndingArgs,
}
impl TextArgs {
    pub fn options(&self) -> TextOptions {
        TextOptions {
            generation: GenerationOptions {
                lines: self.lines,
                seed: self.seed,
                words_per_line: self.words_per_line.clone(),
                word_length: self.word_length.clone(),
                line_ending: self.ending.line_ending.into(),
                final_newline: !self.ending.no_final_newline,
            },
            alphabet: self.alphabet_chars.as_ref().map_or_else(
                || match self.alphabet {
                    AlphabetArg::Ascii => Alphabet::Ascii,
                    AlphabetArg::Unicode => Alphabet::Unicode,
                },
                |s| Alphabet::Custom(s.chars().collect()),
            ),
            length_unit: match self.length_unit {
                UnitArg::Bytes => LengthUnit::Bytes,
                UnitArg::Scalars => LengthUnit::Scalars,
            },
            separator: self.word_separator.clone(),
        }
    }
}

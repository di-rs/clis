use super::{EndingArgs, parse_word_length};
use biggie::{FieldFormat, FieldOptions};
use clap::{Args, ValueEnum};
use std::{ops::RangeInclusive, path::PathBuf};
#[derive(Clone, Copy, Debug, ValueEnum)]
pub enum Format {
    Delimited,
    #[cfg(feature = "csv")]
    Csv,
}
#[derive(Args, Debug)]
pub struct FieldArgs {
    /// Output filename, or - for stdout
    #[arg(default_value = "out.txt")]
    pub file: PathBuf,
    /// Logical record count
    #[arg(short = 'n', long, default_value = "100000")]
    pub lines: u64,
    /// Fields per row
    #[arg(short='f',long,default_value="3",value_parser=clap::value_parser!(u32).range(1..))]
    pub fields: u32,
    /// Output encoding
    #[arg(short = 'F', long, value_enum, default_value = "delimited")]
    pub format: Format,
    /// Single ASCII separator byte, default tab
    #[arg(short='d',long,default_value="\t",value_parser=parse_delimiter)]
    pub delimiter: u8,
    /// Literal values, cycled across all cells
    #[arg(short='v',long="field-value",conflicts_with_all=["seed","word_length"])]
    pub values: Vec<String>,
    /// Make every Nth cell empty, counting from one
    #[arg(short='E',long,value_parser=clap::value_parser!(u64).range(1..))]
    pub empty_every: Option<u64>,
    /// Seed for random cells
    #[arg(short = 's', long)]
    pub seed: Option<u64>,
    /// Positive random cell length: N or MIN..=MAX
    #[arg(short='l',long,value_parser=parse_word_length)]
    pub word_length: Option<RangeInclusive<u32>>,
    #[command(flatten)]
    pub ending: EndingArgs,
}
impl FieldArgs {
    pub fn options(&self) -> FieldOptions {
        FieldOptions {
            lines: self.lines,
            fields: self.fields,
            format: match self.format {
                Format::Delimited => FieldFormat::Delimited,
                #[cfg(feature = "csv")]
                Format::Csv => FieldFormat::Csv,
            },
            delimiter: self.delimiter,
            values: self.values.clone(),
            empty_every: self.empty_every,
            seed: self.seed,
            word_length: self.word_length.clone().unwrap_or(2..=11),
            line_ending: self.ending.line_ending.into(),
            final_newline: !self.ending.no_final_newline,
        }
    }
}
fn parse_delimiter(value: &str) -> Result<u8, String> {
    match value.as_bytes() {
        [b] if b.is_ascii() && !matches!(b, 0 | b'\n' | b'\r' | b'"') => Ok(*b),
        _ => Err("expected one ASCII byte other than NUL, CR, LF or quote".into()),
    }
}

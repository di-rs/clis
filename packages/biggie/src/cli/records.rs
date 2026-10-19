use super::EndingArgs;
use anyhow::{Context, Result, ensure};
use biggie::RecordOptions;
use clap::Args;
use std::{fs::File, io::Read, path::PathBuf};
#[derive(Args, Debug)]
pub struct RecordArgs {
    /// Output filename, or - for stdout
    #[arg(default_value = "out.txt")]
    pub file: PathBuf,
    /// Literal record, repeatable; no embedded LF
    #[arg(short = 'r', long = "record", conflicts_with = "records_file")]
    pub records: Vec<String>,
    /// UTF-8 LF-delimited corpus (at most 8 MiB)
    #[arg(short = 'f', long)]
    pub records_file: Option<PathBuf>,
    /// Copies of each adjacent record
    #[arg(short='p',long,default_value="1",value_parser=clap::value_parser!(u64).range(1..))]
    pub repeat: u64,
    /// Number of full schedule cycles
    #[arg(short = 'c', long, default_value = "1")]
    pub cycles: u64,
    #[command(flatten)]
    pub ending: EndingArgs,
}
impl RecordArgs {
    pub fn literal_options(&self) -> RecordOptions {
        RecordOptions {
            records: self.records.clone(),
            repeat: self.repeat,
            cycles: self.cycles,
            line_ending: self.ending.line_ending.into(),
            final_newline: !self.ending.no_final_newline,
        }
    }
    pub fn options(&self) -> Result<RecordOptions> {
        let mut options = self.literal_options();
        if let Some(path) = &self.records_file {
            let mut bytes = Vec::new();
            File::open(path)
                .with_context(|| format!("Cannot open corpus {}", path.display()))?
                .take(8 * 1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .context("Cannot read corpus")?;
            ensure!(bytes.len() <= 8 * 1024 * 1024, "corpus exceeds 8 MiB");
            let text = String::from_utf8(bytes).context("corpus must be UTF-8")?;
            options.records = text
                .split_terminator('\n')
                .take(100_001)
                .map(str::to_owned)
                .collect();
        }
        options.validate()?;
        Ok(options)
    }
}
